import json
import os
import threading
import time
import traceback
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer

import gspread
from google.oauth2.service_account import Credentials
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

ROWS_CACHE = []
LAST_UPDATE = 0
CACHE_TTL = 300


# ---------------- HEALTH ----------------

def start_health_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is alive")

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

    port = int(os.getenv("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


# ---------------- DATA ----------------

def get_rows():
    global ROWS_CACHE, LAST_UPDATE

    now = time.time()

    if ROWS_CACHE and now - LAST_UPDATE < CACHE_TTL:
        return ROWS_CACHE

    try:
        credentials_info = json.loads(GOOGLE_CREDENTIALS)
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

        credentials = Credentials.from_service_account_info(
            credentials_info,
            scopes=scopes
        )

        client = gspread.authorize(credentials)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        rows = sheet.get_all_records()

        cleaned = []

        for row in rows:
            if not row.get("product") or not row.get("section"):
                continue
            cleaned.append(row)

        for i, row in enumerate(cleaned):
            row["_id"] = i

        ROWS_CACHE = cleaned
        LAST_UPDATE = now

        print(f"[CACHE] loaded rows: {len(cleaned)}", flush=True)

        return ROWS_CACHE

    except Exception as e:
        print("❌ get_rows error:", e, flush=True)
        print(traceback.format_exc(), flush=True)

        return ROWS_CACHE


# ---------------- HELPERS ----------------

def normalize(text):
    return str(text).lower().strip()


def html(text):
    return escape(str(text))


def make_link(text, url):
    text = html(text)
    return f'<a href="{html(url)}">{text}</a>' if url else text


# ❗ ВАЖНО: ВЕРНУЛ ТВОЮ ЛОГИКУ СТАТУСОВ

def get_status_icon(status):
    status = normalize(status)

    if "готово" in status:
        return "✅"
    if "на ревью" in status:
        return "👀"
    if "в работе" in status:
        return "🛠️"
    if "холд" in status:
        return "⏸️"
    if "архив" in status:
        return "📦"

    return "▫️"


# ---------------- SEARCH ----------------

def search_makets(query):
    rows = get_rows()
    query_words = normalize(query).split()
    results = []

    for row in rows:
        text = " ".join([
            normalize(row.get("product", "")),
            normalize(row.get("section", "")),
            normalize(row.get("scenario", "")),
            normalize(row.get("screen", "")),
            normalize(row.get("keywords", "")),
            normalize(row.get("status", "")),
        ])

        if all(w in text for w in query_words):
            results.append(row)

    return results


# ---------------- FORMAT ----------------

def format_result(row):
    icon = get_status_icon(row.get("status", ""))

    return (
        f"🖼 {row.get('screen', 'Без названия')}\n\n\n"
        f"🩵 Продукт          {row.get('product', '-')}\n"
        f"📂 Раздел             {row.get('scenario', '-')}\n"
        f"{icon} Статус              {row.get('status', '-')}\n\n"
        f"🕑 Обновлено    {row.get('updated_at', '-')}"
    )


def result_keyboard(row):
    buttons = []

    if row.get("screen_url"):
        buttons.append([InlineKeyboardButton("Открыть сценарий", url=row["screen_url"])])

    if row.get("scenario_url"):
        buttons.append([InlineKeyboardButton("Открыть раздел", url=row["scenario_url"])])

    return InlineKeyboardMarkup(buttons)


# ---------------- UI ----------------

def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["🔍 Найти макет", "📚 Открыть каталог"],
            ["❓ FAQ", "💬 Связаться"],
        ],
        resize_keyboard=True
    )


# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я помогу найти макеты в Figma.",
        reply_markup=main_menu()
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.message.text
        print(f"Got message: {query}", flush=True)

        if query == "🔍 Найти макет":
            await update.message.reply_text("Напиши запрос…")
            return

        if query == "📚 Открыть каталог":
            await update.message.reply_text("Каталог скоро будет 🙂")
            return

        if query == "❓ FAQ":
            await update.message.reply_text("FAQ пока здесь 🙂")
            return

        if query == "💬 Связаться":
            await update.message.reply_text("👉 @G2_Schrodinger")
            return

        results = search_makets(query)

        if not results:
            await update.message.reply_text("😔 Ничего не нашла")
            return

        await update.message.reply_text("🎉 Кое-что нашлось:")

        for row in results[:5]:
            await update.message.reply_text(
                format_result(row),
                reply_markup=result_keyboard(row),
                parse_mode="HTML"
            )

    except Exception as e:
        print("❌ SEARCH ERROR:", e, flush=True)
        print(traceback.format_exc(), flush=True)

        await update.message.reply_text(
            "⚠️ Ошибка. Попробуй ещё раз или напиши в поддержку."
        )


# ---------------- RUN ----------------

def main():
    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    print("Bot polling started", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
