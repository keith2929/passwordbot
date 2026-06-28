import os
import io
import logging
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import openpyxl

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)
from db import Database
from crypto import encrypt, decrypt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
MASTER_PASSWORD = os.environ["MASTER_PASSWORD"]

db = Database()

# Conversation states
SITE, USERNAME, PASSWORD = range(3)
DELETE_SITE    = 3
MASTER_CONFIRM = 4
SEARCH         = 5
ACTION         = 6
EDIT_PICK      = 7
EDIT_VALUE     = 8
ADD_COL_NAME   = 9
EXTRAS_MENU       = 10
EXTRAS_ADD_KEY    = 11
EXTRAS_ADD_VAL    = 12
EXTRAS_ROW        = 13
EXTRAS_EDIT_FIELD = 14

MENU_KB = ReplyKeyboardMarkup([["🔐 Menu"]], resize_keyboard=True)

_SENSITIVE_COLS = {"password"}

_COL_EMOJI = {
    "username": "👤",
    "password": "🔒",
    "type":     "🏷️",
    "website":  "🌐",
    "notes":    "📝",
}


def is_allowed(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID


def menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add",         callback_data="add"),
         InlineKeyboardButton("🔍 Search",      callback_data="search")],
        [InlineKeyboardButton("📋 List",         callback_data="list"),
         InlineKeyboardButton("🗑️ Delete",       callback_data="delete")],
        [InlineKeyboardButton("📥 Import Excel", callback_data="import"),
         InlineKeyboardButton("⚙️ Columns",      callback_data="columns")],
    ])


def sites_inline(sites: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(s, callback_data=f"site:{s}")] for s in sites]
    )


def action_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Reveal",  callback_data="action_reveal"),
         InlineKeyboardButton("✏️ Edit",    callback_data="action_edit")],
    ])


def edit_pick_inline(entry: dict, edits: dict, extras_count: int) -> InlineKeyboardMarkup:
    buttons = []
    for col, val in entry.items():
        current = edits.get(col, val) or ""
        emoji = _COL_EMOJI.get(col, "•")
        if col in _SENSITIVE_COLS:
            label = f"{emoji} {col}: {'(changed)' if col in edits else '••••••••'}"
        else:
            preview = (current[:20] + "…") if len(current) > 20 else current
            label = f"{emoji} {col}: {preview or '(empty)'}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"editcol:{col}")])
    extras_label = f"📎 Extras ({extras_count})" if extras_count else "📎 Extras (none)"
    buttons.append([InlineKeyboardButton(extras_label, callback_data="edit_extras")])
    buttons.append([InlineKeyboardButton("✅ Save Changes", callback_data="edit_save")])
    return InlineKeyboardMarkup(buttons)


def extras_table_inline(rows: list) -> InlineKeyboardMarkup:
    buttons = []
    for i, r in enumerate(rows, 1):
        q = (r["key"][:20] + "…") if len(r["key"]) > 20 else r["key"]
        a = (r["value"][:20] + "…") if len(r["value"]) > 20 else r["value"]
        label = f"{i}. {q} → {a or '(empty)'}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"extras_row:{r['id']}")])
    buttons.append([InlineKeyboardButton("➕ Add Row", callback_data="extras_add_row")])
    buttons.append([InlineKeyboardButton("← Back to Edit", callback_data="extras_back")])
    return InlineKeyboardMarkup(buttons)


def extras_row_inline(row: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Question", callback_data=f"extras_editkey:{row['id']}"),
         InlineKeyboardButton("✏️ Edit Answer",   callback_data=f"extras_editval:{row['id']}")],
        [InlineKeyboardButton("🗑️ Delete Row",    callback_data=f"extras_delrow:{row['id']}")],
        [InlineKeyboardButton("← Back to Extras", callback_data="extras_menu_back")],
    ])


# ── START / MENU ──────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "🔐 *Password Vault*\nTap Menu to get started.",
        parse_mode="Markdown",
        reply_markup=MENU_KB,
    )


async def show_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text("Choose an action:", reply_markup=menu_inline())


# ── ADD ───────────────────────────────────────────────────

async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("What is the site or app name?")
    else:
        await update.message.reply_text("What is the site or app name?")
    return SITE


