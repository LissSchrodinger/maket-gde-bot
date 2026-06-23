import json
import os
import time
import threading
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

ROWS_CACHE = []
LAST_UPDATE = 0
CACHE_TTL = 300


# ---------------- HEALTH ----------------

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
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


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
    for i, r in enumerate(rows):
        if r.get("product") and r.get("section"):
            r["_id"] = i
            cleaned.append(r)

    ROWS_CACHE = cleaned
    LAST_UPDATE = now

    return ROWS_CACHE


# ---------------- UTILS ----------------

def normalize(t):
    return str(t).lower().strip()


def h(t):
    return escape(str(t))


def get_status_icon(status):
    s = normalize(status)

    if "готово" in s:
        return "🟢"
    if "на ревью" in s:
        return "👀"
    if "в работе" in s:
        return "🛠️"
    if "холд" in s:
        return "⏸️"
    if "архив" in s:
        return "📦"

    return "▫️"


def link(text, url):
    if not url:
        return h(text)
    return f'<a href="{escape(url)}">{h(text)}</a>'


# ---------------- SEARCH ----------------

def search_makets(query):
    rows = get_rows()
    q = normalize(query).split()

    res = []

    for r in rows:
        blob = " ".join([
            normalize(r.get("product", "")),
            normalize(r.get("section", "")),
            normalize(r.get("scenario", "")),
            normalize(r.get("screen", "")),
            normalize(r.get("keywords", "")),
            normalize(r.get("status", "")),
        ])

        if all(w in blob for w in q):
            res.append(r)

    return res


# ---------------- UI ----------------

def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["🔍 Найти макет", "📚 Открыть каталог"],
            ["❓ FAQ", "💬 Связаться"],
        ],
        resize_keyboard=True
    )


def format_result(r):
    return (
        f"🖼 {h(r.get('screen','Без названия'))}\n\n"
        f"🩵 Продукт: {h(r.get('product','-'))}\n"
        f"📂 Раздел: {h(r.get('section','-'))}\n"
        f"{get_status_icon(r.get('status',''))} Статус: {h(r.get('status','-'))}\n\n"
        f"🕑 Обновлено: {h(r.get('updated_at','-'))}"
    )


def result_keyboard(r):
    kb = []

    if r.get("screen_url"):
        kb.append([InlineKeyboardButton("🖼 Открыть экран", url=r["screen_url"])])

    if r.get("scenario_url"):
        kb.append([InlineKeyboardButton("📂 Открыть сценарий", url=r["scenario_url"])])

    return InlineKeyboardMarkup(kb)


# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я помогу найти макеты.",
        reply_markup=main_menu()
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text

    if q == "🔍 Найти макет":
        await update.message.reply_text("Напиши запрос")
        return

    if q == "📚 Открыть каталог":
        rows = get_rows()

        products = sorted({r.get("product") for r in rows if r.get("product")})

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(p, callback_data=f"product|{p}")]
            for p in products
        ])

        await update.message.reply_text("📚 Каталог", reply_markup=kb)
        return

    if q == "❓ FAQ":
        await update.message.reply_text("FAQ: пиши @G2_Schrodinger")
        return

    if q == "💬 Связаться":
        await update.message.reply_text("@G2_Schrodinger")
        return

    res = search_makets(q)

    if not res:
        await update.message.reply_text("Ничего не найдено", reply_markup=main_menu())
        return

    await update.message.reply_text("🎉 Нашла:")

    for r in res[:5]:
        await update.message.reply_text(
            format_result(r),
            reply_markup=result_keyboard(r),
            parse_mode="HTML"
        )


# ---------------- CATALOG (TREE WITH LINKS) ----------------

async def catalog_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    rows = get_rows()

    if data == "catalog":
        products = sorted({r.get("product") for r in rows if r.get("product")})

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

        for scenario, lst in scenarios.items():
            url = lst[0].get("scenario_url")

            text += f"📂 {link(scenario, url)}\n"

            for r in lst:
                screen = r.get("screen", "-")
                screen_url = r.get("screen_url", "")
                status = get_status_icon(r.get("status", ""))

                text += f"   └ {status} {link(screen, screen_url)}\n"

            text += "\n"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("← Назад", callback_data=f"product|{product}")],
            [InlineKeyboardButton("← В каталог", callback_data="catalog")]
        ])

        await q.edit_message_text(
            text,
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True
        )


# ---------------- MAIN ----------------

def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.bot.delete_webhook(drop_pending_updates=True)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(catalog_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    print("BOT STARTED")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
