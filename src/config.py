"""
config.py — Saari settings .env se load hoti hain.
Koi bhi hardcoded key/secret yahan nahi honi chahiye.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    val = os.getenv(name)
    try:
        return float(val) if val else default
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    val = os.getenv(name)
    try:
        return int(val) if val else default
    except ValueError:
        return default


class Config:
    # --- Wallet ---
    SOLANA_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY", "")

    # --- Infra ---
    HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
    HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    HELIUS_WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

    GOPLUS_API_KEY = os.getenv("GOPLUS_API_KEY", "")
    GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")

    # Helius free/starter tier ki RPS limit kam hoti hai - 4 sources parallel chalne se
    # 429 (rate limit) easily lag jata hai. Yeh saari Helius RPC calls ke liye max
    # concurrent requests ki limit hai. 429 baar-baar aaye to yeh aur kam kar do.
    RPC_MAX_CONCURRENT = _int("RPC_MAX_CONCURRENT", 4)

    # Concurrency-limit kaafi nahi hota (4 requests ek sath fire+complete ho sakti hain,
    # burst). Yeh minimum gap hai (seconds) consecutive Helius RPC calls ke beech -
    # asli request-rate control karta hai. 0.25 = max ~4 req/sec.
    RPC_MIN_INTERVAL_SEC = _float("RPC_MIN_INTERVAL_SEC", 0.25)

    # --- Discord ---
    DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
    DISCORD_CONTROL_CHANNEL_ID = _int("DISCORD_CONTROL_CHANNEL_ID", 0)
    DISCORD_ALERTS_CHANNEL_ID = _int("DISCORD_ALERTS_CHANNEL_ID", 0)
    DISCORD_GUILD_ID = _int("DISCORD_GUILD_ID", 0)  # optional - slash commands turant sync karne ke liye

    # --- Risk management ---
    DRY_RUN = _bool("DRY_RUN", True)
    STARTING_CAPITAL_SOL = _float("STARTING_CAPITAL_SOL", 0.5)
    MAX_POSITION_SIZE_PCT = _float("MAX_POSITION_SIZE_PCT", 10)
    MAX_CONCURRENT_POSITIONS = _int("MAX_CONCURRENT_POSITIONS", 3)
    DAILY_LOSS_LIMIT_PCT = _float("DAILY_LOSS_LIMIT_PCT", 20)
    DEFAULT_STOP_LOSS_PCT = _float("DEFAULT_STOP_LOSS_PCT", 25)
    DEFAULT_TAKE_PROFIT_PCT = _float("DEFAULT_TAKE_PROFIT_PCT", 50)
    SLIPPAGE_BPS = _int("SLIPPAGE_BPS", 300)
    PRIORITY_FEE_LAMPORTS = _int("PRIORITY_FEE_LAMPORTS", 200_000)

    # --- Copy trading targets ---
    COPY_TRADE_WALLETS = [
        w.strip() for w in os.getenv("COPY_TRADE_WALLETS", "").split(",") if w.strip()
    ]

    # --- Well-known program IDs (Solana mainnet) ---
    PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    # NOTE: pehle yahan "LanMV9..." ko AMM V4 likha tha jo GALAT tha - woh actually
    # Raydium LaunchLab hai (Raydium ka apna pump.fun-jaisa bonding-curve launchpad).
    # Asli classic AMM V4 (OpenBook-integrated, jo tum chahte ho) yeh hai:
    RAYDIUM_AMM_V4_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
    RAYDIUM_LAUNCHLAB_PROGRAM_ID = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"
    RAYDIUM_CPMM_PROGRAM_ID = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
    MOONSHOT_PROGRAM_ID = "MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG"

    # --- Public APIs (no key required) ---
    JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
    JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
    RUGCHECK_REPORT_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
    GOPLUS_SOLANA_URL = "https://api.gopluslabs.io/api/v1/solana/token_security"
    DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
    SOL_MINT = "So11111111111111111111111111111111111111112"
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    # --- Konse sources on hain ---
    ENABLE_PUMPFUN_WATCHER = _bool("ENABLE_PUMPFUN_WATCHER", True)
    ENABLE_RAYDIUM_LAUNCHLAB_WATCHER = _bool("ENABLE_RAYDIUM_LAUNCHLAB_WATCHER", True)
    # NOTE: Logs se pata chala CPMM ki mint-extraction unreliable hai (same mint
    # repeatedly different deployers ke sath detect hua) - CPMM ki transaction
    # structure Pump.fun se kaafi different hai (LP mint + 2 vaults), generic
    # extraction logic isko sahi se handle nahi kar paa rahi. Default OFF rakha
    # hai jab tak iski extraction logic specifically fix na ho.
    ENABLE_RAYDIUM_CPMM_WATCHER = _bool("ENABLE_RAYDIUM_CPMM_WATCHER", False)
    ENABLE_MOONSHOT_WATCHER = _bool("ENABLE_MOONSHOT_WATCHER", True)

    # Naya token detect hone ke kitne sec baad safety check chalayein.
    # NOTE: Pehle yeh 20s tha taake RugCheck index kar le - lekin practically RugCheck
    # bohot fresh (seconds-old) tokens ko 20s baad bhi index nahi kar pata (logs se
    # confirm hua). Ab jab RugCheck "no data" ko penalize nahi karte (sirf -5, block
    # nahi), itna lamba wait karna sirf speed kam karta hai bina real fayde ke.
    # Chhota rakha hai - bas itna ke transaction settle ho jaye.
    NEW_TOKEN_EVAL_DELAY_SEC = _int("NEW_TOKEN_EVAL_DELAY_SEC", 5)

    # Agar koi source signal nahi de raha, yeh true karke raw logs dekho (Railway logs mein)
    # taake pata chale instruction-name keyword guess sahi hai ya nahi.
    DEBUG_LOG_UNMATCHED = _bool("DEBUG_LOG_UNMATCHED", False)

    # --- Dev Wallet Tracker ---
    DEV_WALLET_HISTORY_LIMIT = _int("DEV_WALLET_HISTORY_LIMIT", 40)  # kitni purani signatures check karein
    DEV_WALLET_CACHE_TTL_SEC = _int("DEV_WALLET_CACHE_TTL_SEC", 3600)  # 1 ghante cache, repeat lookups na ho
    # NOTE: Pump.fun pe ~95% tokens naturally "dead" ho jate hain (interest khatam,
    # zaroori nahi scam ho) - agar threshold bohot strict rakhein (0.5), to almost
    # HAR active deployer block ho jata hai, chahe genuinely scammer ho ya nahi.
    # RugCheck ka apna "Creator history of rugged tokens" check bhi isi signal pe
    # overlap karta hai - dono mil ke double-penalize kar rahe the. Threshold upar
    # kiya hai (sirf bohot zyada consistent rug-pattern wale block hon) aur zyada
    # data points maangte hain judge karne se pehle.
    DEV_WALLET_MAX_RUG_RATIO = _float("DEV_WALLET_MAX_RUG_RATIO", 0.85)  # 85%+ purane tokens rugged = block
    DEV_WALLET_MIN_HISTORY_TO_JUDGE = _int("DEV_WALLET_MIN_HISTORY_TO_JUDGE", 3)  # kam se kam itne purane tokens hon tab hi judge karo
    DEAD_TOKEN_LIQUIDITY_USD = _float("DEAD_TOKEN_LIQUIDITY_USD", 200)  # is se kam liquidity = "rugged" maana jaye
    DEAD_TOKEN_MIN_AGE_HOURS = _float("DEAD_TOKEN_MIN_AGE_HOURS", 12)  # itne purane token ko hi dead judge karo

    # --- Advanced Trailing Stop (staircase) ---
    # Format: "profit_pct:locked_profit_pct,profit_pct:locked_profit_pct,..."
    # Jaise jaise price upar jaye, stop-loss bhi upar shift hota hai aur kabhi neeche nahi aata.
    TRAILING_STAIRCASE_RAW = os.getenv("TRAILING_STAIRCASE", "15:0,30:15,60:30,100:60,200:120")
    TRAILING_PCT_FROM_PEAK = _float("TRAILING_PCT_FROM_PEAK", 20)  # all-time-high se itna % neeche aaye to exit

    @classmethod
    def trailing_staircase(cls):
        """Parsed staircase tiers: [(profit_trigger_pct, locked_in_profit_pct), ...] sorted ascending."""
        tiers = []
        for part in cls.TRAILING_STAIRCASE_RAW.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            trigger, locked = part.split(":")
            try:
                tiers.append((float(trigger), float(locked)))
            except ValueError:
                continue
        return sorted(tiers, key=lambda t: t[0])

    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "bot.db")

    @classmethod
    def validate(cls):
        """Startup pe basic sanity check - missing critical config pakadta hai."""
        problems = []
        if not cls.SOLANA_PRIVATE_KEY and not cls.DRY_RUN:
            problems.append("SOLANA_PRIVATE_KEY missing aur DRY_RUN=false hai — yeh khatarnak hai.")
        if not cls.HELIUS_API_KEY:
            problems.append("HELIUS_API_KEY missing — chain monitoring kaam nahi karegi.")
        if not cls.DISCORD_BOT_TOKEN:
            problems.append("DISCORD_BOT_TOKEN missing — Discord bot start nahi hoga.")
        return problems

