"""
safety_filter.py — Token ko buy karne se PEHLE yeh checks chalte hain.
RugCheck + GoPlus dono se data le ke ek combined safety score banata hai.

Score 0-100. Threshold se kam ho to bot trade skip karta hai.
"""
import aiohttp
import logging

from .config import Config

logger = logging.getLogger("safety_filter")

MIN_SAFETY_SCORE = 60  # isse kam score wale tokens skip honge


async def _fetch_json(session, url, params=None, headers=None, timeout=8):
    try:
        async with session.get(url, params=params, headers=headers, timeout=timeout) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.warning(f"GET {url} -> status {resp.status}")
            return None
    except Exception as e:
        logger.warning(f"Fetch failed {url}: {e}")
        return None


async def check_rugcheck(session, mint: str) -> dict:
    """RugCheck.xyz summary report - mint/freeze authority, LP lock, risk flags."""
    url = Config.RUGCHECK_REPORT_URL.format(mint=mint)
    data = await _fetch_json(session, url)
    if not data:
        # Brand-new tokens (seconds/minutes old) aksar RugCheck ke indexer mein
        # abhi tak nahi hote - yeh "unsafe" ka proof nahi hai, sirf "data nahi hai".
        # Isliye yeh "risks" list mein NAHI daalte (warna keyword-matching isko
        # galti se 'rug' wala danger-flag samajh leta hai).
        return {"ok": False, "risks": []}

    risks = [r.get("name", "") for r in data.get("risks", [])]
    rc_score = data.get("score", 0)
    return {"ok": True, "raw_score": rc_score, "risks": risks}


async def check_goplus(session, mint: str) -> dict:
    """GoPlus Security - honeypot / mintable / ownership flags for Solana token."""
    params = {"contract_addresses": mint}
    headers = {}
    if Config.GOPLUS_API_KEY:
        headers["Authorization"] = Config.GOPLUS_API_KEY
    data = await _fetch_json(session, Config.GOPLUS_SOLANA_URL, params=params, headers=headers)
    # BUG FIX: GoPlus kabhi-kabhi HTTP 200 ke sath bhi {"result": null} bhejta hai
    # (jab token bilkul fresh ho, indexed na ho) - pehle "result" key exist karne
    # ki hi check thi, null value pe crash ho jata tha. Ab dono check hote hain.
    if not data or data.get("result") is None:
        return {"ok": False, "flags": []}

    info = data["result"].get(mint, {})
    flags = []
    if info.get("is_honeypot") == "1":
        flags.append("honeypot")
    if info.get("mintable", {}).get("status") == "1":
        flags.append("mintable")
    if info.get("freezable", {}).get("status") == "1":
        flags.append("freezable")
    # NOTE: low_holder_count hata diya - har brand-new token (jo hum seconds-old pe
    # check kar rahe hain) ka holder count hamesha kam hoga, yeh signal discriminate
    # nahi karta safe vs unsafe ke beech is stage pe. high_holder_concentration
    # (insider pre-mine) zyada meaningful hai, woh rakha hai.
    top10 = info.get("holders", [])[:10]
    try:
        top10_pct = sum(float(h.get("percent", 0)) for h in top10) * 100
        if top10_pct > 60:
            flags.append("high_holder_concentration")
    except Exception:
        pass

    return {"ok": True, "flags": flags}


def _score_from_flags(rugcheck_result: dict, goplus_result: dict) -> tuple[int, list, bool]:
    """Saare flags ko combine karke ek 0-100 score banata hai. Teesra return value
    hard_block hai - critical flags (honeypot/mintable/freezable) mile to True,
    yeh score se independent hai (warna -40 ka deduction kabhi-kabhi threshold pe
    "safe" ban jata tha, jo galat hai - yeh deal-breakers hain, "maybe risky" nahi)."""
    score = 100
    reasons = []
    hard_block = False

    critical_flags = {"honeypot", "mintable", "freezable"}
    warning_flags = {"high_holder_concentration"}

    # GoPlus ke actual findings - sirf jab data mila ho
    if goplus_result.get("ok"):
        for flag in goplus_result.get("flags", []):
            if flag in critical_flags:
                score -= 40
                reasons.append(flag)
                hard_block = True
            elif flag in warning_flags:
                score -= 15
                reasons.append(flag)

    # RugCheck ke actual findings - sirf jab data mila ho. Keywords specific rakhe
    # hain (generic "rug" substring nahi) taake apni hi meta-status strings galti
    # se match na ho jayein.
    if rugcheck_result.get("ok"):
        for risk in rugcheck_result.get("risks", []):
            risk_lower = risk.lower()
            if any(k in risk_lower for k in ["danger", "honeypot", "rug pull", "scam", "malicious"]):
                score -= 30
                reasons.append(f"rugcheck:{risk}")
                hard_block = True
            elif risk_lower:
                score -= 10
                reasons.append(f"rugcheck:{risk}")

    # Data-availability handling - ALAG se, "risk finding" jaisa treat nahi karte.
    # Ek source missing hone pe sirf mild penalty (brand-new tokens ke liye normal hai),
    # dono missing hone pe hard block (bina kisi data ke trade karna genuinely risky hai).
    missing = []
    if not rugcheck_result.get("ok"):
        missing.append("rugcheck")
    if not goplus_result.get("ok"):
        missing.append("goplus")

    if len(missing) == 2:
        score = 0
        reasons.append("no_safety_data_available")
        hard_block = True
    elif len(missing) == 1:
        score -= 5
        reasons.append(f"{missing[0]}_unavailable")

    return max(0, score), reasons, hard_block


async def evaluate_token(mint: str) -> dict:
    """
    Main entry point. Returns:
    { "safe": bool, "score": int, "reasons": [...] }
    """
    async with aiohttp.ClientSession() as session:
        rc_result = await check_rugcheck(session, mint)
        gp_result = await check_goplus(session, mint)

    score, reasons, hard_block = _score_from_flags(rc_result, gp_result)
    safe = (score >= MIN_SAFETY_SCORE) and not hard_block

    logger.info(f"Safety check {mint}: score={score} safe={safe} hard_block={hard_block} reasons={reasons}")
    return {"safe": safe, "score": score, "reasons": reasons}
