"""
database.py — SQLite mein positions, closed trades, aur daily PnL ka record.
Risk manager isi data se daily loss limit check karta hai.
"""
import sqlite3
import os
import time
from contextlib import contextmanager

from .config import Config

os.makedirs(os.path.dirname(Config.DB_PATH), exist_ok=True)


@contextmanager
def get_conn():
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_mint TEXT NOT NULL,
                token_symbol TEXT,
                entry_price_sol REAL NOT NULL,
                amount_tokens REAL NOT NULL,
                sol_spent REAL NOT NULL,
                stop_loss_price REAL,
                take_profit_price REAL,
                trailing_high REAL,
                source TEXT,
                opened_at REAL NOT NULL,
                status TEXT DEFAULT 'open'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_mint TEXT NOT NULL,
                token_symbol TEXT,
                side TEXT NOT NULL,
                price_sol REAL NOT NULL,
                amount_tokens REAL NOT NULL,
                sol_amount REAL NOT NULL,
                pnl_sol REAL DEFAULT 0,
                tx_signature TEXT,
                dry_run INTEGER DEFAULT 1,
                created_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                starting_balance_sol REAL,
                realized_pnl_sol REAL DEFAULT 0,
                trades_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dev_wallet_cache (
                wallet TEXT PRIMARY KEY,
                total_created INTEGER DEFAULT 0,
                rugged_count INTEGER DEFAULT 0,
                checked_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)


def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))


def get_virtual_balance_sol() -> float:
    """DRY_RUN paper-trading ka virtual balance - Discord se `!setbalance` se
    change ho sakta hai bina redeploy kiye. Pehli baar Config.STARTING_CAPITAL_SOL
    default hota hai."""
    val = get_setting("virtual_balance_sol")
    return float(val) if val is not None else Config.STARTING_CAPITAL_SOL


def set_virtual_balance_sol(amount: float):
    set_setting("virtual_balance_sol", amount)


def get_cached_dev_wallet(wallet: str, ttl_seconds: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM dev_wallet_cache WHERE wallet=?", (wallet,)).fetchone()
        if not row:
            return None
        if (time.time() - row["checked_at"]) > ttl_seconds:
            return None
        return dict(row)


def save_dev_wallet_cache(wallet: str, total_created: int, rugged_count: int):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO dev_wallet_cache (wallet, total_created, rugged_count, checked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(wallet) DO UPDATE SET
                total_created=excluded.total_created,
                rugged_count=excluded.rugged_count,
                checked_at=excluded.checked_at
        """, (wallet, total_created, rugged_count, time.time()))


def open_position(token_mint, token_symbol, entry_price_sol, amount_tokens,
                   sol_spent, stop_loss_price, take_profit_price, source):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO positions
            (token_mint, token_symbol, entry_price_sol, amount_tokens, sol_spent,
             stop_loss_price, take_profit_price, trailing_high, source, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (token_mint, token_symbol, entry_price_sol, amount_tokens, sol_spent,
              stop_loss_price, take_profit_price, entry_price_sol, source, time.time()))
        return cur.lastrowid


def close_position(position_id, pnl_sol):
    with get_conn() as conn:
        conn.execute("UPDATE positions SET status='closed' WHERE id=?", (position_id,))
    record_pnl(pnl_sol)


def get_open_positions():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM positions WHERE status='open'").fetchall()
        return [dict(r) for r in rows]


def update_trailing_high(position_id, new_high):
    with get_conn() as conn:
        conn.execute("UPDATE positions SET trailing_high=? WHERE id=?", (new_high, position_id))


def log_trade(token_mint, token_symbol, side, price_sol, amount_tokens,
              sol_amount, pnl_sol, tx_signature, dry_run):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO trades
            (token_mint, token_symbol, side, price_sol, amount_tokens, sol_amount,
             pnl_sol, tx_signature, dry_run, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (token_mint, token_symbol, side, price_sol, amount_tokens, sol_amount,
              pnl_sol, tx_signature, int(dry_run), time.time()))


def _today():
    return time.strftime("%Y-%m-%d")


def ensure_today_row(starting_balance_sol):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM daily_stats WHERE date=?", (_today(),)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO daily_stats (date, starting_balance_sol) VALUES (?, ?)",
                (_today(), starting_balance_sol),
            )


def record_pnl(pnl_sol):
    with get_conn() as conn:
        conn.execute("""
            UPDATE daily_stats
            SET realized_pnl_sol = realized_pnl_sol + ?,
                trades_count = trades_count + 1
            WHERE date=?
        """, (pnl_sol, _today()))


def get_today_stats():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM daily_stats WHERE date=?", (_today(),)).fetchone()
        return dict(row) if row else None


def get_recent_trades(limit=10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_alltime_stats():
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(realized_pnl_sol), 0) as total_pnl,
                COALESCE(SUM(trades_count), 0) as total_trades
            FROM daily_stats
        """).fetchone()
        closed_positions = conn.execute(
            "SELECT COUNT(*) as c FROM positions WHERE status='closed'"
        ).fetchone()["c"]
        return {
            "total_pnl": row["total_pnl"],
            "total_trades": row["total_trades"],
            "closed_positions": closed_positions,
        }
