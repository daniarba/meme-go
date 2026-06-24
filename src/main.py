"""
main.py — Bot ka entry point. Yahan se sab kuch chalta hai:

1. Discord bot start hota hai (control + alerts)
2. Naye token discovery - Pump.fun, Raydium LaunchLab, Raydium CPMM, Moonshot
3. Copy-trade wallets ke liye watcher
4. Open positions ke liye exit-monitor loop (stop loss / take profit / trailing)

Run: python -m src.main  (project root se)
"""
import asyncio
import logging

from . import chain_monitor, database as db, dev_wallet_tracker, executor, risk_manager, safety_filter, solana_rpc, stats
from .config import Config
from .discord_bot import bot, bot_state, send_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("main")

POSITION_CHECK_INTERVAL_SEC = 15
SOL_MINT = Config.SOL_MINT

# Dedupe safety-net: same mint thodi der mein dobara aaye (kisi bhi wajah se -
# duplicate WS message, ya kisi source ki extraction confused ho) to dobara
# process na karein. 120 sec TTL kaafi hai is use-case ke liye.
_RECENTLY_SEEN_TTL_SEC = 120
_recently_seen_mints: dict = {}


def _already_processing(mint: str) -> bool:
    now = asyncio.get_event_loop().time()
    # purani entries cleanup karo
    expired = [m for m, t in _recently_seen_mints.items() if now - t > _RECENTLY_SEEN_TTL_SEC]
    for m in expired:
        del _recently_seen_mints[m]

    if mint in _recently_seen_mints:
        return True
    _recently_seen_mints[mint] = now
    return False


async def get_risk_manager() -> risk_manager.RiskManager:
    # DRY_RUN (paper trading) mein hamesha virtual balance use karo - real wallet
    # balance pe kabhi depend na karo. Virtual balance ab DB mein stored hai
    # (Discord `!setbalance` se change ho sakta hai), Config sirf first-time default hai.
    if not Config.DRY_RUN and Config.SOLANA_PRIVATE_KEY:
        from .executor import load_wallet
        wallet = load_wallet()
        balance = await solana_rpc.get_wallet_sol_balance(str(wallet.pubkey())) if wallet else db.get_virtual_balance_sol()
    else:
        balance = db.get_virtual_balance_sol()
    return risk_manager.RiskManager(balance)


async def handle_new_token_event(event: dict):
    source = event.get("source", "unknown")
    stats.bump("ws_event_received")

    if bot_state["paused"]:
        stats.bump("skipped_paused")
        return

    signature = event.get("signature")
    if not signature:
        return

    tx = await solana_rpc.get_transaction(signature)
    if tx is None:
        stats.bump("tx_fetch_failed")
        logger.info(f"[{source}] tx fetch failed for {signature}")
        return

    mint = solana_rpc.extract_new_mint(tx)
    if not mint:
        stats.bump("mint_extract_failed")
        logger.info(f"[{source}] mint extraction failed for {signature}")
        return

    if _already_processing(mint):
        stats.bump("duplicate_mint_skipped")
        logger.info(f"[{source}] duplicate mint {mint} - pehle hi process ho raha hai/hua hai, skip.")
        return

    deployer = solana_rpc.extract_fee_payer(tx)
    logger.info(f"[{source}] New token detected: {mint} (deployer: {deployer})")
    stats.bump("new_token_detected")

    # RugCheck/GoPlus ko brand-new mint index karne mein time lagta hai - thoda wait karo,
    # warna hamesha "no data available" milega aur har token automatically skip ho jayega.
    if Config.NEW_TOKEN_EVAL_DELAY_SEC > 0:
        await asyncio.sleep(Config.NEW_TOKEN_EVAL_DELAY_SEC)

    await _evaluate_and_maybe_buy(mint, source=source, deployer=deployer)


async def handle_wallet_event(event: dict):
    stats.bump("copy_trade_event_received")
    if bot_state["paused"]:
        return

    signature = event.get("signature")
    if not signature:
        return

    tx = await solana_rpc.get_transaction(signature)
    if tx is None:
        stats.bump("tx_fetch_failed")
        return

    mint = solana_rpc.extract_swap_token_from_tx(tx, exclude_mints={SOL_MINT})
    if not mint:
        return

    logger.info(f"Copy-trade target wallet swapped into: {mint}")
    await _evaluate_and_maybe_buy(mint, source="copy_trade")


