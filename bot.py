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
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


# ---------------- DATA ----------------

def get_rows():
    global ROWS_CACHE, LAST_UPDATE

    now = time.time()

    if ROWS_CACHE and now - LAST_UPDATE < CACHE_TTL:
        return ROWS_CACHE

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
        if row.get("product") and row.get("section"):
            cleaned.append(row)

    for i, row in enumerate(cleaned):
        row["_id"] = i

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

    if "готово" in s:
        return "✅"
    if "на ревью" in s:
        return "👀"
    if "в работе" in s:
        return "🛠️"
    if "холд" in s:
        return "⏸️"
    if "архив" in s:
        return "📦"

    return "▫️"


def make_link(text, url):
    if not url:
        return h(text)
    return f'<a href="{escape(url)}">{h(text)}</a>'


# ---------------- SEARCH ----------------

def search_makets(query):
    rows = get_rows()
    q_words = normalize(query).split()

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

        if all(w in text for w in q_words):
            results.append(row)

    return results


# ---------------- UI ----------------

def format_result(row):
    return (
        f"🖼 {row.get('screen', 'Без названия')}\n\n"
        f"🩵 Продукт: {row.get('product', '-')}\n"
        f"📂 Раздел: {row.get('scenario', '-')}\n"
        f"{get_status_icon(row.get('status', ''))} Статус: {row.get('status', '-')}\n\n"
        f"🕑 Обновлено: {row.get('updated_at', '-')}"
    )


def result_keyboard(row):
    buttons = []

    if row.get("screen_url"):
        buttons.append([InlineKeyboardButton("Открыть сценарий", url=row["screen_url"])])

    if row.get("scenario_url"):
        buttons.append([InlineKeyboardButton("Открыть раздел", url=row["scenario_url"])])

    return InlineKeyboardMarkup(buttons)


def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["🔍 Найти макет", "📚 Открыть каталог"],
            ["❓ FAQ", "💬 Связаться"],
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

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(s, callback_data=f"section|{product}|{i}")]
        for i, s in enumerate(sections)
    ] + [[InlineKeyboardButton("← Назад", callback_data="catalog")]])


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
        url = items[0].get("scenario_url", "")
        text += f"📂 {make_link(scenario, url)}\n"

        for r in items:
            text += f"   └ {get_status_icon(r.get('status',''))} {make_link(r.get('screen',''), r.get('screen_url',''))}\n"

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
        "Выбери действие ниже или просто напиши, что ищешь.",
        reply_markup=main_menu()
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.message.text
        print("MSG:", query, flush=True)

        if query == "🔍 Найти макет":
            await update.message.reply_text("Напиши запрос для поиска.")
            return

        if query == "📚 Открыть каталог":
            await update.message.reply_text(
                "📚 Каталог\n\nВыбери продукт:",
                reply_markup=product_keyboard()
            )
            return

        if query == "❓ FAQ":
            await update.message.reply_text(
                "❓ Что умеет бот?\n"
                "Находит макеты в Figma по структуре.\n\n"

                "❓ Не помню экран?\n"
                "Используй каталог.\n\n"

                "❓ Почему не нахожу?\n"
                "Он может называться иначе или ещё не добавлен.\n\n"

                "❓ Сломалась ссылка?\n"
                "Напиши в поддержку.\n\n"

                "❓ Кому писать?\n"
                "👉 @G2_Schrodinger",
                parse_mode="HTML"
            )
            return

        if query == "💬 Связаться":
            await update.message.reply_text("👉 @G2_Schrodinger")
            return

        results = search_makets(query)

        if not results:
            await update.message.reply_text(
                "😔 Ничего не нашла\n\n"
                "Попробуй другой запрос или каталог.",
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
        print(traceback.format_exc(), flush=True)


async def handle_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data

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
            for r in get_rows()
            if r.get("product") == product
        })

        section = sections[int(i)]
        text, kb = build_section(product, section)

        await q.edit_message_text(
            text,
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True
        )


# ---------------- MAIN ----------------

def main():
    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_catalog))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    print("BOT STARTED", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
