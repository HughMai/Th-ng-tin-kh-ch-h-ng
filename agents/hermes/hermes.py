import os
import logging
import anthropic
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("HERMES_LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL")
MODEL = os.getenv("HERMES_MODEL", "claude-sonnet-4-6")
PORT = int(os.getenv("PORT", 8080))

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

ALLOWED_CHAT_IDS = set()
raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
if raw:
    ALLOWED_CHAT_IDS = {int(i.strip()) for i in raw.split(",") if i.strip()}

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


ptb_app = Application.builder().token(TELEGRAM_TOKEN).updater(None).build()
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(CommandHandler("clear", clear))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ptb_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    await ptb_app.initialize()
    await ptb_app.start()
    logger.info(f"Hermes online | model={MODEL} | webhook={WEBHOOK_URL}/webhook")
    yield
    await ptb_app.stop()
    await ptb_app.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)
