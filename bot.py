import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
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
    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=scopes
    )

    client = gspread.authorize(credentials)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1

    return sheet.get_all_records()


def normalize(text):
    return str(text).lower().strip()


def search_makets(query):
    rows = get_rows()
    query = normalize(query)
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

        if query in searchable_text:
            results.append(row)

    return results


def format_result(row):
    return (
        f"📌 {row.get('screen', 'Без названия')}\n\n"
        f"Продукт: {row.get('product', '-')}\n"
        f"Раздел: {row.get('section', '-')}\n"
        f"Сценарий: {row.get('scenario', '-')}\n"
        f"Статус: {row.get('status', '-')}\n"
        f"Обновлено: {row.get('updated_at', '-')}\n\n"
        f"🔗 Экран: {row.get('screen_url', '-')}\n"
        f"🔗 Сценарий: {row.get('scenario_url', '-')}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот «А где макет?» 👀\n\n"
        "Напиши, что ищешь. Например:\n"
        "• файлы\n"
        "• субъект\n"
        "• информация о проверке"
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    results = search_makets(query)

    if not results:
        await update.message.reply_text(
            "Ничего не нашла 😔\n\n"
            "Попробуй другую формулировку или ключевое слово."
        )
        return

    limited_results = results[:5]

    text = f"Нашла макеты: {len(results)}\n\n"
    text += "\n\n———\n\n".join(format_result(row) for row in limited_results)

    if len(results) > 5:
        text += f"\n\nПоказала первые 5 из {len(results)}."

    await update.message.reply_text(text)


def main():
    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    app.run_polling()


if __name__ == "__main__":
    main()
