import os
from typing import Optional, List, Tuple
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")


class Database:
    def __init__(self, url: str = DATABASE_URL):
        self.url = url
        self._init()

    def _connect(self):
        return psycopg2.connect(self.url)

    def _init(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS vault (
                        site TEXT PRIMARY KEY,
                        type TEXT DEFAULT '',
                        website TEXT DEFAULT '',
                        username TEXT NOT NULL,
                        password TEXT NOT NULL,
                        notes TEXT DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                for col, defn in [
                    ("type",    "TEXT DEFAULT ''"),
                    ("website", "TEXT DEFAULT ''"),
                    ("notes",   "TEXT DEFAULT ''"),
                ]:
                    cur.execute(f"ALTER TABLE vault ADD COLUMN IF NOT EXISTS {col} {defn}")
            conn.commit()

    def save_entry(self, site: str, username: str, encrypted_password: str,
                   type_: str = "", website: str = "", notes: str = ""):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO vault (site, type, website, username, password, notes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (site) DO UPDATE SET
                        type     = EXCLUDED.type,
                        website  = EXCLUDED.website,
                        username = EXCLUDED.username,
                        password = EXCLUDED.password,
                        notes    = EXCLUDED.notes,
                        updated_at = NOW()
                """, (site, type_, website, username, encrypted_password, notes))
            conn.commit()

    def get_entry(self, site: str) -> Optional[Tuple]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT username, password, type, website, notes FROM vault WHERE site = %s",
                    (site,)
                )
                return cur.fetchone()

    def list_sites(self) -> List[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT site FROM vault ORDER BY site")
                return [r[0] for r in cur.fetchall()]

    def search_sites(self, query: str) -> List[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT site FROM vault WHERE site ILIKE %s ORDER BY site",
                    (f"%{query}%",)
                )
                return [r[0] for r in cur.fetchall()]

    def delete_entry(self, site: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM vault WHERE site = %s", (site,))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted
