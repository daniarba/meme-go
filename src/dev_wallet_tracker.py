"""
dev_wallet_tracker.py — Jab koi naya token Pump.fun pe launch ho, uska DEPLOYER
wallet check karte hain: is wallet ne pehle kitne tokens banaye, aur unme se
kitne "rugged/dead" ho gaye (liquidity zero, abandoned).

Logic (paid plugins jaisa, lekin apna):
1. Deployer wallet ki recent transaction history nikalo
2. Usme se Pump.fun "Create" instructions filter karo -> purane tokens ki list mile
3. Har purane token ka current liquidity status check karo (DexScreener)
4. Agar liquidity bohot kam hai aur token kaafi purana hai -> "rugged" maano
5. rug_ratio = rugged / total -> threshold se zyada ho to naya token bhi SKIP karo
"""
import logging
import time

import aiohttp

from . import database as db
from .config import Config
from . import solana_rpc

logger = logging.getLogger("dev_wallet_tracker")


async def _is_token_dead(session, mint: str, created_at_unix: float | None) -> bool | None:
    """
    DexScreener se current liquidity check karta hai.
    Returns: True (dead/rugged), False (alive), None (pata nahi chal saka)
    """
    # bohot naya token hai to "dead" judge karna unfair hoga - usko skip (unknown) karo
    if created_at_unix and (time.time() - created_at_unix) < Config.DEAD_TOKEN_MIN_AGE_HOURS * 3600:
        return None

    url = Config.DEXSCREENER_TOKEN_URL.format(mint=mint)
    try:
        async with session.get(url, timeout=8) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception as e:
        logger.warning(f"DexScreener fetch failed for {mint}: {e}")
        return None

    pairs = data.get("pairs") or []
    if not pairs:
        return True  # koi trading pair hi nahi bacha - rugged/abandoned

    max_liquidity = max((p.get("liquidity", {}).get("usd") or 0) for p in pairs)
    return max_liquidity < Config.DEAD_TOKEN_LIQUIDITY_USD


async def _find_previous_tokens_by_wallet(wallet: str) -> list:
    """
    Wallet ki history mein Pump.fun 'Create' transactions dhundta hai.
    Returns list of (mint, created_at_unix).
    """
    sigs_info = await solana_rpc.get_signatures_for_address(wallet, Config.DEV_WALLET_HISTORY_LIMIT)
    if not sigs_info:
        return []

    signatures = [s["signature"] for s in sigs_info if not s.get("err")]
    block_times = {s["signature"]: s.get("blockTime") for s in sigs_info}

    txs = await solana_rpc.get_transactions_batch(signatures)

    previous_tokens = []
    for sig, tx in zip(signatures, txs):
        if not tx:
            continue
        logs = (tx.get("meta") or {}).get("logMessages", [])
        is_create = any("Instruction: Create" in log for log in logs)
        mentions_pumpfun = any(Config.PUMPFUN_PROGRAM_ID in log for log in logs)
        if is_create and mentions_pumpfun:
            mint = solana_rpc.extract_new_mint_from_pumpfun_tx(tx)
            if mint:
                previous_tokens.append((mint, block_times.get(sig)))

    return previous_tokens


async def evaluate_dev_wallet(wallet: str) -> dict:
    """
    Main entry point. Returns:
    {
        "risk": "high" | "medium" | "low" | "unknown",
        "total_created": int,
        "rugged_count": int,
        "rug_ratio": float,
        "reason": str
    }
    """
    cached = db.get_cached_dev_wallet(wallet, Config.DEV_WALLET_CACHE_TTL_SEC)
    if cached:
        total = cached["total_created"]
        rugged = cached["rugged_count"]
        return _build_verdict(total, rugged, from_cache=True)

    try:
        previous_tokens = await _find_previous_tokens_by_wallet(wallet)
    except Exception as e:
        logger.warning(f"Dev wallet history fetch failed for {wallet}: {e}")
        return {"risk": "unknown", "total_created": 0, "rugged_count": 0,
                "rug_ratio": 0.0, "reason": "history_fetch_failed"}

    if not previous_tokens:
        db.save_dev_wallet_cache(wallet, 0, 0)
        return _build_verdict(0, 0, from_cache=False)

    rugged_count = 0
    async with aiohttp.ClientSession() as session:
        for mint, created_at in previous_tokens:
            is_dead = await _is_token_dead(session, mint, created_at)
            if is_dead is True:
                rugged_count += 1

    total = len(previous_tokens)
    db.save_dev_wallet_cache(wallet, total, rugged_count)
    return _build_verdict(total, rugged_count, from_cache=False)


def _build_verdict(total: int, rugged: int, from_cache: bool) -> dict:
    ratio = (rugged / total) if total else 0.0

    if total < Config.DEV_WALLET_MIN_HISTORY_TO_JUDGE:
        risk = "unknown"
        reason = f"sirf {total} purane tokens mile — judge karne ke liye kaafi data nahi"
    elif ratio >= Config.DEV_WALLET_MAX_RUG_RATIO:
        risk = "high"
        reason = f"{rugged}/{total} purane tokens rugged/dead nikle ({ratio*100:.0f}%)"
    elif ratio > 0:
        risk = "medium"
        reason = f"{rugged}/{total} purane tokens rugged/dead ({ratio*100:.0f}%) — threshold se kam"
    else:
        risk = "low"
        reason = f"{total} purane tokens, koi rugged nahi mila"

    return {
        "risk": risk,
        "total_created": total,
        "rugged_count": rugged,
        "rug_ratio": round(ratio, 2),
        "reason": reason + (" (cached)" if from_cache else ""),
    }
