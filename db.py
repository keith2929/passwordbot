import os
from typing import Optional, List, Tuple
import psycopg2
from psycopg2.extras import execute_values

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
                        username TEXT NOT NULL,
                        password TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
            conn.commit()

    def save_entry(self, site: str, username: str, encrypted_password: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO vault (site, username, password)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (site) DO UPDATE SET
                        username = EXCLUDED.username,
                        password = EXCLUDED.password,
                        updated_at = NOW()
                """, (site, username, encrypted_password))
            conn.commit()

    def get_entry(self, site: str) -> Optional[Tuple[str, str]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT username, password FROM vault WHERE site = %s", (site,)
                )
                return cur.fetchone()

    def list_sites(self) -> List[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT site FROM vault ORDER BY site")
                return [r[0] for r in cur.fetchall()]

    def delete_entry(self, site: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM vault WHERE site = %s", (site,))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted
