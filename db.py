import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "finance.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS expenses (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                amount     REAL    NOT NULL,
                category   TEXT    NOT NULL,
                created_at TEXT    DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS profiles (
                user_id        INTEGER PRIMARY KEY,
                monthly_income REAL,
                goal_text      TEXT,
                net_worth      REAL
            );
            CREATE TABLE IF NOT EXISTS goals (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL REFERENCES users(id),
                title          TEXT    NOT NULL,
                category       TEXT    NOT NULL DEFAULT 'general',
                target_amount  REAL    NOT NULL,
                saved_amount   REAL    NOT NULL DEFAULT 0,
                deadline       TEXT,
                created_at     TEXT    DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS budgets (
                user_id       INTEGER NOT NULL,
                category      TEXT    NOT NULL,
                monthly_limit REAL    NOT NULL,
                PRIMARY KEY (user_id, category)
            );
            CREATE TABLE IF NOT EXISTS incomes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                amount     REAL    NOT NULL,
                source     TEXT    NOT NULL DEFAULT 'unplanned',
                created_at TEXT    DEFAULT (datetime('now'))
            );
            UPDATE expenses SET created_at = datetime('now') WHERE created_at IS NULL;
            UPDATE incomes  SET created_at = datetime('now') WHERE created_at IS NULL;
        """)
        # Safe migrations — ignore if column already exists
        for sql in [
            "ALTER TABLE profiles ADD COLUMN net_worth REAL",
            "ALTER TABLE profiles ADD COLUMN goal_text TEXT",
            "ALTER TABLE goals ADD COLUMN saved_amount REAL NOT NULL DEFAULT 0",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass
