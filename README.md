# 🔐 Telegram Password Vault Bot

A private, encrypted password manager that lives in your Telegram. Only you can access it. Passwords are encrypted at rest and auto-delete from chat after 30 seconds.

---

## Features

- **Menu-driven UI** — persistent Menu button with inline keyboard, no commands to memorise
- **Search** — partial match search across all your saved sites
- **Tappable list** — browse all entries and tap to retrieve
- **Master password gate** — even if someone has your Telegram, they can't see passwords without the master password
- **Excel import** — bulk import from a `.xlsx` file
- **Auto-delete** — retrieved passwords vanish from chat after 30 seconds
- **Encrypted at rest** — AES-128 via Fernet, keyed with PBKDF2 (480,000 iterations) from your master password
- **Postgres storage** — persistent via Supabase, survives redeploys

---

## Setup

### 1. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** (looks like `123456789:ABCdef...`)

### 2. Get your Telegram user ID

1. Message [@userinfobot](https://t.me/userinfobot)
2. Copy your numeric **user ID** (e.g. `987654321`)

This locks the bot so only you can use it — it will silently ignore everyone else.

### 3. Set up Supabase

1. Create a free project at [supabase.com](https://supabase.com)
2. Go to **Connect → Direct → Session pooler** and copy the connection string
3. Replace `[YOUR-PASSWORD]` with your database password

> The connection string looks like:
> `postgresql://postgres.xxxx:yourpassword@aws-0-region.pooler.supabase.com:5432/postgres`

### 4. Deploy to Render

1. Push this repo to GitHub
2. Create a new **Web Service** on [render.com](https://render.com) pointing to your repo
3. Set **Branch** to `main`, **Build Command** to `pip install -r requirements.txt`, **Start Command** to `python bot.py`
4. Add these environment variables:

| Variable | Value |
|---|---|
| `BOT_TOKEN` | Token from BotFather |
| `ALLOWED_USER_ID` | Your Telegram numeric user ID |
| `MASTER_PASSWORD` | A strong passphrase you choose — used for encryption |
| `DATABASE_URL` | Your Supabase session pooler connection string |

5. Deploy — the bot will start and create the database table automatically.

---

## Usage

Send `/start` to your bot to initialise the Menu button, then tap **🔐 Menu** at any time.

### Menu options

| Button | Description |
|---|---|
| ➕ Add | Add a new entry (guided steps) |
| 🔍 Search | Search by partial site name |
| 📋 List | Browse all saved sites as tappable buttons |
| 🗑️ Delete | Delete an entry by name |
| 📥 Import Excel | Bulk import from a `.xlsx` file |

### Adding a password

```
Tap ➕ Add
→ Bot: What is the site or app name?
→ You: netflix
→ Bot: Username or email?
→ You: me@email.com
→ Bot: Password? (your message will be deleted immediately)
→ You: mypassword123   ← deleted instantly
→ Bot: ✅ Saved netflix.
```

### Retrieving a password

```
Tap 🔍 Search → type "net"
→ Bot shows [netflix] button
→ Tap netflix
→ Bot: Enter master password (deleted immediately)
→ You: ••••••••
→ Bot: 🔑 netflix
       👤 me@email.com
       🔒 mypassword123
       (self-deletes in 30 seconds)
```

### Importing from Excel

Your spreadsheet needs these columns (header row required):

| Column | Required |
|---|---|
| `name` | ✅ |
| `username` | ✅ |
| `password` | ✅ |
| `type` | optional |
| `website` | optional |

Tap **📥 Import Excel**, then send the `.xlsx` file. The bot will encrypt and upsert all rows, and report how many were imported.

---

## Security

- **Single-user** — the bot checks your Telegram user ID on every message. No multi-user, no admin panel.
- **Encryption** — passwords are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before being stored. The key is derived from your master password via PBKDF2 with 480,000 iterations.
- **Master password prompt** — retrieving any password requires typing your master password in chat, which is deleted immediately.
- **Auto-delete** — decrypted passwords are removed from Telegram after 30 seconds.
- **No plaintext storage** — even with full database access, passwords cannot be read without the master password.

> ⚠️ **Your master password is not stored anywhere.** If you lose it, your encrypted passwords cannot be recovered. Keep it somewhere safe.

---

## Adding columns later

The vault table in Supabase can be extended at any time without breaking existing data:

```sql
ALTER TABLE vault ADD COLUMN IF NOT EXISTS totp_secret TEXT DEFAULT '';
```

No code changes needed for storage — the extra column is preserved on upsert.
