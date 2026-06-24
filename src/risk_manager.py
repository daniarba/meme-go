"""
risk_manager.py — Saari risk rules yahan enforce hoti hain.
Koi bhi trade execute hone se pehle yeh checks pass karna zaroori hai.
"""
import logging

from . import database as db
from .config import Config

logger = logging.getLogger("risk_manager")


class RiskManager:
    def __init__(self, wallet_balance_sol: float):
        self.balance = wallet_balance_sol
        db.ensure_today_row(wallet_balance_sol)

    def position_size_sol(self) -> float:
        """Per-trade position size = balance ka MAX_POSITION_SIZE_PCT."""
        return round(self.balance * (Config.MAX_POSITION_SIZE_PCT / 100), 4)

    def can_open_new_position(self) -> tuple[bool, str]:
        open_positions = db.get_open_positions()
        if len(open_positions) >= Config.MAX_CONCURRENT_POSITIONS:
            return False, f"Max concurrent positions ({Config.MAX_CONCURRENT_POSITIONS}) reached."

        stats = db.get_today_stats()
        if stats and stats["starting_balance_sol"] > 0:
            loss_pct = -(stats["realized_pnl_sol"] / stats["starting_balance_sol"]) * 100
            if loss_pct >= Config.DAILY_LOSS_LIMIT_PCT:
                return False, (
                    f"Daily loss limit hit ({loss_pct:.1f}% >= {Config.DAILY_LOSS_LIMIT_PCT}%). "
                    "Bot aaj ke liye pause hai."
                )

        if self.position_size_sol() <= 0:
            return False, "Position size 0 ya negative hai — balance check karo."

        return True, "ok"

    def compute_stop_loss(self, entry_price: float) -> float:
        return entry_price * (1 - Config.DEFAULT_STOP_LOSS_PCT / 100)

    def compute_take_profit(self, entry_price: float) -> float:
        return entry_price * (1 + Config.DEFAULT_TAKE_PROFIT_PCT / 100)

    def check_exit_conditions(self, position: dict, current_price: float) -> tuple[bool, str]:
        """
        Advanced staircase trailing stop-loss + take-profit logic.

        Kaam kaise karta hai:
        1. All-time-high (trailing_high) track hota hai is position ke liye
        2. Jaise jaise profit % badhta hai, staircase tiers se stop-loss "lock" hota hai
           upar — kabhi neeche nahi jata (Config.TRAILING_STAIRCASE se configurable)
        3. Highest tier cross hone ke baad ek percentage-trail bhi lagta hai
           (peak se TRAILING_PCT_FROM_PEAK% neeche aaye to exit) — yeh bade upside
           ko bhi capture karta hai bina hard tier ka wait kiye
        4. Hard take-profit aur original stop-loss bhi backstop ke taur pe rehte hain
        """
        entry = position["entry_price_sol"]
        prev_high = position.get("trailing_high") or entry
        trailing_high = max(prev_high, current_price)

        if trailing_high > prev_high:
            db.update_trailing_high(position["id"], trailing_high)

        current_profit_pct = ((current_price / entry) - 1) * 100
        peak_profit_pct = ((trailing_high / entry) - 1) * 100

        # --- Staircase floor: tiers se locked-in profit nikalo ---
        staircase_floor_price = position["stop_loss_price"]  # default: original hard stop
        for trigger_pct, locked_pct in Config.trailing_staircase():
            if peak_profit_pct >= trigger_pct:
                locked_price = entry * (1 + locked_pct / 100)
                staircase_floor_price = max(staircase_floor_price, locked_price)

        # --- Percentage-trail from peak (pehla tier cross hone ke baad activate hota hai) ---
        any_tier_triggered = any(
            peak_profit_pct >= trigger_pct for trigger_pct, _ in Config.trailing_staircase()
        )
        trail_from_peak_price = None
        if any_tier_triggered:
            trail_from_peak_price = trailing_high * (1 - Config.TRAILING_PCT_FROM_PEAK / 100)

        effective_stop = staircase_floor_price
        if trail_from_peak_price:
            effective_stop = max(effective_stop, trail_from_peak_price)

        if current_price <= effective_stop:
            if current_profit_pct <= 0:
                return True, "stop_loss_hit"
            return True, f"trailing_staircase_exit (locked {current_profit_pct:.0f}%)"

        # hard take-profit backstop (agar staircase configure na ho to bhi yeh kaam kare)
        if current_price >= position["take_profit_price"] and not Config.trailing_staircase():
            return True, "take_profit_hit"

        return False, "hold"
