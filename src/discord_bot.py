"""
discord_bot.py — Discord se bot control karna, ek "Coin Sniper"-style button panel ke sath.

/start ya /menu likhne pe ek inline-button panel aata hai (jaisa Telegram bots mein hota hai):
  ⚙️ Settings   👛 Wallet
  📊 Positions  📋 Orders
  💰 PnL        ⏸️ Pause/Resume

Purane text commands (!status, !pause, etc.) bhi kaam karte hain — backward compatible.
"""
import logging
import time

import discord
from discord.ext import commands

from . import database as db
from . import executor
from . import solana_rpc
from . import stats
from .config import Config

logger = logging.getLogger("discord_bot")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Shared mutable state - main.py isko import karke control loop ke andar check karta hai
bot_state = {"paused": False}


def _in_control_channel(channel_id: int) -> bool:
    return channel_id == Config.DISCORD_CONTROL_CHANNEL_ID


# ============================================================
# Embed builders — har button ka content yahan se aata hai
# ============================================================

def _build_settings_embed() -> discord.Embed:
    mode = "🟢 DRY RUN (paper trading)" if Config.DRY_RUN else "🔴 LIVE (real funds)"
    e = discord.Embed(title="⚙️ Settings", color=discord.Color.blurple())
    e.add_field(name="Mode", value=mode, inline=False)
    e.add_field(name="Max position size", value=f"{Config.MAX_POSITION_SIZE_PCT}% of balance", inline=True)
    e.add_field(name="Max concurrent positions", value=str(Config.MAX_CONCURRENT_POSITIONS), inline=True)
    e.add_field(name="Daily loss limit", value=f"{Config.DAILY_LOSS_LIMIT_PCT}%", inline=True)
    e.add_field(name="Slippage", value=f"{Config.SLIPPAGE_BPS / 100}%", inline=True)
    tiers = Config.trailing_staircase()
    tiers_str = ", ".join(f"+{t}%→+{l}%" for t, l in tiers) if tiers else "off"
    e.add_field(name="Staircase trailing tiers", value=tiers_str, inline=False)
    e.add_field(name="Peak-trail %", value=f"{Config.TRAILING_PCT_FROM_PEAK}%", inline=True)
    e.add_field(name="Copy-trade wallets", value=str(len(Config.COPY_TRADE_WALLETS)), inline=True)
    e.set_footer(text="Change karne ke liye .env edit karke bot restart karo.")
    return e


async def _build_wallet_embed() -> discord.Embed:
    e = discord.Embed(title="👛 Wallet", color=discord.Color.gold())

    if Config.DRY_RUN:
        virtual_balance = db.get_virtual_balance_sol()
        e.description = "🟢 DRY RUN mode — yeh virtual (demo) balance hai, real wallet use nahi ho raha."
        e.add_field(name="Virtual balance", value=f"{virtual_balance} SOL", inline=False)
        open_positions = db.get_open_positions()
        locked = sum(p["sol_spent"] for p in open_positions)
        e.add_field(name="Locked in positions", value=f"{locked:.4f} SOL", inline=True)
        e.set_footer(text="Balance change karne ke liye: !setbalance <amount>")
        return e

    if not Config.SOLANA_PRIVATE_KEY:
        e.description = "Koi wallet configure nahi hai."
        return e

    wallet = executor.load_wallet()
    pubkey = str(wallet.pubkey())
    balance = await solana_rpc.get_wallet_sol_balance(pubkey)
    e.add_field(name="Address", value=f"`{pubkey}`", inline=False)
    e.add_field(name="SOL Balance", value=f"{balance:.4f} SOL", inline=True)
    open_positions = db.get_open_positions()
    locked = sum(p["sol_spent"] for p in open_positions)
    e.add_field(name="Locked in positions", value=f"{locked:.4f} SOL", inline=True)
    return e


def _build_positions_embed() -> discord.Embed:
    positions = db.get_open_positions()
    e = discord.Embed(title="📊 Open Positions", color=discord.Color.green())
    if not positions:
        e.description = "Koi open position nahi hai."
        return e
    for p in positions[:15]:
        age_min = (time.time() - p["opened_at"]) / 60
        e.add_field(
            name=f"{p['token_symbol'] or p['token_mint'][:8]} ({p['source']})",
            value=(
                f"Entry: `{p['entry_price_sol']:.8f}` SOL\n"
                f"SL: `{p['stop_loss_price']:.8f}` | TP: `{p['take_profit_price']:.8f}`\n"
                f"Spent: {p['sol_spent']:.4f} SOL | Age: {age_min:.0f}m"
            ),
            inline=False,
        )
    return e


def _build_orders_embed() -> discord.Embed:
    trades = db.get_recent_trades(limit=10)
    e = discord.Embed(title="📋 Recent Orders", color=discord.Color.orange())
    if not trades:
        e.description = "Abhi tak koi trade nahi hua."
        return e
    for t in trades:
        emoji = "🟢" if t["side"] == "buy" else ("🔴" if t["pnl_sol"] < 0 else "🟢")
        tag = "[DRY] " if t["dry_run"] else ""
        e.add_field(
            name=f"{emoji} {tag}{t['side'].upper()} {t['token_symbol'] or t['token_mint'][:8]}",
            value=f"{t['sol_amount']:.4f} SOL @ {t['price_sol']:.8f}" + (
                f" | PnL: {t['pnl_sol']:+.4f}" if t["side"] == "sell" else ""
            ),
            inline=False,
        )
    return e


