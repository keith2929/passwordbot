import os
import re
from typing import Optional, List, Dict
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")

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
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS vault_extras (
                        id         SERIAL PRIMARY KEY,
                        site       TEXT NOT NULL REFERENCES vault(site) ON DELETE CASCADE,
                        key        TEXT NOT NULL,
                        value      TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
            conn.commit()

    # ── Columns ───────────────────────────────────────────

    def get_columns(self) -> List[str]:
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
        clean = re.sub(r'[^a-z0-9_]', '_', col_name.lower().strip())
        if not clean or clean[0].isdigit() or clean in _SYSTEM_COLS:
            raise ValueError(f"Invalid column name: {col_name!r}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"ALTER TABLE vault ADD COLUMN IF NOT EXISTS {clean} TEXT DEFAULT ''")
            conn.commit()
        return clean

    # ── Vault entries ─────────────────────────────────────

    def save_entry(self, site: str, fields: Dict[str, str]):
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

    # ── Extras ────────────────────────────────────────────

    def get_extras(self, site: str) -> List[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, key, value FROM vault_extras WHERE site = %s ORDER BY id",
                    (site,)
                )
                return [{"id": r[0], "key": r[1], "value": r[2]} for r in cur.fetchall()]

    def add_extra(self, site: str, key: str, value: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO vault_extras (site, key, value) VALUES (%s, %s, %s)",
                    (site, key, value)
                )
            conn.commit()

    def delete_extra(self, extra_id: int):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM vault_extras WHERE id = %s", (extra_id,))
            conn.commit()

    def has_extras(self, site: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM vault_extras WHERE site = %s LIMIT 1", (site,)
                )
                return cur.fetchone() is not None
