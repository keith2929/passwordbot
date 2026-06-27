import os
import io
import logging
import asyncio
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

SITE, USERNAME, PASSWORD = range(3)
GET_SITE = 3
DELETE_SITE = 4

MENU_KB = ReplyKeyboardMarkup([["🔐 Menu"]], resize_keyboard=True)


def is_allowed(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID


def menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add", callback_data="add"),
         InlineKeyboardButton("🔍 Get", callback_data="get")],
        [InlineKeyboardButton("📋 List", callback_data="list"),
         InlineKeyboardButton("🗑️ Delete", callback_data="delete")],
        [InlineKeyboardButton("📥 Import Excel", callback_data="import")],
    ])


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
    username = ctx.user_data["username"]
    db.save_entry(site, username, encrypt(MASTER_PASSWORD, password))
    await ctx.bot.send_message(
        update.effective_chat.id,
        f"✅ Saved *{site}*.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── GET ───────────────────────────────────────────────────

async def get_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("Which site?")
    else:
        await update.message.reply_text("Which site?")
    return GET_SITE


async def get_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    site = update.message.text.strip().lower()
    entry = db.get_entry(site)
    if not entry:
        await update.message.reply_text(f"❌ No entry for *{site}*.", parse_mode="Markdown")
        return ConversationHandler.END
    username, enc_pass, type_, website, notes = entry
    password = decrypt(MASTER_PASSWORD, enc_pass)
    lines = [f"🔑 *{site}*", f"👤 `{username}`", f"🔒 `{password}`"]
    if type_:
        lines.append(f"🏷️ {type_}")
    if website:
        lines.append(f"🌐 {website}")
    if notes:
        lines.append(f"📝 {notes}")
    lines.append("\n_Self-deletes in 30s_")
    msg = await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _delete(context: ContextTypes.DEFAULT_TYPE):
        try:
            await context.bot.delete_message(msg.chat_id, msg.message_id)
        except Exception:
            pass

    ctx.job_queue.run_once(_delete, when=30)
    return ConversationHandler.END


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
        lines = "\n".join(f"• {s}" for s in sites)
        await send(f"📋 *Saved entries:*\n{lines}", parse_mode="Markdown")


# ── IMPORT ────────────────────────────────────────────────

async def import_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "📥 Send me your Excel file (.xlsx)\n"
        "Required columns: *name, username, password*\n"
        "Optional: *type, website*",
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
    col = {h: i for i, h in enumerate(headers)}
    required = {"name", "username", "password"}
    if not required.issubset(col):
        missing = required - set(col)
        await update.message.reply_text(
            f"❌ Missing columns: {', '.join(missing)}\nFound: {', '.join(h for h in headers if h)}"
        )
        return
    imported = skipped = 0
    for row in rows:
        def cell(key):
            return str(row[col[key]]).strip() if key in col and row[col[key]] is not None else ""
        name = cell("name").lower()
        username = cell("username")
        password = cell("password")
        if not name or not username or not password:
            skipped += 1
            continue
        db.save_entry(
            name, username, encrypt(MASTER_PASSWORD, password),
            type_=cell("type"), website=cell("website"),
        )
        imported += 1
    await update.message.reply_text(
        f"✅ Imported {imported} entr{'y' if imported == 1 else 'ies'}."
        + (f" Skipped {skipped} incomplete rows." if skipped else "")
    )


# ── CANCEL ────────────────────────────────────────────────

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def main():
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

    get_conv = ConversationHandler(
        entry_points=[
            CommandHandler("get", get_start),
            CallbackQueryHandler(get_start, pattern="^get$"),
        ],
        states={
            GET_SITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password)],
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
    app.add_handler(CallbackQueryHandler(list_handler, pattern="^list$"))
    app.add_handler(CallbackQueryHandler(import_prompt, pattern="^import$"))
    app.add_handler(MessageHandler(filters.Document.FileExtension("xlsx"), import_excel))
    app.add_handler(add_conv)
    app.add_handler(get_conv)
    app.add_handler(delete_conv)

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
