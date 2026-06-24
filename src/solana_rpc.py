"""
solana_rpc.py — Helius RPC se transaction details, wallet balance, aur
Jupiter quote se token price nikalne ke helper functions.

RATE LIMITING: Helius free tier ki request-per-second limit kam hoti hai. Jab
4 sources (Pump.fun + Raydium x2 + Moonshot) parallel chal rahe hon, har naya
token detect hote hi getTransaction call hoti hai - free tier pe yeh easily
429 (rate limited) de deta hai. Isliye:
1. Saari RPC calls ek shared semaphore se guarded hain (max concurrent calls)
2. 429 milne pe automatic retry with exponential backoff
3. Har failure ka asli reason (status code + body) log hota hai - "tx fetch failed"
   jaisa generic message ab nahi aata, exact wajah pata chalti hai
"""
import asyncio
import logging

import aiohttp

from .config import Config

logger = logging.getLogger("solana_rpc")

# Helius free/starter tier ki RPS limit ke hisab se conservative rakha hai.
# Agar phir bhi 429 aaye, .env mein RPC_MAX_CONCURRENT kam karo ya Helius plan upgrade karo.
_RPC_SEMAPHORE = asyncio.Semaphore(Config.RPC_MAX_CONCURRENT)


class _RateLimiter:
    """
    Concurrency-semaphore kaafi nahi tha - 4 requests ek sath fire ho ke turant
    complete ho sakti hain (burst), jo phir bhi free-tier RPS limit cross kar deta hai.
    Yeh class requests ko minimum interval se space karta hai (jaise traffic signal),
    taake actual request-rate bhi control mein rahe, sirf concurrent count nahi.
    """
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait(self):
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            delay = self._next_allowed - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = loop.time()
            self._next_allowed = now + self.min_interval


_rate_limiter = _RateLimiter(Config.RPC_MIN_INTERVAL_SEC)

_session: aiohttp.ClientSession | None = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def _rpc_call(method: str, params: list, timeout: int = 10, retries: int = 4):
    """
    Generic Helius JSON-RPC caller — rate-limit (429) pe retry karta hai,
    aur har tarah ki failure ko clearly log karta hai (silent None nahi deta).
    """
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    backoff = 1.0

    async with _RPC_SEMAPHORE:
        session = await _get_session()
        for attempt in range(1, retries + 1):
            await _rate_limiter.wait()
            try:
                async with session.post(Config.HELIUS_RPC_URL, json=payload, timeout=timeout) as resp:
                    if resp.status == 429:
                        body = await resp.text()
                        logger.warning(
                            f"{method}: rate-limited (429), attempt {attempt}/{retries}, "
                            f"backing off {backoff:.1f}s. Body: {body[:150]}"
                        )
                        await asyncio.sleep(backoff)
                        backoff *= 2
                        continue

                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"{method}: HTTP {resp.status} — {body[:200]}")
                        return None

                    data = await resp.json()
                    if "error" in data:
                        logger.warning(f"{method}: RPC error — {data['error']}")
                        return None
                    result = data.get("result")
                    if result is None:
                        logger.info(
                            f"{method}: result is null (signature/account abhi mainnet pe "
                            f"available nahi hua, ya wrong commitment level)."
                        )
                    return result

            except asyncio.TimeoutError:
                logger.warning(f"{method}: timeout on attempt {attempt}/{retries}")
                await asyncio.sleep(backoff)
                backoff *= 2
            except Exception as e:
                logger.warning(f"{method}: exception on attempt {attempt}/{retries} — {e}")
                await asyncio.sleep(backoff)
                backoff *= 2

    logger.error(f"{method}: failed after {retries} retries — likely persistent rate-limit. "
                 f"Helius plan upgrade ya RPC_MAX_CONCURRENT kam karna pad sakta hai.")
    return None


async def get_transaction(signature: str) -> dict | None:
    # IMPORTANT: commitment "confirmed" hona chahiye, "finalized" (default) nahi -
    # humne logsSubscribe bhi "confirmed" pe kiya hai. Finalized hone mein ~15-20s
    # lagte hain; itna wait karenge to getTransaction hamesha null (result: None)
    # dega bina kisi error ke - exactly jo "tx fetch failed" mein dikh raha tha.
    params = [signature, {
        "maxSupportedTransactionVersion": 0,
        "encoding": "jsonParsed",
        "commitment": "confirmed",
    }]

    result = await _rpc_call("getTransaction", params, timeout=10)
    if result is not None:
        return result

    # Tiny race-condition safety: logsSubscribe notification aur RPC node ke
    # internal state ke beech kabhi 1-2 sec ka lag ho sakta hai. Do chhote
    # retries (1s gap) kaafi hote hain - agar phir bhi null aaye to genuinely
    # kuch aur masla hai (jo ab logs mein dikh jayega).
    for attempt in range(2):
        await asyncio.sleep(1)
        result = await _rpc_call("getTransaction", params, timeout=10)
        if result is not None:
            return result

    return None


