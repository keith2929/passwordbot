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
                        value      TEXT NOT NULL DEFAULT '',
                        row_num    INTEGER NOT NULL DEFAULT 1,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute(
                    "ALTER TABLE vault_extras ADD COLUMN IF NOT EXISTS row_num INTEGER NOT NULL DEFAULT 1"
                )
            conn.commit()

    # Columns

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

    # Vault entries

    def save_entry(self, site: str, fields: Dict[str, str]):
        existing = set(self.get_columns())
        safe = {k: v for k, v in fields.items() if k in existing}
        if not safe:
            return
        cols = list(safe.keys())
        vals = list(safe.values())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM vault WHERE site = %s", (site,))
                if cur.fetchone():
                    set_clause = ", ".join(f"{c} = %s" for c in cols) + ", updated_at = NOW()"
                    cur.execute(f"UPDATE vault SET {set_clause} WHERE site = %s", vals + [site])
                else:
                    col_list = ["site"] + cols
                    placeholders = ", ".join(["%s"] * len(col_list))
                    cur.execute(
                        f"INSERT INTO vault ({', '.join(col_list)}) VALUES ({placeholders})",
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

    # Extras (column x row table model)

    def get_extra_cols(self, site: str) -> List[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT key FROM vault_extras WHERE site = %s ORDER BY key",
                    (site,)
                )
                return [r[0] for r in cur.fetchall()]

    def get_extra_table(self, site: str) -> Dict[int, Dict[str, str]]:
        """Returns {row_num: {col: value}}."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT row_num, key, value FROM vault_extras WHERE site = %s ORDER BY row_num, key",
                    (site,)
                )
                table: Dict[int, Dict[str, str]] = {}
                for row_num, key, value in cur.fetchall():
                    table.setdefault(row_num, {})[key] = value
                return table

    def get_extra_col_values(self, site: str, col: str) -> List[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT row_num, value FROM vault_extras WHERE site = %s AND key = %s ORDER BY row_num",
                    (site, col)
                )
                return [{"row_num": r[0], "value": r[1]} for r in cur.fetchall()]

    def set_extra_cell(self, site: str, col: str, row_num: int, value: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM vault_extras WHERE site = %s AND key = %s AND row_num = %s",
                    (site, col, row_num)
                )
                existing = cur.fetchone()
                if existing:
                    cur.execute("UPDATE vault_extras SET value = %s WHERE id = %s", (value, existing[0]))
                else:
                    cur.execute(
                        "INSERT INTO vault_extras (site, key, value, row_num) VALUES (%s, %s, %s, %s)",
                        (site, col, value, row_num)
                    )
            conn.commit()

    def add_extra_col(self, site: str, col: str):
        """Add a new column with empty cells for all existing rows (or row 1 if none)."""
        table = self.get_extra_table(site)
        with self._connect() as conn:
            with conn.cursor() as cur:
                rows_to_fill = list(table.keys()) if table else [1]
                for row_num in rows_to_fill:
                    cur.execute(
                        "INSERT INTO vault_extras (site, key, value, row_num) VALUES (%s, %s, %s, %s)",
                        (site, col, "", row_num)
                    )
            conn.commit()

    def add_extra_row(self, site: str) -> int:
        """Add a blank row for all existing columns. Returns new row_num."""
        cols = self.get_extra_cols(site)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(row_num), 0) FROM vault_extras WHERE site = %s", (site,)
                )
                new_row = cur.fetchone()[0] + 1
                for col in cols:
                    cur.execute(
                        "INSERT INTO vault_extras (site, key, value, row_num) VALUES (%s, %s, %s, %s)",
                        (site, col, "", new_row)
                    )
            conn.commit()
        return new_row

    def delete_extra_col(self, site: str, col: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM vault_extras WHERE site = %s AND key = %s", (site, col))
            conn.commit()

    def delete_extra_row(self, site: str, row_num: int):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM vault_extras WHERE site = %s AND row_num = %s", (site, row_num))
            conn.commit()

    def has_extras(self, site: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM vault_extras WHERE site = %s LIMIT 1", (site,))
                return cur.fetchone() is not None

    def get_extras(self, site: str) -> List[Dict]:
        """Compat shim: returns list of row dicts for extras_count in edit_pick_inline."""
        return list(self.get_extra_table(site).values())
