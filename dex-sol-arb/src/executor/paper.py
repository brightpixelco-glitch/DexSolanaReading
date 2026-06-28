"""Paper executor — records hypothetical trades without executing real swaps."""
import sqlite3
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

from ..arb.calculator import ArbOpportunity

DB_PATH = Path(__file__).parent.parent.parent / "paper_trades.db"


class TradeDB:
    def __init__(self, db_path: str = str(DB_PATH)):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    rpc_provider TEXT NOT NULL,
                    rpc_url TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT
                );
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    token_mint TEXT,
                    dex TEXT,
                    side TEXT,
                    trade_size_sol REAL,
                    entry_price REAL,
                    exit_price REAL,
                    profit_sol REAL,
                    profit_percent REAL,
                    confidence REAL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
            """)
            conn.commit()

    def create_session(self, label: str, rpc_provider: str, rpc_url: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO sessions (label, rpc_provider, rpc_url, start_time) VALUES (?, ?, ?, ?)",
                (label, rpc_provider, rpc_url, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return cur.lastrowid

    def end_session(self, session_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET end_time = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), session_id),
            )
            conn.commit()

    def record_trade(self, session_id: int, opp: ArbOpportunity):
        with sqlite3.connect(self.db_path) as conn:
            for leg in opp.legs:
                conn.execute(
                    """INSERT INTO trades
                    (session_id, timestamp, strategy, token_mint, dex, side,
                     trade_size_sol, entry_price, exit_price, profit_sol, profit_percent, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        datetime.now(timezone.utc).isoformat(),
                        opp.strategy,
                        leg.token_mint or "",
                        leg.dex or "",
                        leg.side or "",
                        leg.amount if leg.side == "buy" else 0,
                        leg.price if leg.side == "buy" else 0,
                        leg.price if leg.side == "sell" else 0,
                        opp.profit_sol,
                        opp.profit_percent,
                        opp.confidence,
                    ),
                )
            conn.commit()

    def get_session_summary(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                """SELECT
                    s.label, s.rpc_provider, s.rpc_url, s.start_time, s.end_time,
                    COUNT(t.id) as total_trades,
                    COALESCE(SUM(t.profit_sol), 0) as total_profit_sol,
                    COALESCE(AVG(t.profit_percent), 0) as avg_profit_pct,
                    COALESCE(SUM(CASE WHEN t.profit_sol > 0 THEN 1 ELSE 0 END), 0) as wins,
                    COALESCE(SUM(CASE WHEN t.profit_sol <= 0 THEN 1 ELSE 0 END), 0) as losses,
                    COALESCE(MAX(t.profit_sol), 0) as best_trade,
                    COALESCE(MIN(t.profit_sol), 0) as worst_trade
                FROM sessions s
                LEFT JOIN trades t ON t.session_id = s.id
                GROUP BY s.id
                ORDER BY s.id""",
            ).fetchall()

    def get_trades_with_session(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                """SELECT t.*, s.label as session_label, s.rpc_provider as session_rpc
                FROM trades t
                JOIN sessions s ON s.id = t.session_id
                ORDER BY t.timestamp""",
            ).fetchall()

    def get_per_pair_pnl(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                """SELECT
                    s.rpc_provider,
                    t.token_mint,
                    COUNT(*) as trades,
                    COALESCE(SUM(t.profit_sol), 0) as total_profit_sol,
                    COALESCE(AVG(t.profit_percent), 0) as avg_profit_pct
                FROM trades t
                JOIN sessions s ON s.id = t.session_id
                WHERE t.token_mint != ''
                GROUP BY s.rpc_provider, t.token_mint
                ORDER BY s.rpc_provider, total_profit_sol DESC""",
            ).fetchall()

    def clear_all(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM sessions")
            conn.commit()


class PaperExecutor:
    """Records hypothetical trades without executing real swaps."""

    def __init__(self, db: TradeDB, session_id: int):
        self.db = db
        self.session_id = session_id
        self.log = logging.getLogger("paper")

    def execute(self, opp: ArbOpportunity) -> bool:
        self.db.record_trade(self.session_id, opp)
        tokens = set(l.token_mint for l in opp.legs if l.token_mint)
        token_str = ",".join(t for t in tokens if t)
        self.log.info(
            f"[PAPER] {opp.strategy} | tokens={token_str} "
            f"profit={opp.profit_sol:.6f} SOL ({opp.profit_percent:.2f}%)"
        )
        return True
