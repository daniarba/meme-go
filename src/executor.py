"""
executor.py — Jupiter Aggregator API se actual buy/sell execution.
DRY_RUN=true ho to sirf simulate karta hai, real transaction nahi bhejta.

IMPORTANT: Yeh module real funds move karta hai jab DRY_RUN=false ho.
Pehle hamesha DRY_RUN=true pe test karo.
"""
import base64
import logging

import aiohttp
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from .config import Config

logger = logging.getLogger("executor")


def load_wallet() -> Keypair | None:
    if not Config.SOLANA_PRIVATE_KEY:
        return None
    return Keypair.from_base58_string(Config.SOLANA_PRIVATE_KEY)


async def get_quote(session, input_mint: str, output_mint: str, amount: int, slippage_bps: int):
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "slippageBps": slippage_bps,
    }
    # BUG FIX: pehle yahan try/except nahi tha - agar Jupiter se connection error
    # aaye (timeout, DNS, network blip), poora background task crash ho jata tha
    # (uncaught exception), aur token "Bought" ya "failed" alert kuch nahi milta tha,
    # bas silently rah jata tha. Ab connection errors bhi gracefully handle hote hain.
    for attempt in range(2):
        try:
            async with session.get(Config.JUPITER_QUOTE_URL, params=params, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Jupiter quote failed: {resp.status} {await resp.text()}")
                    return None
                return await resp.json()
        except Exception as e:
            logger.warning(f"Jupiter quote connection error (attempt {attempt + 1}/2): {e}")
    logger.error(f"Jupiter quote failed after retries for {input_mint} -> {output_mint}")
    return None


async def _build_swap_transaction(session, quote: dict, wallet_pubkey: str):
    payload = {
        "quoteResponse": quote,
        "userPublicKey": wallet_pubkey,
        "wrapAndUnwrapSol": True,
        "prioritizationFeeLamports": Config.PRIORITY_FEE_LAMPORTS,
    }
    for attempt in range(2):
        try:
            async with session.post(Config.JUPITER_SWAP_URL, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Jupiter swap build failed: {resp.status} {await resp.text()}")
                    return None
                return await resp.json()
        except Exception as e:
            logger.warning(f"Jupiter swap-build connection error (attempt {attempt + 1}/2): {e}")
    logger.error("Jupiter swap build failed after retries")
    return None


async def execute_swap(input_mint: str, output_mint: str, amount_lamports: int,
                        rpc_client=None) -> dict:
    """
    Generic swap executor. amount_lamports = input token ki smallest-unit amount.
    Returns: {"success": bool, "signature": str|None, "out_amount": int|None, "dry_run": bool}
    """
    if Config.DRY_RUN:
        logger.info(f"[DRY_RUN] Swap simulate: {amount_lamports} of {input_mint} -> {output_mint}")
        async with aiohttp.ClientSession() as session:
            quote = await get_quote(session, input_mint, output_mint, amount_lamports, Config.SLIPPAGE_BPS)
        if not quote:
            return {"success": False, "signature": None, "out_amount": None, "dry_run": True}
        return {
            "success": True,
            "signature": "DRY_RUN_NO_TX",
            "out_amount": int(quote.get("outAmount", 0)),
            "dry_run": True,
        }

    wallet = load_wallet()
    if wallet is None:
        logger.error("DRY_RUN=false hai lekin SOLANA_PRIVATE_KEY missing — trade abort.")
        return {"success": False, "signature": None, "out_amount": None, "dry_run": False}

    async with aiohttp.ClientSession() as session:
        quote = await get_quote(session, input_mint, output_mint, amount_lamports, Config.SLIPPAGE_BPS)
        if not quote:
            return {"success": False, "signature": None, "out_amount": None, "dry_run": False}

        swap_data = await _build_swap_transaction(session, quote, str(wallet.pubkey()))
        if not swap_data or "swapTransaction" not in swap_data:
            return {"success": False, "signature": None, "out_amount": None, "dry_run": False}

    raw_tx = base64.b64decode(swap_data["swapTransaction"])
    tx = VersionedTransaction.from_bytes(raw_tx)
    signed_tx = VersionedTransaction(tx.message, [wallet])

    if rpc_client is None:
        logger.error("RPC client provide nahi hua — transaction bhej nahi sakte.")
        return {"success": False, "signature": None, "out_amount": None, "dry_run": False}

    try:
        sig = await rpc_client.send_raw_transaction(bytes(signed_tx))
        logger.info(f"Swap submitted: {sig}")
        return {
            "success": True,
            "signature": str(sig),
            "out_amount": int(quote.get("outAmount", 0)),
            "dry_run": False,
        }
    except Exception as e:
        logger.error(f"Swap submission failed: {e}")
        return {"success": False, "signature": None, "out_amount": None, "dry_run": False}


async def buy_token(token_mint: str, sol_amount: float, rpc_client=None) -> dict:
    lamports = int(sol_amount * 1_000_000_000)
    return await execute_swap(Config.SOL_MINT, token_mint, lamports, rpc_client)


async def sell_token(token_mint: str, token_amount_raw: int, rpc_client=None) -> dict:
    return await execute_swap(token_mint, Config.SOL_MINT, token_amount_raw, rpc_client)
