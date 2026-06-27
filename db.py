import os
import sqlite3
from typing import Optional, List, Tuple

DB_PATH = "/data/vault.db"


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vault (
                    site TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    password TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def save_entry(self, site: str, username: str, encrypted_password: str):
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO vault (site, username, password)
                VALUES (?, ?, ?)
                ON CONFLICT(site) DO UPDATE SET
                    username = excluded.username,
                    password = excluded.password,
                    updated_at = CURRENT_TIMESTAMP
            """, (site, username, encrypted_password))
            conn.commit()

    def get_entry(self, site: str) -> Optional[Tuple[str, str]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT username, password FROM vault WHERE site = ?", (site,)
            ).fetchone()
        return row  # (username, encrypted_password) or None

    def list_sites(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT site FROM vault ORDER BY site"
            ).fetchall()
        return [r[0] for r in rows]

    def delete_entry(self, site: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM vault WHERE site = ?", (site,))
            conn.commit()
        return cur.rowcount > 0
