import os
import re
from typing import Optional, List, Dict
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_SYSTEM_COLS = {"site", "created_at", "updated_at"}


class Database:
    def __init__(self, url: str = DATABASE_URL):
        self.url = url
        self._conn = None
        self._init()

    def _connect(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.url)
        return self._conn

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
                # older deployments may have columns (e.g. username) left over
                # as NOT NULL with no default from before DEFAULT '' was added;
                # normalize every non-system column so partial saves never fail
                cur.execute("""
                    DO $$
                    DECLARE col text;
                    BEGIN
                        FOR col IN
                            SELECT column_name FROM information_schema.columns
                            WHERE table_name = 'vault'
                              AND column_name NOT IN ('site', 'created_at', 'updated_at')
                        LOOP
                            EXECUTE format('ALTER TABLE vault ALTER COLUMN %I DROP NOT NULL', col);
                            EXECUTE format('ALTER TABLE vault ALTER COLUMN %I SET DEFAULT %L', col, '');
                        END LOOP;
                    END $$;
                """)
                # column definitions per site
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS vault_extra_cols (
                        id        SERIAL PRIMARY KEY,
                        site      TEXT NOT NULL REFERENCES vault(site) ON DELETE CASCADE,
                        col_name  TEXT NOT NULL,
                        position  INT  NOT NULL DEFAULT 0,
                        UNIQUE (site, col_name)
                    )
                """)
                # cell data: one row per (site, row_num, col_name)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS vault_extras (
                        id       SERIAL PRIMARY KEY,
                        site     TEXT NOT NULL REFERENCES vault(site) ON DELETE CASCADE,
                        row_num  INT  NOT NULL DEFAULT 1,
                        col_name TEXT NOT NULL DEFAULT '',
                        value    TEXT NOT NULL DEFAULT '',
                        UNIQUE (site, row_num, col_name)
                    )
                """)
                # migrate old key/value schema if needed
                cur.execute("""
                    ALTER TABLE vault_extras
                        ADD COLUMN IF NOT EXISTS row_num  INT  NOT NULL DEFAULT 1,
                        ADD COLUMN IF NOT EXISTS col_name TEXT NOT NULL DEFAULT ''
                """)
                # older deployments have a legacy NOT NULL "key" column unused by new code
                cur.execute("""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'vault_extras' AND column_name = 'key'
                        ) THEN
                            ALTER TABLE vault_extras ALTER COLUMN key DROP NOT NULL;
                        END IF;
                    END $$;
                """)
                # older deployments may predate this constraint; add it if missing
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint WHERE conname = 'vault_extras_site_row_num_col_name_key'
                        ) THEN
                            ALTER TABLE vault_extras
                                ADD CONSTRAINT vault_extras_site_row_num_col_name_key
                                UNIQUE (site, row_num, col_name);
                        END IF;
                    END $$;
                """)
                # allow renaming a vault entry (UPDATE site) to cascade into
                # extras tables instead of being blocked by the FK
                for table in ("vault_extra_cols", "vault_extras"):
                    cur.execute(f"""
                        ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_site_fkey
                    """)
                    cur.execute(f"""
                        ALTER TABLE {table}
                            ADD CONSTRAINT {table}_site_fkey
                            FOREIGN KEY (site) REFERENCES vault(site)
                            ON UPDATE CASCADE ON DELETE CASCADE
                    """)
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

    def rename_entry(self, old_site: str, new_site: str) -> bool:
        """Renames a vault entry's site key; extras cascade via ON UPDATE CASCADE."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE vault SET site = %s WHERE site = %s", (new_site, old_site))
                renamed = cur.rowcount > 0
            conn.commit()
        return renamed

    # ── Extras ────────────────────────────────────────────────

    def get_extra_cols(self, site: str) -> List[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT col_name FROM vault_extra_cols WHERE site = %s ORDER BY position, id",
                    (site,)
                )
                return [r[0] for r in cur.fetchall()]

    def add_extra_col(self, site: str, col_name: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM vault_extra_cols WHERE site = %s",
                    (site,)
                )
                pos = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO vault_extra_cols (site, col_name, position) VALUES (%s, %s, %s)"
                    " ON CONFLICT (site, col_name) DO NOTHING",
                    (site, col_name, pos)
                )
            conn.commit()

    def delete_extra_col(self, site: str, col_name: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM vault_extra_cols WHERE site = %s AND col_name = %s",
                    (site, col_name)
                )
                cur.execute(
                    "DELETE FROM vault_extras WHERE site = %s AND col_name = %s",
                    (site, col_name)
                )
            conn.commit()

    def get_extra_rows(self, site: str) -> Dict[int, Dict[str, str]]:
        """Returns {row_num: {col_name: value}}."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT row_num, col_name, value FROM vault_extras WHERE site = %s ORDER BY row_num",
                    (site,)
                )
                rows: Dict[int, Dict[str, str]] = {}
                for row_num, col_name, value in cur.fetchall():
                    rows.setdefault(row_num, {})[col_name] = value
                return rows

    def add_extra_row(self, site: str) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(row_num), 0) + 1 FROM vault_extras WHERE site = %s",
                    (site,)
                )
                new_row = cur.fetchone()[0]
            conn.commit()
        return new_row

    def set_extra_cell(self, site: str, row_num: int, col_name: str, value: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO vault_extras (site, row_num, col_name, value) VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (site, row_num, col_name) DO UPDATE SET value = EXCLUDED.value",
                    (site, row_num, col_name, value)
                )
            conn.commit()

    def delete_extra_row(self, site: str, row_num: int):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM vault_extras WHERE site = %s AND row_num = %s",
                    (site, row_num)
                )
            conn.commit()

    def get_extras(self, site: str) -> List[Dict]:
        """Returns row count as list for edit_pick_inline extras_count."""
        rows = self.get_extra_rows(site)
        return list(rows.keys())