async def add_site(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["site"] = update.message.text.strip().lower()
    await update.message.reply_text("Username or email?")
    return USERNAME


async def add_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["username"] = update.message.text.strip()
    await update.message.reply_text(
        "Password?\n_Your message will be deleted immediately._",
        parse_mode="Markdown",
    )
    return PASSWORD


async def add_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    site = ctx.user_data["site"]
    db.save_entry(site, {
        "username": ctx.user_data["username"],
        "password": encrypt(MASTER_PASSWORD, password),
    })
    await ctx.bot.send_message(
        update.effective_chat.id,
        f"✅ Saved *{site}*.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── LIST ──────────────────────────────────────────────────

async def list_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        send = update.callback_query.message.reply_text
    else:
        if not is_allowed(update):
            return
        send = update.message.reply_text
    sites = db.list_sites()
    if not sites:
        await send("No entries saved yet.")
    else:
        await send(
            "📋 *Tap a site to retrieve its password:*",
            parse_mode="Markdown",
            reply_markup=sites_inline(sites),
        )


# ── COLUMNS MANAGEMENT ────────────────────────────────────

async def columns_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cols = db.get_columns()
    text = "⚙️ *Current columns:*\n" + "\n".join(f"• {c}" for c in cols)
    await update.callback_query.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Column", callback_data="add_column")]
        ]),
    )


async def add_column_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Enter the new column name (letters, numbers, underscores only):"
    )
    return ADD_COL_NAME


async def add_column_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    try:
        clean = db.add_column(name)
        await update.message.reply_text(f"✅ Column *{clean}* added.", parse_mode="Markdown")
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    return ConversationHandler.END


# ── SEARCH ────────────────────────────────────────────────

async def search_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return ConversationHandler.END
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("🔍 Type a site name to search:")
    return SEARCH


async def search_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip().lower()
    results = db.search_sites(query)
    if not results:
        await update.message.reply_text(f"❌ No matches for *{query}*.", parse_mode="Markdown")
        return ConversationHandler.END
    if len(results) == 1:
        return await _ask_master(update.message.reply_text, ctx, results[0])
    await update.message.reply_text(
        f"Found {len(results)} matches — tap one:",
        reply_markup=sites_inline(results),
    )
    return ConversationHandler.END


# ── SITE SELECTED → MASTER PASSWORD ───────────────────────

async def site_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    site = update.callback_query.data.split(":", 1)[1]
    return await _ask_master(update.callback_query.message.reply_text, ctx, site)


async def _ask_master(send_fn, ctx, site: str):
    ctx.user_data["pending_site"] = site
    ctx.user_data["edits"] = {}
    msg = await send_fn(
        "🔑 Enter master password:\n_Message will be deleted immediately._",
        parse_mode="Markdown",
    )
    ctx.user_data["prompt_msg_id"] = msg.message_id
    return MASTER_CONFIRM


async def master_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    typed = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    try:
        await ctx.bot.delete_message(update.effective_chat.id, ctx.user_data.get("prompt_msg_id"))
    except Exception:
        pass

    if typed != MASTER_PASSWORD:
        await ctx.bot.send_message(update.effective_chat.id, "❌ Wrong master password.")
        return ConversationHandler.END

    site = ctx.user_data["pending_site"]
    entry = db.get_entry(site)
    if not entry:
        await ctx.bot.send_message(
            update.effective_chat.id, f"❌ No entry for *{site}*.", parse_mode="Markdown"
        )
        return ConversationHandler.END

    ctx.user_data["entry"] = entry
    await ctx.bot.send_message(
        update.effective_chat.id,
        f"🔐 *{site}* — verified. What would you like to do?",
        parse_mode="Markdown",
        reply_markup=action_inline(),
    )
    return ACTION


# ── REVEAL ────────────────────────────────────────────────

async def action_reveal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    site = ctx.user_data["pending_site"]
    entry = ctx.user_data["entry"]
    extras = db.get_extras(site)

    lines = [f"🔑 *{site}*"]
    for col, val in entry.items():
        if not val:
            continue
        emoji = _COL_EMOJI.get(col, "•")
        if col == "password":
            lines.append(f"{emoji} `{decrypt(MASTER_PASSWORD, val)}`")
        else:
            lines.append(f"{emoji} {col}: `{val}`")

    if extras:
        lines.append("\n📎 *Extras — refer to table:*")
        for ex in extras:
            lines.append(f"  • {ex['key']}: `{ex['value']}`")

    lines.append("\n_Self-deletes in 30s_")

    msg = await update.callback_query.message.reply_text(
        "\n".join(lines), parse_mode="Markdown"
    )

    async def _delete(context: ContextTypes.DEFAULT_TYPE):
        try:
            await context.bot.delete_message(msg.chat_id, msg.message_id)
        except Exception:
            pass

    ctx.job_queue.run_once(_delete, when=30)
    return ConversationHandler.END