def _build_pnl_embed() -> discord.Embed:
    today = db.get_today_stats()
    alltime = db.get_alltime_stats()
    e = discord.Embed(title="💰 PnL Summary", color=discord.Color.dark_gold())
    today_pnl = today["realized_pnl_sol"] if today else 0
    today_trades = today["trades_count"] if today else 0
    e.add_field(name="Today", value=f"{today_pnl:+.4f} SOL ({today_trades} trades)", inline=False)
    e.add_field(
        name="All-time",
        value=f"{alltime['total_pnl']:+.4f} SOL ({alltime['closed_positions']} closed positions)",
        inline=False,
    )
    return e


# ============================================================
# Main Menu View — persistent buttons (Telegram-style panel)
# ============================================================

class MainMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self._sync_pause_button()

    def _sync_pause_button(self):
        if bot_state["paused"]:
            self.pause_button.label = "▶️ Resume"
            self.pause_button.style = discord.ButtonStyle.success
        else:
            self.pause_button.label = "⏸️ Pause"
            self.pause_button.style = discord.ButtonStyle.danger

    @discord.ui.button(label="⚙️ Settings", style=discord.ButtonStyle.secondary, row=0, custom_id="menu:settings")
    async def settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=_build_settings_embed(), ephemeral=True)

    @discord.ui.button(label="👛 Wallet", style=discord.ButtonStyle.secondary, row=0, custom_id="menu:wallet")
    async def wallet_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = await _build_wallet_embed()
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="📊 Positions", style=discord.ButtonStyle.primary, row=1, custom_id="menu:positions")
    async def positions_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=_build_positions_embed(), ephemeral=True)

    @discord.ui.button(label="📋 Orders", style=discord.ButtonStyle.primary, row=1, custom_id="menu:orders")
    async def orders_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=_build_orders_embed(), ephemeral=True)

    @discord.ui.button(label="💰 PnL", style=discord.ButtonStyle.success, row=2, custom_id="menu:pnl")
    async def pnl_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=_build_pnl_embed(), ephemeral=True)

    @discord.ui.button(label="⏸️ Pause", style=discord.ButtonStyle.danger, row=2, custom_id="menu:pause")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _in_control_channel(interaction.channel.id):
            await interaction.response.send_message("Yeh control channel mein use karo.", ephemeral=True)
            return
        bot_state["paused"] = not bot_state["paused"]
        self._sync_pause_button()
        await interaction.response.edit_message(view=self)
        state_text = "⏸ Paused — naye trades nahi lenge." if bot_state["paused"] else "▶ Resumed — trading active hai."
        await interaction.followup.send(state_text, ephemeral=True)


def _welcome_embed() -> discord.Embed:
    mode = "DRY RUN (paper trading)" if Config.DRY_RUN else "LIVE (real funds)"
    e = discord.Embed(
        title="🤖 Memecoin Sniper Bot",
        description=f"Online. Mode: **{mode}**\nNeeche se control karo 👇",
        color=discord.Color.purple(),
    )
    return e


# ============================================================
# Slash + prefix commands to open the panel
# ============================================================

@bot.hybrid_command(name="start", description="Bot ka control panel kholo")
async def start_cmd(ctx: commands.Context):
    await ctx.send(embed=_welcome_embed(), view=MainMenuView())


@bot.hybrid_command(name="menu", description="Bot ka control panel kholo")
async def menu_cmd(ctx: commands.Context):
    await ctx.send(embed=_welcome_embed(), view=MainMenuView())


