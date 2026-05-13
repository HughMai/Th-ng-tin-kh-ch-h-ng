import os
import logging
from dotenv import load_dotenv
from anthropic import Anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("HERMES_LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.getenv("HERMES_MODEL", "claude-sonnet-4-6")

ALLOWED_CHAT_IDS = set()
raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
if raw:
    ALLOWED_CHAT_IDS = {int(id.strip()) for id in raw.split(",") if id.strip()}

conversation_history: dict[int, list] = {}

SYSTEM_PROMPT = """You are Hermes, an AI assistant for Hughie's business.
You help with outreach, client follow-ups, and business operations.
Be direct, concise, and action-oriented."""


def is_allowed(chat_id: int) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return chat_id in ALLOWED_CHAT_IDS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_chat.id):
        return
    await update.message.reply_text("Hermes online. What do you need?")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_chat.id):
        return
    conversation_history.pop(update.effective_chat.id, None)
    await update.message.reply_text("Conversation cleared.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_allowed(chat_id):
        return

    user_text = update.message.text
    history = conversation_history.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=history,
    )

    reply = response.content[0].text
    history.append({"role": "assistant", "content": reply})

    await update.message.reply_text(reply)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info(f"Hermes running with model {MODEL}")
    app.run_polling()


if __name__ == "__main__":
    main()