def extract_deployer_from_pumpfun_tx(tx: dict) -> str | None:
    """
    Transaction ka fee-payer (pehla signer) hi token ka deployer hota hai.
    Generic hai — Pump.fun, Raydium LaunchLab, Moonshot sab ke liye kaam karta hai.
    """
    if not tx:
        return None
    try:
        account_keys = tx["transaction"]["message"]["accountKeys"]
        for acc in account_keys:
            if acc.get("signer"):
                return acc["pubkey"]
        if account_keys:
            return account_keys[0].get("pubkey")
    except Exception as e:
        logger.warning(f"Deployer extraction failed: {e}")
    return None


# Generic alias - naam zyada accurate hai kyunki yeh sirf pump.fun tak limited nahi
extract_fee_payer = extract_deployer_from_pumpfun_tx


async def get_signatures_for_address(address: str, limit: int = 40) -> list:
    """Wallet ki recent transaction signatures (history) - dev wallet tracker ke liye."""
    result = await _rpc_call("getSignaturesForAddress", [address, {"limit": limit}], timeout=15)
    return result or []


async def get_transactions_batch(signatures: list, concurrency: int = 8) -> list:
    """Multiple signatures ki full transaction details parallel fetch karta hai (rate-limit safe)."""
    sem = asyncio.Semaphore(concurrency)

    async def _fetch(sig):
        async with sem:
            return await get_transaction(sig)

    return await asyncio.gather(*[_fetch(s) for s in signatures])


def extract_new_mint_from_pumpfun_tx(tx: dict) -> str | None:
    """
    Transaction ke postTokenBalances se naye mint address nikalta hai - jo mint
    pre-balances mein nahi tha wo "naya" hai. Generic hai, kisi bhi token-create
    transaction (Pump.fun, Raydium LaunchLab, Moonshot) ke liye kaam karta hai.

    BUG FIX: SOL mint (So111...112) ko explicitly exclude karte hain - yeh hamesha
    quote-currency ke taur pe involved hota hai, "naya token" kabhi nahi hota.
    Pehle isko galti se "new token detected" bana ke dikha rahe the.
    """
    if not tx:
        return None
    try:
        meta = tx.get("meta", {})
        post_balances = meta.get("postTokenBalances", [])
        pre_mints = {b["mint"] for b in meta.get("preTokenBalances", [])}

        candidates = [b["mint"] for b in post_balances if b["mint"] != Config.SOL_MINT]

        for mint in candidates:
            if mint not in pre_mints:
                return mint
        if candidates:
            return candidates[0]
    except Exception as e:
        logger.warning(f"Mint extraction failed: {e}")
    return None


# Generic alias - naam zyada accurate hai
extract_new_mint = extract_new_mint_from_pumpfun_tx


def extract_swap_token_from_tx(tx: dict, exclude_mints: set) -> str | None:
    """Wallet-activity transaction se non-SOL token mint nikalta hai (copy-trade ke liye)."""
    if not tx:
        return None
    try:
        meta = tx.get("meta", {})
        for bal in meta.get("postTokenBalances", []) + meta.get("preTokenBalances", []):
            if bal["mint"] not in exclude_mints:
                return bal["mint"]
    except Exception as e:
        logger.warning(f"Swap token extraction failed: {e}")
    return None


async def get_wallet_sol_balance(pubkey: str) -> float:
    result = await _rpc_call("getBalance", [pubkey], timeout=10)
    if result is None:
        return 0.0
    lamports = result.get("value", 0)
    return lamports / 1_000_000_000


async def get_token_price_in_sol(token_mint: str) -> float | None:
    """1 token ki price SOL mein, Jupiter quote (token -> SOL, 1 unit) se nikalta hai.
    NOTE: Yeh Jupiter API hai, Helius nahi - alag rate limit, isliye semaphore yahan nahi lagaya.
    """
    params = {
        "inputMint": token_mint,
        "outputMint": Config.SOL_MINT,
        "amount": 1_000_000,  # assume 6 decimals; approximate for early price-check use
        "slippageBps": Config.SLIPPAGE_BPS,
    }
    try:
        session = await _get_session()
        async with session.get(Config.JUPITER_QUOTE_URL, params=params, timeout=8) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            out_amount = data.get("outAmount")
            if out_amount is None:
                return None
            return int(out_amount) / 1_000_000_000
    except Exception as e:
        logger.warning(f"get_token_price_in_sol failed: {e}")
        return None
