import os
import re
from typing import Optional, List, Dict
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Columns managed by the system — never shown or edited by the user
_SYSTEM_COLS = {"site", "created_at", "updated_at"}


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
                        site        TEXT PRIMARY KEY,
                        type        TEXT DEFAULT '',
                        website     TEXT DEFAULT '',
                        username    TEXT DEFAULT '',
                        password    TEXT DEFAULT '',
                        notes       TEXT DEFAULT '',
                        created_at  TIMESTAMPTZ DEFAULT NOW(),
                        updated_at  TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                for col, defn in [
                    ("type",    "TEXT DEFAULT ''"),
                    ("website", "TEXT DEFAULT ''"),
                    ("notes",   "TEXT DEFAULT ''"),
                ]:
                    cur.execute(f"ALTER TABLE vault ADD COLUMN IF NOT EXISTS {col} {defn}")
            conn.commit()

    def get_columns(self) -> List[str]:
        """Returns user-editable columns in table order."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'vault'
                      AND column_name NOT IN ('site', 'created_at', 'updated_at')
                    ORDER BY ordinal_position
                """)
                return [r[0] for r in cur.fetchall()]

    def add_column(self, col_name: str) -> str:
        """Adds a new TEXT column. Returns the sanitized name."""
        clean = re.sub(r'[^a-z0-9_]', '_', col_name.lower().strip())
        if not clean or clean[0].isdigit() or clean in _SYSTEM_COLS:
            raise ValueError(f"Invalid column name: {col_name!r}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"ALTER TABLE vault ADD COLUMN IF NOT EXISTS {clean} TEXT DEFAULT ''")
            conn.commit()
        return clean

    def save_entry(self, site: str, fields: Dict[str, str]):
        """
        Upsert an entry. `fields` is a dict of column→value.
        Only columns that exist in the table are written.
        """
        existing = set(self.get_columns())
        safe = {k: v for k, v in fields.items() if k in existing}
        if not safe:
            return
        cols = list(safe.keys())
        vals = list(safe.values())
        col_list = ["site"] + cols
        placeholders = ", ".join(["%s"] * len(col_list))
        update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols) + ", updated_at = NOW()"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO vault ({', '.join(col_list)}) VALUES ({placeholders}) "
                    f"ON CONFLICT (site) DO UPDATE SET {update_clause}",
                    [site] + vals,
                )
            conn.commit()

    def get_entry(self, site: str) -> Optional[Dict[str, str]]:
        cols = self.get_columns()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {', '.join(cols)} FROM vault WHERE site = %s", (site,)
                )
                row = cur.fetchone()
        return dict(zip(cols, row)) if row else None

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
