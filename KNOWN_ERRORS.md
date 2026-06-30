# Known errors

Log of root causes we've already hit, so future ones are faster to diagnose.
When the bot hits an unrecognized exception it will tell you so in chat
("Unrecognized error (...)") — add a row here and a matching entry to
`_KNOWN_ERRORS` in `bot.py` once you've diagnosed it.

| Exception | Cause | Fix |
|---|---|---|
| `psycopg2.errors.InvalidColumnReference: there is no unique or exclusion constraint matching the ON CONFLICT specification` | `vault_extras` table predated the `UNIQUE(site, row_num, col_name)` constraint added later in code. `CREATE TABLE IF NOT EXISTS` doesn't retrofit constraints onto an existing table. | Added a migration in `db.py`'s `_init()` that adds the constraint if missing. |
| `psycopg2.errors.NotNullViolation: null value in column "key"` | Legacy `vault_extras.key` column (from the old key/value schema) still had `NOT NULL`, but new code writes `col_name`/`value` and never populates `key`. | Added a migration that drops `NOT NULL` on `key` if the column exists. |
| `psycopg2.errors.NotNullViolation: null value in column "username" of relation "vault"` | Production `vault` table predated `DEFAULT ''` on `username`/etc., so when an imported CSV row omitted that field, the INSERT skipped the column and hit the old NOT NULL with no default. | Added a migration that loops every non-system `vault` column and drops NOT NULL + sets `DEFAULT ''`, so partial saves can never fail this way again (covers custom columns too). |

## How to extend

1. Read the traceback in the Render logs — note the exception class (e.g. `NotNullViolation`) and a distinctive substring of the message.
2. Add a row to the table above describing the cause and fix.
3. Add a tuple to `_KNOWN_ERRORS` in `bot.py`: `(exception_class_substring, message_substring, user_facing_hint)`. Leave `message_substring` as `""` to match on class alone.
4. Most causes here are migrations missing from `db.py`'s `_init()` — `CREATE TABLE IF NOT EXISTS` never updates an existing table's columns/constraints, so any schema change needs an explicit `ALTER TABLE ... IF NOT EXISTS`/`DO $$ ... $$` guard alongside it.
