import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import gspread
from google.oauth2.service_account import Credentials
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters


TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")


def start_health_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is alive")

    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


def get_rows():
    credentials_info = json.loads(GOOGLE_CREDENTIALS)
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    credentials = Credentials.from_service_account_info(credentials_info, scopes=scopes)
    client = gspread.authorize(credentials)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    return sheet.get_all_records()


def normalize(text):
    return str(text).lower().strip()


def get_status_icon(status):
    status = normalize(status)

    if "готов" in status:
        return "🟢"
    if "ревью" in status:
        return "🟣"
    if "работ" in status:
        return "🟡"
    if "холд" in status:
        return "🔴"
    if "архив" in status:
        return "⚫"

    return "⚪"


def search_makets(query):
    rows = get_rows()
    query_words = normalize(query).split()
    results = []

    for row in rows:
        searchable_text = " ".join([
            normalize(row.get("product", "")),
            normalize(row.get("section", "")),
            normalize(row.get("scenario", "")),
            normalize(row.get("screen", "")),
            normalize(row.get("keywords", "")),
            normalize(row.get("status", "")),
        ])

        if all(word in searchable_text for word in query_words):
            results.append(row)

    return results


def format_result(row):
    status = row.get("status", "-")
    status_icon = get_status_icon(status)

    return (
        f"📌 <b>{row.get('screen', 'Без названия')}</b>\n\n"
        f"📦 <b>Продукт:</b> {row.get('product', '-')}\n"
        f"📂 <b>Раздел:</b> {row.get('section', '-')}\n"
        f"🎬 <b>Сценарий:</b> {row.get('scenario', '-')}\n\n"
        f"{status_icon} <b>Статус:</b> {status}\n"
        f"🗓 <b>Обновлено:</b> {row.get('updated_at', '-')}"
    )


def result_keyboard(row):
    buttons = []

    screen_url = row.get("screen_url", "")
    scenario_url = row.get("scenario_url", "")

    if screen_url:
        buttons.append([InlineKeyboardButton("🖼 Открыть экран", url=screen_url)])

    if scenario_url:
        buttons.append([InlineKeyboardButton("🎬 Открыть сценарий", url=scenario_url)])

    return InlineKeyboardMarkup(buttons)


def main_menu():
    return ReplyKeyboardMarkup(
        [["🔍 Найти макет", "📚 Открыть каталог"]],
        resize_keyboard=True
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот <b>«А где макет?»</b>\n\n"
        "Помогу найти нужный экран, сценарий или раздел в Figma.\n\n"
        "Выбери действие ниже или просто напиши, что ищешь.",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text

    if query == "🔍 Найти макет":
        await update.message.reply_text(
            "Напиши название, фразу или ключевое слово.\n\n"
            "Например: <b>файлы</b>, <b>субъект</b>, <b>информация о проверке</b>",
            parse_mode="HTML"
        )
        return

    if query == "📚 Открыть каталог":
        await update.message.reply_text(
            "📚 Каталог скоро добавим.\n\n"
            "Пока можно искать макеты текстом.",
            parse_mode="HTML"
        )
        return

    results = search_makets(query)

    if not results:
        await update.message.reply_text(
            "Ничего не нашла 😔\n\n"
            "Попробуй другую формулировку или ключевое слово.",
            reply_markup=main_menu()
        )
        return

    await update.message.reply_text(
        f"Нашла макеты: <b>{len(results)}</b>",
        parse_mode="HTML"
    )

    for row in results[:5]:
        await update.message.reply_text(
            format_result(row),
            reply_markup=result_keyboard(row),
            parse_mode="HTML"
        )

    if len(results) > 5:
        await update.message.reply_text(f"Показала первые 5 из {len(results)}.")


def main():
    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    app.run_polling()


if __name__ == "__main__":
    main()
