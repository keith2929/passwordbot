import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
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


def is_allowed(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "🔐 *Password Vault Bot*\n\n"
        "Commands:\n"
        "/add — add or update an entry\n"
        "/get <site> — retrieve a password\n"
        "/list — list all saved sites\n"
        "/delete <site> — delete an entry\n"
        "/cancel — cancel current operation",
        parse_mode="Markdown"
    )


async def get_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /get <site>")
        return
    site = " ".join(ctx.args).strip().lower()
    entry = db.get_entry(site)
    if not entry:
        await update.message.reply_text(f"❌ No entry found for *{site}*.", parse_mode="Markdown")
        return
    username, enc_pass = entry
    password = decrypt(MASTER_PASSWORD, enc_pass)
    msg = await update.message.reply_text(
        f"🔑 *{site}*\n"
        f"👤 `{username}`\n"
        f"🔒 `{password}`\n\n"
        "_This message will self-delete in 30 seconds._",
        parse_mode="Markdown"
    )

    async def _delete(context: ContextTypes.DEFAULT_TYPE):
        try:
            await context.bot.delete_message(msg.chat_id, msg.message_id)
        except Exception:
            pass

    ctx.job_queue.run_once(_delete, when=30)


async def list_sites(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    sites = db.list_sites()
    if not sites:
        await update.message.reply_text("No entries saved yet. Use /add to get started.")
        return
    lines = "\n".join(f"• {s}" for s in sites)
    await update.message.reply_text(f"📋 *Saved sites:*\n{lines}", parse_mode="Markdown")


async def delete_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /delete <site>")
        return
    site = " ".join(ctx.args).strip().lower()
    if db.delete_entry(site):
        await update.message.reply_text(f"🗑️ Deleted entry for *{site}*.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ No entry found for *{site}*.", parse_mode="Markdown")


async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return ConversationHandler.END
    await update.message.reply_text("What is the site or app name? (e.g. Gmail, Netflix)")
    return SITE


async def add_site(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["site"] = update.message.text.strip().lower()
    await update.message.reply_text("What is the username or email?")
    return USERNAME


async def add_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["username"] = update.message.text.strip()
    await update.message.reply_text(
        "What is the password?\n_Your message will be deleted immediately._",
        parse_mode="Markdown"
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
        f"✅ Saved entry for *{site}*.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


def main():
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            SITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_site)],
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_username)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("get", get_password))
    app.add_handler(CommandHandler("list", list_sites))
    app.add_handler(CommandHandler("delete", delete_entry))
    app.add_handler(add_conv)

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