# ── EDIT ──────────────────────────────────────────────────

async def action_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_edit_pick(update.callback_query.message.reply_text, ctx)


async def _show_edit_pick(send_fn, ctx):
    site = ctx.user_data["pending_site"]
    entry = ctx.user_data["entry"]
    edits = ctx.user_data.get("edits", {})
    extras = db.get_extras(site)
    msg = await send_fn(
        f"✏️ Editing *{site}* — tap a field to change it:",
        parse_mode="Markdown",
        reply_markup=edit_pick_inline(entry, edits, len(extras)),
    )
    if hasattr(msg, "message_id"):
        ctx.user_data["pick_msg_id"] = msg.message_id
    return EDIT_PICK


async def edit_col_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    col = update.callback_query.data.split(":", 1)[1]
    ctx.user_data["editing_col"] = col
    if col in _SENSITIVE_COLS:
        prompt = f"Enter new value for *{col}*:\n_Message will be deleted immediately._"
    else:
        current = ctx.user_data["edits"].get(col) or ctx.user_data["entry"].get(col, "")
        prompt = f"Enter new value for *{col}*:\n_Current: {current or '(empty)'}_"
    await update.callback_query.message.reply_text(prompt, parse_mode="Markdown")
    return EDIT_VALUE


async def edit_value_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    col = ctx.user_data["editing_col"]
    value = update.message.text.strip()
    if col in _SENSITIVE_COLS:
        try:
            await update.message.delete()
        except Exception:
            pass
        value = encrypt(MASTER_PASSWORD, value)
    ctx.user_data["edits"][col] = value
    # Edit the existing pick message in place so old Save buttons stay valid
    site = ctx.user_data["pending_site"]
    entry = ctx.user_data["entry"]
    edits = ctx.user_data["edits"]
    extras = db.get_extras(site)
    pick_msg_id = ctx.user_data.get("pick_msg_id")
    if pick_msg_id:
        try:
            await ctx.bot.edit_message_reply_markup(
                chat_id=update.effective_chat.id,
                message_id=pick_msg_id,
                reply_markup=edit_pick_inline(entry, edits, len(extras)),
            )
            return EDIT_PICK
        except Exception:
            pass
    return await _show_edit_pick(update.message.reply_text, ctx)


