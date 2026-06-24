"""
chain_monitor.py — Helius WebSocket (logsSubscribe) se real-time data, MULTIPLE sources se:

1. Pump.fun naye token launches
2. Raydium LaunchLab naye bonding-curve launches
3. Raydium CPMM naye direct pools (community pools + migrations)
4. Moonshot naye token launches
5. Copy-trade target wallets ki activity

Yeh module sirf "events" produce karta hai - actual decision (buy/skip) main.py mein hota hai.

DEBUG NOTE: Har source ke liye humne instruction-name keywords guess kiye hain (Anchor logs
se, jaise "Instruction: Create"). Agar koi source bilkul signal nahi de raha lekin events_seen
counter badh raha hai (matlab WS messages aa rahe hain), to iska matlab keyword match nahi ho raha -
.env mein DEBUG_LOG_UNMATCHED=true karke raw logs dekho aur keyword adjust karo.
"""
import asyncio
import json
import logging

import websockets

from . import stats
from .config import Config

logger = logging.getLogger("chain_monitor")


def _fire_and_log(coro):
    """asyncio.create_task() jaisa hi, lekin agar task mein exception aaye to
    woh silently GC hone ka wait nahi karta - turant log ho jata hai."""
    task = asyncio.create_task(coro)

    def _on_done(t: asyncio.Task):
        exc = t.exception() if not t.cancelled() else None
        if exc:
            logger.error(f"Background task failed: {exc}", exc_info=exc)

    task.add_done_callback(_on_done)
    return task

# Source name -> (program_id, [instruction keywords - case-insensitive substring match])
SOURCES = {
    "pumpfun": (Config.PUMPFUN_PROGRAM_ID, ["Instruction: Create"]),
    "raydium_launchlab": (Config.RAYDIUM_LAUNCHLAB_PROGRAM_ID, ["Instruction: Initialize"]),
    "raydium_cpmm": (Config.RAYDIUM_CPMM_PROGRAM_ID, ["Instruction: CreatePool", "Instruction: Initialize"]),
    "moonshot": (Config.MOONSHOT_PROGRAM_ID, ["Instruction: TokenMint", "TokenMint"]),
}


async def _subscribe_logs(ws, mention_address: str, sub_id: int):
    request = {
        "jsonrpc": "2.0",
        "id": sub_id,
        "method": "logsSubscribe",
        "params": [
            {"mentions": [mention_address]},
            {"commitment": "confirmed"},
        ],
    }
    await ws.send(json.dumps(request))


async def watch_new_token_source(source_name: str, on_event):
    """
    Generic watcher - kisi bhi program ke 'naya token create' instruction ko detect karta hai.
    Pehli 5 unmatched messages ke raw logs INFO level pe print karta hai (debug ke liye),
    taake agar keyword guess galat ho to terminal/Railway logs se pata chal jaye.
    """
    program_id, keywords = SOURCES[source_name]
    unmatched_logged = 0

    while True:
        try:
            async with websockets.connect(Config.HELIUS_WS_URL, ping_interval=20) as ws:
                await _subscribe_logs(ws, program_id, sub_id=1)
                logger.info(f"[{source_name}] subscription active on {program_id}")

                async for message in ws:
                    data = json.loads(message)
                    result = data.get("params", {}).get("result", {})
                    value = result.get("value", {})
                    logs = value.get("logs", [])
                    signature = value.get("signature")

                    if not logs:
                        continue

                    stats.bump_source(source_name)

                    matched = any(
                        any(kw.lower() in log.lower() for kw in keywords) for log in logs
                    )
                    if matched:
                        stats.bump(f"{source_name}_matched")
                        # IMPORTANT: create_task use karte hain, await nahi - warna on_event()
                        # (jo getTransaction + safety checks + 20s delay karta hai) WS read loop
                        # ko block kar dega aur agle events backlog mein phas jayenge.
                        _fire_and_log(on_event({
                            "type": "new_token",
                            "source": source_name,
                            "signature": signature,
                            "logs": logs,
                        }))
                    elif unmatched_logged < 5 and Config.DEBUG_LOG_UNMATCHED:
                        unmatched_logged += 1
                        logger.info(f"[{source_name}] unmatched logs sample #{unmatched_logged}: {logs}")
        except Exception as e:
            logger.warning(f"[{source_name}] watcher disconnected: {e}. 5s mein reconnect karenge.")
            await asyncio.sleep(5)


async def watch_copy_trade_wallets(on_event):
    """
    COPY_TRADE_WALLETS mein diye gaye addresses ki transactions monitor karta hai.
    Jab target wallet koi swap karti hai, on_event() call hota hai taake hum mirror kar sakein.
    """
    wallets = Config.COPY_TRADE_WALLETS
    if not wallets:
        logger.info("COPY_TRADE_WALLETS empty hai — copy-trade watcher skip ho raha hai.")
        return

    while True:
        try:
            async with websockets.connect(Config.HELIUS_WS_URL, ping_interval=20) as ws:
                for idx, wallet in enumerate(wallets, start=100):
                    await _subscribe_logs(ws, wallet, sub_id=idx)
                logger.info(f"Copy-trade watching {len(wallets)} wallets.")

                async for message in ws:
                    data = json.loads(message)
                    result = data.get("params", {}).get("result", {})
                    value = result.get("value", {})
                    signature = value.get("signature")
                    logs = value.get("logs", [])

                    stats.bump_source("copy_trade")
                    _fire_and_log(on_event({
                        "type": "wallet_activity",
                        "signature": signature,
                        "logs": logs,
                    }))
        except Exception as e:
            logger.warning(f"Copy-trade watcher disconnected: {e}. 5s mein reconnect karenge.")
            await asyncio.sleep(5)