@bot.event
async def on_ready():
    logger.info(f"Discord bot logged in as {bot.user}")
    try:
        if Config.DISCORD_GUILD_ID:
            guild = discord.Object(id=Config.DISCORD_GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            logger.info("Slash commands synced instantly to configured guild.")
        else:
            await bot.tree.sync()
            logger.info("Slash commands synced globally (Discord pe propagate hone mein ~1 hour lag sakta hai pehli baar).")
    except Exception as e:
        logger.warning(f"Slash command sync failed: {e}")

    channel = bot.get_channel(Config.DISCORD_ALERTS_CHANNEL_ID)
    if channel:
        await channel.send("🤖 Memecoin bot online.")

    control_channel = bot.get_channel(Config.DISCORD_CONTROL_CHANNEL_ID)
    if control_channel:
        await control_channel.send(embed=_welcome_embed(), view=MainMenuView())


# ============================================================
# Backward-compatible text commands (control channel only)
# ============================================================

def _check_ctx(ctx) -> bool:
    return _in_control_channel(ctx.channel.id)


@bot.command(name="status")
async def status_cmd(ctx):
    if not _check_ctx(ctx):
        return
    await ctx.send(embed=_build_pnl_embed())


@bot.command(name="positions")
async def positions_cmd(ctx):
    if not _check_ctx(ctx):
        return
    await ctx.send(embed=_build_positions_embed())


@bot.command(name="pause")
async def pause_cmd(ctx):
    if not _check_ctx(ctx):
        return
    bot_state["paused"] = True
    await ctx.send("⏸ Bot pause ho gaya — koi naya trade nahi lega. Open positions monitor hote rahenge.")


@bot.command(name="resume")
async def resume_cmd(ctx):
    if not _check_ctx(ctx):
        return
    bot_state["paused"] = False
    await ctx.send("▶ Bot resume ho gaya.")


@bot.command(name="mode")
async def mode_cmd(ctx):
    if not _check_ctx(ctx):
        return
    mode = "DRY RUN (paper trading, real funds use nahi ho rahe)" if Config.DRY_RUN else "⚠️ LIVE (real funds active)"
    await ctx.send(f"Current mode: **{mode}**\nMode change karne ke liye .env mein DRY_RUN edit karke bot restart karo.")


@bot.command(name="setbalance")
async def setbalance_cmd(ctx, amount: float = None):
    """Paper-trading ka virtual balance khud set karo - .env edit ya redeploy ki zaroorat nahi."""
    if not _check_ctx(ctx):
        return
    if not Config.DRY_RUN:
        await ctx.send("⚠️ Bot abhi LIVE mode mein hai — virtual balance sirf DRY_RUN mein kaam karta hai.")
        return
    if amount is None or amount <= 0:
        await ctx.send("Usage: `!setbalance <SOL amount>` — jaise `!setbalance 5`")
        return
    db.set_virtual_balance_sol(amount)
    await ctx.send(f"✅ Paper-trading virtual balance ab **{amount} SOL** hai. Naye trades isi se size honge.")


@bot.command(name="balance")
async def balance_cmd(ctx):
    """Current paper-trading balance dikhata hai."""
    if not _check_ctx(ctx):
        return
    if Config.DRY_RUN:
        bal = db.get_virtual_balance_sol()
        await ctx.send(f"💰 Paper-trading virtual balance: **{bal} SOL**\nChange karne ke liye: `!setbalance <amount>`")
    else:
        wallet = executor.load_wallet()
        if wallet:
            real_bal = await solana_rpc.get_wallet_sol_balance(str(wallet.pubkey()))
            await ctx.send(f"💰 Real wallet balance (LIVE mode): **{real_bal:.4f} SOL**")
        else:
            await ctx.send("Koi wallet configure nahi hai.")


@bot.command(name="devcheck")
async def devcheck_cmd(ctx, wallet: str = None):
    if not _check_ctx(ctx):
        return
    if not wallet:
        await ctx.send("Usage: `!devcheck <wallet_address>`")
        return

    from . import dev_wallet_tracker  # lazy import - circular import avoid karne ke liye
    await ctx.send(f"🔍 Checking `{wallet[:8]}…` history...")
    result = await dev_wallet_tracker.evaluate_dev_wallet(wallet)
    emoji = {"high": "🚫", "medium": "⚠️", "low": "✅", "unknown": "❓"}.get(result["risk"], "❓")
    await ctx.send(
        f"{emoji} **{result['risk'].upper()}** | "
        f"{result['rugged_count']}/{result['total_created']} tokens rugged "
        f"({result['rug_ratio']*100:.0f}%)\n{result['reason']}"
    )


@bot.command(name="debug")
async def debug_cmd(ctx):
    """Pata lagao bot kahan stuck hai - WS events aa rahe hain ya filter mein sab skip ho rahe hain."""
    if not _check_ctx(ctx):
        return
    snap = stats.snapshot()
    src = snap["source_events"]
    c = snap["counters"]

    lines = ["**📡 Source Events (WS messages received)**"]
    if not src:
        lines.append("Koi WS event abhi tak nahi mila — Helius connection ya program ID check karo.")
    for name, count in src.items():
        matched = c.get(f"{name}_matched", 0)
        lines.append(f"`{name}`: {count} messages | {matched} matched (Create instruction)")

    lines.append("")
    lines.append("**🔍 Pipeline Funnel**")
    lines.append(f"New tokens detected: {c.get('new_token_detected', 0)}")
    lines.append(f"Evaluated (safety+dev check chala): {c.get('evaluated', 0)}")
    lines.append(f"Skipped - safety filter: {c.get('skipped_safety', 0)}")
    lines.append(f"Skipped - dev wallet risk: {c.get('skipped_devwallet', 0)}")
    lines.append(f"Skipped - risk manager: {c.get('skipped_risk', 0)}")
    lines.append(f"Bought: {c.get('bought', 0)} | Buy failed: {c.get('buy_failed', 0)}")
    lines.append(f"tx fetch failed: {c.get('tx_fetch_failed', 0)} | mint extract failed: {c.get('mint_extract_failed', 0)}")

    await ctx.send("\n".join(lines))


async def send_alert(message: str):
    channel = bot.get_channel(Config.DISCORD_ALERTS_CHANNEL_ID)
    if channel:
        await channel.send(message)
    else:
        logger.warning("Alerts channel not found - check DISCORD_ALERTS_CHANNEL_ID")