async def _evaluate_and_maybe_buy(mint: str, source: str, deployer: str | None = None):
    stats.bump("evaluated")

    # Safety filter aur dev-wallet history check parallel chalao - time bachane ke liye
    safety_task = safety_filter.evaluate_token(mint)
    dev_task = dev_wallet_tracker.evaluate_dev_wallet(deployer) if deployer else None

    if dev_task:
        safety, dev_check = await asyncio.gather(safety_task, dev_task)
    else:
        safety = await safety_task
        dev_check = {"risk": "unknown", "reason": "deployer address nahi mila"}

    if not safety["safe"]:
        stats.bump("skipped_safety")
        logger.info(f"Skipping {mint} — safety score {safety['score']} ({safety['reasons']})")
        await send_alert(f"🚫 Skip `{mint[:8]}…` ({source}) — safety score {safety['score']}: {', '.join(safety['reasons']) or 'low score'}")
        return

    if dev_check["risk"] == "high":
        stats.bump("skipped_devwallet")
        logger.info(f"Skipping {mint} — deployer {deployer} flagged high risk ({dev_check['reason']})")
        await send_alert(
            f"🚫 Skip `{mint[:8]}…` ({source}) — Dev wallet `{(deployer or '')[:8]}…` high risk: {dev_check['reason']}"
        )
        return

    rm = await get_risk_manager()
    can_open, reason = rm.can_open_new_position()
    if not can_open:
        stats.bump("skipped_risk")
        logger.info(f"Risk manager blocked trade: {reason}")
        await send_alert(f"⚠️ Trade blocked by risk manager: {reason}")
        return

    sol_amount = rm.position_size_sol()
    result = await executor.buy_token(mint, sol_amount)

    if not result["success"]:
        stats.bump("buy_failed")
        await send_alert(f"❌ Buy failed for `{mint[:8]}…` ({source})")
        return

    stats.bump("bought")
    entry_price = sol_amount / (result["out_amount"] / 1_000_000) if result["out_amount"] else 0
    stop_loss = rm.compute_stop_loss(entry_price) if entry_price else 0
    take_profit = rm.compute_take_profit(entry_price) if entry_price else 0

    position_id = db.open_position(
        token_mint=mint, token_symbol=mint[:6], entry_price_sol=entry_price,
        amount_tokens=result["out_amount"] or 0, sol_spent=sol_amount,
        stop_loss_price=stop_loss, take_profit_price=take_profit, source=source,
    )
    db.log_trade(mint, mint[:6], "buy", entry_price, result["out_amount"] or 0,
                 sol_amount, 0, result["signature"], result["dry_run"])

    tag = "[DRY RUN] " if result["dry_run"] else ""
    dev_note = f" | dev: {dev_check['risk']}" if deployer else ""
    await send_alert(
        f"{tag}✅ Bought `{mint[:8]}…` ({source}) | {sol_amount} SOL | "
        f"safety {safety['score']}{dev_note} | SL {stop_loss:.8f} | TP {take_profit:.8f}"
    )
    logger.info(f"Position #{position_id} opened for {mint}")


async def position_monitor_loop():
    """Har 15 sec mein open positions ke liye current price check karke exit-decision leta hai."""
    rm = await get_risk_manager()
    while True:
        try:
            positions = db.get_open_positions()
            for pos in positions:
                price = await solana_rpc.get_token_price_in_sol(pos["token_mint"])
                if price is None:
                    continue

                should_exit, reason = rm.check_exit_conditions(pos, price)
                if not should_exit:
                    continue

                result = await executor.sell_token(pos["token_mint"], int(pos["amount_tokens"]))
                if not result["success"]:
                    logger.warning(f"Sell failed for position #{pos['id']}")
                    continue

                proceeds_sol = (result["out_amount"] or 0) / 1_000_000_000
                pnl = proceeds_sol - pos["sol_spent"]
                db.close_position(pos["id"], pnl)
                db.log_trade(pos["token_mint"], pos["token_symbol"], "sell", price,
                             pos["amount_tokens"], proceeds_sol, pnl, result["signature"], result["dry_run"])

                tag = "[DRY RUN] " if result["dry_run"] else ""
                emoji = "🟢" if pnl >= 0 else "🔴"
                await send_alert(
                    f"{tag}{emoji} Closed `{pos['token_symbol']}` — {reason} | PnL: {pnl:+.4f} SOL"
                )
        except Exception as e:
            logger.error(f"Position monitor error: {e}")

        await asyncio.sleep(POSITION_CHECK_INTERVAL_SEC)


async def _delayed_start(coro_func, delay: float, *args):
    """WS connections ko thode gap se start karta hai - sab ek sath connect honge to
    Helius shuru mein hi 429 de deta hai (jaisa logs mein dikha)."""
    if delay > 0:
        await asyncio.sleep(delay)
    await coro_func(*args)


def _build_discovery_tasks():
    """Config ke ENABLE_* flags ke hisab se discovery watchers start karta hai (staggered)."""
    sources = []
    if Config.ENABLE_PUMPFUN_WATCHER:
        sources.append("pumpfun")
    if Config.ENABLE_RAYDIUM_LAUNCHLAB_WATCHER:
        sources.append("raydium_launchlab")
    if Config.ENABLE_RAYDIUM_CPMM_WATCHER:
        sources.append("raydium_cpmm")
    if Config.ENABLE_MOONSHOT_WATCHER:
        sources.append("moonshot")

    tasks = []
    for i, source_name in enumerate(sources):
        delay = i * 3  # har connection ke beech 3 sec gap
        tasks.append(_delayed_start(chain_monitor.watch_new_token_source, delay, source_name, handle_new_token_event))
    return tasks


async def run_bot():
    problems = Config.validate()
    for p in problems:
        logger.warning(f"CONFIG WARNING: {p}")

    db.init_db()

    tasks = [
        bot.start(Config.DISCORD_BOT_TOKEN),
        position_monitor_loop(),
        *_build_discovery_tasks(),
    ]
    if Config.COPY_TRADE_WALLETS:
        tasks.append(chain_monitor.watch_copy_trade_wallets(handle_wallet_event))

    await asyncio.gather(*tasks)


def main():
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")


if __name__ == "__main__":
    main()
