import json
import os
import time
import threading
import asyncio
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

# ---------------- CONFIG ----------------

TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

CACHE = []
CACHE_TIME = 0
TTL = 300


# ---------------- HEALTH SERVER ----------------

def run_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

    port = int(os.getenv("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


# ---------------- DATA ----------------

def get_rows():
    global CACHE, CACHE_TIME

    if not GOOGLE_CREDENTIALS:
        print("ERROR: GOOGLE_CREDENTIALS is empty")
        return []

    now = time.time()
    if CACHE and now - CACHE_TIME < TTL:
        return CACHE

    creds = json.loads(GOOGLE_CREDENTIALS)

    client = gspread.authorize(
        Credentials.from_service_account_info(
            creds,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
    )

    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    rows = sheet.get_all_records()

    clean = []
    for i, r in enumerate(rows):
        if r.get("product") and r.get("section"):
            r["_id"] = i
            clean.append(r)

    CACHE = clean
    CACHE_TIME = now
    return CACHE


# ---------------- UTILS ----------------

def norm(t):
    return str(t).lower().strip()


def h(t):
    return escape(str(t))


def icon(status):
    s = norm(status)

    if "готов" in s:
        return "🟢"
    if "ревью" in s:
        return "👀"
    if "работ" in s:
        return "🛠️"
    if "холд" in s:
        return "⏸️"
    if "архив" in s:
        return "📦"

    return "▫️"


# ---------------- SEARCH ----------------

def search(query):
    rows = get_rows()
    words = norm(query).split()

    res = []

    for r in rows:
        text = " ".join([
            norm(r.get("product", "")),
            norm(r.get("section", "")),
            norm(r.get("scenario", "")),
            norm(r.get("screen", "")),
            norm(r.get("keywords", "")),
            norm(r.get("status", "")),
        ])

        if all(w in text for w in words):
            res.append(r)

    return res


# ---------------- UI ----------------

def menu():
    return ReplyKeyboardMarkup(
        [
            ["🔍 Найти макет", "📚 Открыть каталог"],
            ["❓ FAQ", "💬 Связаться"],
        ],
        resize_keyboard=True
    )


def format_row(r):
    return (
        f"🖼 {h(r.get('screen','Без названия'))}\n\n"
        f"🩵 Продукт: {h(r.get('product','-'))}\n"
        f"📂 Раздел: {h(r.get('section','-'))}\n"
        f"{icon(r.get('status',''))} Статус: {h(r.get('status','-'))}\n\n"
        f"🕑 {h(r.get('updated_at','-'))}"
    )


def kb_row(r):
    kb = []

    if r.get("screen_url"):
        kb.append([InlineKeyboardButton("🖼 Открыть экран", url=r["screen_url"])])

    if r.get("scenario_url"):
        kb.append([InlineKeyboardButton("📂 Открыть сценарий", url=r["scenario_url"])])

    return InlineKeyboardMarkup(kb)


# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я помогу найти макеты.",
        reply_markup=menu()
    )


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text

    if q == "🔍 Найти макет":
        await update.message.reply_text("Напиши запрос")
        return

    if q == "📚 Открыть каталог":
        rows = get_rows()

        products = sorted({str(r.get("product")) for r in rows if r.get("product")})

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(p, callback_data=f"product|{p}")]
            for p in products
        ])

        await update.message.reply_text("📚 Каталог", reply_markup=kb)
        return

    if q == "❓ FAQ":
        await update.message.reply_text(
            "Раздел FAQ\n\n"
            "🔎 Если макет не найден — попробуй другое слово\n\n"
            "🧠 Если ты “точно его видел” — он мог переехать или быть переименован\n\n"
            "🔐 Нет доступа — запроси его стандартным способом\n\n"
            "💬 Если ничего не помогает — @G2_Schrodinger"
        )
        return

    if q == "💬 Связаться":
        await update.message.reply_text("@G2_Schrodinger")
        return

    res = search(q)

    if not res:
        await update.message.reply_text("Ничего не найдено", reply_markup=menu())
        return

    await update.message.reply_text("Нашла:")

    for r in res[:5]:
        await update.message.reply_text(
            format_row(r),
            reply_markup=kb_row(r)
        )


# ---------------- CALLBACK ----------------

async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    rows = get_rows()

    if data == "catalog":
        products = sorted({str(r.get("product")) for r in rows if r.get("product")})

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(p, callback_data=f"product|{p}")]
            for p in products
        ])

        await q.edit_message_text("📚 Каталог", reply_markup=kb)
        return

    if data.startswith("product|"):
        product = data.split("|", 1)[1]

        sections = sorted({
            r.get("section")
            for r in rows
            if r.get("product") == product
        })

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(s, callback_data=f"section|{product}|{i}")]
            for i, s in enumerate(sections)
        ] + [[InlineKeyboardButton("← Назад", callback_data="catalog")]])

        await q.edit_message_text(f"📚 {product}", reply_markup=kb)
        return

    if data.startswith("section|"):
        _, product, i = data.split("|")

        sections = sorted({
            r.get("section")
            for r in rows
            if r.get("product") == product
        })

        section = sections[int(i)]

        items = [
            r for r in rows
            if r.get("product") == product and r.get("section") == section
        ]

        scenarios = {}

        for r in items:
            scenarios.setdefault(r.get("scenario", "Без сценария"), []).append(r)

        text = f"📚 {h(product)} → {h(section)}\n\n"

        for sc, lst in scenarios.items():
            text += f"📂 {h(sc)}\n"

            for r in lst:
                text += f"   └ {icon(r.get('status',''))} {h(r.get('screen',''))}\n"

            text += "\n"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("← Назад", callback_data=f"product|{product}")],
            [InlineKeyboardButton("← В каталог", callback_data="catalog")]
        ])

        await q.edit_message_text(text, reply_markup=kb)


# ---------------- WEBHOOK CLEANUP ----------------

async def post_init(app):
    for i in range(3):
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
            print("Webhook cleared")
            return
        except Exception as e:
            print(f"Webhook attempt {i}: {e}")
            await asyncio.sleep(2)


# ---------------- MAIN ----------------

def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(catalog))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_handler))

    print("🚀 BOT STARTED")

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
