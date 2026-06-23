import json
import os
import time
from html import escape

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


# ---------------- DATA ----------------

def get_rows():
    global ROWS_CACHE, LAST_UPDATE

    now = time.time()

    if ROWS_CACHE and now - LAST_UPDATE < CACHE_TTL:
        return ROWS_CACHE

    try:
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

    except Exception:
        return ROWS_CACHE or []


# ---------------- UTILS ----------------

def normalize(text):
    return str(text).lower().strip()


def h(text):
    return escape(str(text))


def status_icon(status):
    s = normalize(status)

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


def link(text, url):
    if not url:
        return h(text)
    return f'<a href="{escape(url)}">{h(text)}</a>'


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
        f"🖼 {r.get('screen','-')}\n\n"
        f"🩵 Продукт: {r.get('product','-')}\n"
        f"📂 Раздел: {r.get('scenario','-')}\n"
        f"{status_icon(r.get('status',''))} Статус: {r.get('status','-')}\n\n"
        f"🕑 Обновлено: {r.get('updated_at','-')}"
    )


def result_keyboard(r):
    kb = []

    if r.get("screen_url"):
        kb.append([InlineKeyboardButton("🖼 Открыть сценарий", url=r["screen_url"])])

    if r.get("scenario_url"):
        kb.append([InlineKeyboardButton("📂 Открыть раздел", url=r["scenario_url"])])

    return InlineKeyboardMarkup(kb)


def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["🔍 Найти макет", "📚 Открыть каталог"],
            ["❓ FAQ", "💬 Связаться"],
        ],
        resize_keyboard=True
    )


# ---------------- START ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я помогу найти макеты.\nВыбери действие или напиши запрос.",
        reply_markup=main_menu()
    )


# ---------------- SEARCH ----------------

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text

    if q == "🔍 Найти макет":
        await update.message.reply_text("Напиши запрос.")
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
        await update.message.reply_text(
            "❓ FAQ\n\nЕсли ничего не найдено — напиши @G2_Schrodinger"
        )
        return

    if q == "💬 Связаться":
        await update.message.reply_text("@G2_Schrodinger")
        return

    results = search_makets(q)

    if not results:
        await update.message.reply_text(
            "😔 Ничего не нашла",
            reply_markup=main_menu()
        )
        return

    await update.message.reply_text("🎉 Нашла:")

    for r in results[:5]:
        await update.message.reply_text(
            format_result(r),
            reply_markup=result_keyboard(r)
        )


# ---------------- CATALOG ----------------

async def catalog_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    rows = get_rows()

    # catalog root
    if data == "catalog":
        products = sorted({r.get("product") for r in rows if r.get("product")})

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(p, callback_data=f"product|{p}")]
            for p in products
        ])

        await q.edit_message_text("📚 Каталог", reply_markup=kb)
        return

    # product -> sections
    if data.startswith("product|"):
        product = data.split("|")[1]

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

    # section -> TREE WITH LINKS (FIXED)
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

        for scenario, scen_rows in scenarios.items():
            scenario_url = scen_rows[0].get("scenario_url", "")

            text += f"📂 {link(scenario, scenario_url)}\n"

            for r in scen_rows:
                text += f"   ├ {status_icon(r.get('status',''))} {link(r.get('screen',''), r.get('screen_url',''))}\n"

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
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(catalog_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    print("BOT STARTED")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
