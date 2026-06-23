import json
import os
import threading
import time
import traceback
from html import escape

import gspread
from google.oauth2.service_account import Credentials

from fastapi import FastAPI, Request
import uvicorn

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

# ---------------- ENV ----------------

TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

BASE_URL = "https://maket-gde-bot.onrender.com"
WEBHOOK_PATH = "/webhook"

# ---------------- APP ----------------

fastapi_app = FastAPI()
telegram_app = None


# ---------------- WEBHOOK ----------------

@fastapi_app.get("/")
def health():
    return {"status": "ok"}


@fastapi_app.post(WEBHOOK_PATH)
async def webhook(req: Request):
    update = Update.de_json(await req.json(), telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


# ---------------- GOOGLE SHEETS ----------------

def get_rows():
    creds = json.loads(GOOGLE_CREDENTIALS)

    credentials = Credentials.from_service_account_info(
        creds,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )

    client = gspread.authorize(credentials)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1

    return sheet.get_all_records()


# ---------------- UTILS ----------------

def normalize(text):
    return str(text).lower().strip()


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
            ["❓ FAQ", "💬 Связаться"],
        ],
        resize_keyboard=True
    )


# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я помогу найти сценарий или раздел в Figma.\n\n"
        "Выбери действие или просто напиши запрос.",
        reply_markup=main_menu()
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        q = update.message.text

        if q == "🔍 Найти макет":
            await update.message.reply_text("Напиши запрос для поиска.")
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
                "❓ FAQ\n\n"
                "Если ничего не находится — напиши @G2_Schrodinger"
            )
            return

        if q == "💬 Связаться":
            await update.message.reply_text("@G2_Schrodinger")
            return

        results = search_makets(q)

        if not results:
            await update.message.reply_text(
                "😔 Ничего не нашла\n\n"
                "Попробуй другой запрос или открой каталог.",
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


# ---------------- CATALOG NAVIGATION ----------------

async def handle_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    rows = get_rows()

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

    if data.startswith("section|"):
        _, product, i = data.split("|")

        sections = sorted({
            r.get("section")
            for r in rows
            if r.get("product") == product
        })

        section = sections[int(i)]

        filtered = [
            r for r in rows
            if r.get("product") == product and r.get("section") == section
        ]

        text = f"📚 {product} → {section}\n\n"

        for r in filtered:
            text += f"└ {get_status_icon(r.get('status'))} {r.get('screen')}\n"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("← Назад", callback_data=f"product|{product}")],
            [InlineKeyboardButton("← В каталог", callback_data="catalog")]
        ])

        await q.edit_message_text(text, reply_markup=kb)
        return

    if data == "catalog":
        rows = get_rows()
        products = sorted({r.get("product") for r in rows if r.get("product")})

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(p, callback_data=f"product|{p}")]
            for p in products
        ])

        await q.edit_message_text("📚 Каталог", reply_markup=kb)


# ---------------- START WEBHOOK ----------------

async def post_init(app):
    await app.bot.set_webhook(url=f"{BASE_URL}{WEBHOOK_PATH}")


def run_server():
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(fastapi_app, host="0.0.0.0", port=port)


# ---------------- MAIN ----------------

def main():
    global telegram_app

    telegram_app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CallbackQueryHandler(handle_catalog))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    threading.Thread(target=run_server, daemon=True).start()

    
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000))
    )
