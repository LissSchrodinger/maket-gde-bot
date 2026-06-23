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

    creds = json.loads(GOOGLE_CREDENTIALS)

    credentials = Credentials.from_service_account_info(
        creds,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )

    client = gspread.authorize(credentials)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1

    rows = sheet.get_all_records()

    cleaned = []
    for r in rows:
        if r.get("product") and r.get("section"):
            cleaned.append(r)

    for i, r in enumerate(cleaned):
        r["_id"] = i

    ROWS_CACHE = cleaned
    LAST_UPDATE = now

    return ROWS_CACHE


# ---------------- UTILS ----------------

def normalize(text):
    return str(text).lower().strip()


def h(text):
    return escape(str(text))


def get_status_icon(status):
    s = normalize(status)

    if "готов" in s:
        return "✅"
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

def search_makets(query):
    rows = get_rows()
    q_words = normalize(query).split()

    results = []

    for r in rows:
        text = " ".join([
            normalize(r.get("product", "")),
            normalize(r.get("section", "")),
            normalize(r.get("scenario", "")),
            normalize(r.get("screen", "")),
            normalize(r.get("keywords", "")),
            normalize(r.get("status", "")),
        ])

        if all(w in text for w in q_words):
            results.append(r)

    return results


# ---------------- UI ----------------

def format_result(r):
    return (
        f"🖼 {r.get('screen', 'Без названия')}\n\n"
        f"🩵 Продукт: {r.get('product', '-')}\n"
        f"📂 Раздел: {r.get('scenario', '-')}\n"
        f"{get_status_icon(r.get('status',''))} Статус: {r.get('status','-')}\n\n"
        f"🕑 Обновлено: {r.get('updated_at','-')}"
    )


def result_keyboard(r):
    buttons = []

    if r.get("screen_url"):
        buttons.append([InlineKeyboardButton("🖼 Открыть сценарий", url=r["screen_url"])])

    if r.get("scenario_url"):
        buttons.append([InlineKeyboardButton("📂 Открыть раздел", url=r["scenario_url"])])

    return InlineKeyboardMarkup(buttons)


def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["🔍 Найти макет", "📚 Открыть каталог"],
            ["❓ FAQ", "💬 Связаться"]
        ],
        resize_keyboard=True
    )


# ---------------- CATALOG ----------------

def product_keyboard():
    rows = get_rows()

    products = sorted({
        r.get("product", "")
        for r in rows
        if r.get("product")
    })

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(p, callback_data=f"product|{p}")]
        for p in products
    ])


def section_keyboard(product):
    rows = get_rows()

    sections = sorted({
        r.get("section", "")
        for r in rows
        if r.get("product") == product and r.get("section")
    })

    buttons = [
        [InlineKeyboardButton(s, callback_data=f"section|{product}|{i}")]
        for i, s in enumerate(sections)
    ]

    buttons.append([InlineKeyboardButton("← Назад", callback_data="catalog")])

    return InlineKeyboardMarkup(buttons)


def build_section(product, section):
    rows = get_rows()

    filtered = [
        r for r in rows
        if r.get("product") == product and r.get("section") == section
    ]

    scenarios = {}

    for r in filtered:
        scenarios.setdefault(r.get("scenario", "Без сценария"), []).append(r)

    text = f"📚 {h(product)} → {h(section)}\n\n"

    for scenario, items in scenarios.items():
        text += f"📂 {scenario}\n"

        for r in items:
            text += f"   └ {get_status_icon(r.get('status',''))} {r.get('screen','')}\n"

        text += "\n"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("← Назад", callback_data=f"product|{product}")],
        [InlineKeyboardButton("← В каталог", callback_data="catalog")]
    ])

    return text, kb


# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я помогу найти нужный сценарий или раздел в Figma.\n\n"
        "Выбери действие ниже или просто напиши запрос.",
        reply_markup=main_menu()
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        q = update.message.text

        if q == "🔍 Найти макет":
            await update.message.reply_text("Напиши запрос.")
            return

        if q == "📚 Открыть каталог":
            await update.message.reply_text("📚 Каталог", reply_markup=product_keyboard())
            return

        if q == "❓ FAQ":
            await update.message.reply_text(
                "❓ FAQ\n\n"
                "Если что-то не находится — напиши @G2_Schrodinger"
            )
            return

        if q == "💬 Связаться":
            await update.message.reply_text("@G2_Schrodinger")
            return

        results = search_makets(q)

        if not results:
            await update.message.reply_text(
                "😔 Ничего не нашла\nПопробуй другой запрос.",
                reply_markup=main_menu()
            )
            return

        await update.message.reply_text("🎉 Нашла:")

        for r in results[:5]:
            await update.message.reply_text(
                format_result(r),
                reply_markup=result_keyboard(r)
            )

    except Exception:
        print(traceback.format_exc())


async def handle_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data

    rows = get_rows()

    if data == "catalog":
        await q.edit_message_text("📚 Каталог", reply_markup=product_keyboard())
        return

    if data.startswith("product|"):
        product = data.split("|")[1]
        await q.edit_message_text(
            f"📚 {product}",
            reply_markup=section_keyboard(product)
        )
        return

    if data.startswith("section|"):
        _, product, i = data.split("|")

        sections = sorted({
            r.get("section", "")
            for r in rows
            if r.get("product") == product
        })

        section = sections[int(i)]
        text, kb = build_section(product, section)

        await q.edit_message_text(
            text,
            reply_markup=kb,
            disable_web_page_preview=True
        )


# ---------------- MAIN ----------------

def main():
    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_catalog))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    print("BOT STARTED")
    app.run_polling()


if __name__ == "__main__":
    main()