async def edit_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    site = ctx.user_data["pending_site"]
    edits = ctx.user_data.get("edits", {})
    if not edits:
        await update.callback_query.message.reply_text("No changes made.")
        return ConversationHandler.END
    db.save_entry(site, edits)
    await update.callback_query.message.reply_text(
        f"✅ *{site}* updated ({len(edits)} field{'s' if len(edits) != 1 else ''} changed).",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── EXTRAS ────────────────────────────────────────────────

def _extras_text(site: str, rows: list) -> str:
    if not rows:
        return f"📊 *Extras for {site}*\n_No rows yet. Tap ➕ Add Row to get started._"
    lines = [f"📊 *Extras for {site}*\n"]
    lines.append("`No  Question              Answer`")
    lines.append("`" + "─" * 38 + "`")
    for i, r in enumerate(rows, 1):
        q = (r["key"][:20] + "…") if len(r["key"]) > 20 else r["key"]
        a = (r["value"][:20] + "…") if len(r["value"]) > 20 else r["value"]
        lines.append(f"`{str(i).ljust(4)}{q.ljust(22)}{a}`")
    return "\n".join(lines)


async def _show_extras_menu(send_fn, ctx):
    site = ctx.user_data["pending_site"]
    rows = db.get_extras(site)
    text = _extras_text(site, rows)
    await send_fn(text, parse_mode="Markdown", reply_markup=extras_table_inline(rows))
    return EXTRAS_MENU


async def edit_extras(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_extras_menu(update.callback_query.message.reply_text, ctx)


async def extras_row_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    extra_id = int(update.callback_query.data.split(":", 1)[1])
    site = ctx.user_data["pending_site"]
    rows = db.get_extras(site)
    row = next((r for r in rows if r["id"] == extra_id), None)
    if not row:
        return await _show_extras_menu(update.callback_query.message.reply_text, ctx)
    ctx.user_data["extras_row"] = row
    await update.callback_query.message.reply_text(
        f"*Question:* {row['key'] or '(empty)'}\n*Answer:* {row['value'] or '(empty)'}",
        parse_mode="Markdown",
        reply_markup=extras_row_inline(row),
    )
    return EXTRAS_ROW


async def extras_edit_key_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    extra_id = int(update.callback_query.data.split(":", 1)[1])
    ctx.user_data["extras_editing_id"] = extra_id
    ctx.user_data["extras_editing_field"] = "key"
    row = ctx.user_data["extras_row"]
    await update.callback_query.message.reply_text(
        f"Enter new *Question*:\n_Current: {row['key'] or '(empty)'}_",
        parse_mode="Markdown",
    )
    return EXTRAS_EDIT_FIELD


async def extras_edit_val_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    extra_id = int(update.callback_query.data.split(":", 1)[1])
    ctx.user_data["extras_editing_id"] = extra_id
    ctx.user_data["extras_editing_field"] = "value"
    row = ctx.user_data["extras_row"]
    await update.callback_query.message.reply_text(
        f"Enter new *Answer*:\n_Current: {row['value'] or '(empty)'}_",
        parse_mode="Markdown",
    )
    return EXTRAS_EDIT_FIELD


async def extras_edit_field_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    extra_id = ctx.user_data["extras_editing_id"]
    field = ctx.user_data["extras_editing_field"]
    new_val = update.message.text.strip()
    row = ctx.user_data["extras_row"].copy()
    row[field] = new_val
    ctx.user_data["extras_row"] = row
    db.update_extra(extra_id, row["key"], row["value"])
    await update.message.reply_text("✅ Updated.")
    return await _show_extras_menu(update.message.reply_text, ctx)


async def extras_del_row(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    extra_id = int(update.callback_query.data.split(":", 1)[1])
    db.delete_extra(extra_id)
    await update.callback_query.message.reply_text("🗑️ Row deleted.")
    return await _show_extras_menu(update.callback_query.message.reply_text, ctx)


async def extras_add_row_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "Enter the *Question* (e.g. _What is your pet's name?_):",
        parse_mode="Markdown",
    )
    return EXTRAS_ADD_KEY


async def extras_add_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["extras_new_key"] = update.message.text.strip()
    await update.message.reply_text(
        f"Now enter the *Answer*:",
        parse_mode="Markdown",
    )
    return EXTRAS_ADD_VAL


async def extras_add_val(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    site = ctx.user_data["pending_site"]
    key = ctx.user_data["extras_new_key"]
    value = update.message.text.strip()
    db.add_extra(site, key, value)
    await update.message.reply_text("✅ Row added.")
    return await _show_extras_menu(update.message.reply_text, ctx)


async def extras_menu_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_extras_menu(update.callback_query.message.reply_text, ctx)


async def extras_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    return await _show_edit_pick(update.callback_query.message.reply_text, ctx)


# ── DELETE ────────────────────────────────────────────────

async def delete_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("Which site to delete?")
    else:
        await update.message.reply_text("Which site to delete?")
    return DELETE_SITE


async def delete_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    site = update.message.text.strip().lower()
    if db.delete_entry(site):
        await update.message.reply_text(f"🗑️ Deleted *{site}*.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ No entry for *{site}*.", parse_mode="Markdown")
    return ConversationHandler.END


# ── IMPORT ────────────────────────────────────────────────

async def import_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cols = db.get_columns()
    await update.callback_query.message.reply_text(
        f"📥 Send me your Excel file (.xlsx)\n"
        f"*Required column:* name\n"
        f"*Available columns:* {', '.join(cols)}\n"
        f"Missing fields will be set to empty.",
        parse_mode="Markdown",
    )


async def import_excel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    doc = update.message.document
    if not doc.file_name.lower().endswith(".xlsx"):
        await update.message.reply_text("Please send a .xlsx file.")
        return
    await update.message.reply_text("⏳ Processing...")
    file = await ctx.bot.get_file(doc.file_id)
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    buf.seek(0)
    wb = openpyxl.load_workbook(buf, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    raw_headers = next(rows)
    headers = [str(h).strip().lower() if h is not None else "" for h in raw_headers]
    col_index = {h: i for i, h in enumerate(headers)}
    if "name" not in col_index:
        await update.message.reply_text("❌ Missing required column: name")
        return
    db_cols = db.get_columns()
    imported = skipped = 0
    for row in rows:
        def cell(key):
            return str(row[col_index[key]]).strip() if key in col_index and row[col_index[key]] is not None else ""
        name = cell("name").lower()
        if not name:
            skipped += 1
            continue
        fields = {}
        for col in db_cols:
            if col == "password":
                raw = cell("password")
                fields["password"] = encrypt(MASTER_PASSWORD, raw) if raw else ""
            else:
                fields[col] = cell(col)
        db.save_entry(name, fields)
        imported += 1
    await update.message.reply_text(
        f"✅ Imported {imported} entr{'y' if imported == 1 else 'ies'}."
        + (f" Skipped {skipped} rows with no name." if skipped else "")
    )


# ── CANCEL ────────────────────────────────────────────────

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── HEALTH SERVER ─────────────────────────────────────────

def _start_health_server():
    port = int(os.environ.get("PORT", 8080))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


def main():
    threading.Thread(target=_start_health_server, daemon=True).start()
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            CallbackQueryHandler(add_start, pattern="^add$"),
        ],
        states={
            SITE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_site)],
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_username)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    col_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_column_start, pattern="^add_column$"),
        ],
        states={
            ADD_COL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_column_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    vault_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(site_selected, pattern="^site:"),
            CallbackQueryHandler(search_start,  pattern="^search$"),
        ],
        states={
            SEARCH:         [MessageHandler(filters.TEXT & ~filters.COMMAND, search_query)],
            MASTER_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, master_confirm)],
            ACTION: [
                CallbackQueryHandler(action_reveal, pattern="^action_reveal$"),
                CallbackQueryHandler(action_edit,   pattern="^action_edit$"),
            ],
            EDIT_PICK: [
                CallbackQueryHandler(edit_col_pick, pattern="^editcol:"),
                CallbackQueryHandler(edit_extras,   pattern="^edit_extras$"),
                CallbackQueryHandler(edit_save,     pattern="^edit_save$"),
            ],
            EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value_input),
            ],
            EXTRAS_MENU: [
                CallbackQueryHandler(extras_row_pick,      pattern="^extras_row:"),
                CallbackQueryHandler(extras_add_row_start, pattern="^extras_add_row$"),
                CallbackQueryHandler(extras_back,          pattern="^extras_back$"),
            ],
            EXTRAS_ROW: [
                CallbackQueryHandler(extras_edit_key_start, pattern="^extras_editkey:"),
                CallbackQueryHandler(extras_edit_val_start, pattern="^extras_editval:"),
                CallbackQueryHandler(extras_del_row,        pattern="^extras_delrow:"),
                CallbackQueryHandler(extras_menu_back,      pattern="^extras_menu_back$"),
            ],
            EXTRAS_EDIT_FIELD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, extras_edit_field_value),
            ],
            EXTRAS_ADD_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, extras_add_key),
            ],
            EXTRAS_ADD_VAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, extras_add_val),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    delete_conv = ConversationHandler(
        entry_points=[
            CommandHandler("delete", delete_start),
            CallbackQueryHandler(delete_start, pattern="^delete$"),
        ],
        states={
            DELETE_SITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_handler))
    app.add_handler(MessageHandler(filters.Text(["🔐 Menu"]), show_menu))
    app.add_handler(CallbackQueryHandler(list_handler,  pattern="^list$"))
    app.add_handler(CallbackQueryHandler(columns_menu,  pattern="^columns$"))
    app.add_handler(CallbackQueryHandler(import_prompt, pattern="^import$"))
    app.add_handler(MessageHandler(filters.Document.FileExtension("xlsx"), import_excel))
    app.add_handler(add_conv)
    app.add_handler(col_conv)
    app.add_handler(vault_conv)
    app.add_handler(delete_conv)

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
