import json
import os
import threading
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

CATALOG_PRODUCTS = ["ЕРМДБ", "СКДБ", "СОВА"]


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
    rows = sheet.get_all_records()

    for index, row in enumerate(rows):
        row["_id"] = index

    return rows


def normalize(text):
    return str(text).lower().strip()


def get_status_icon(status):
    status = normalize(status)

    if "готов" in status:
        return "✅"
    if "ревью" in status:
        return "👀"
    if "работ" in status:
        return "🛠️"
    if "холд" in status:
        return "⏸️"
    if "архив" in status:
        return "📦"

    return "▫️"


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
        f"🖼 {row.get('screen', 'Без названия')}\n\n\n"
        f"🩵 Продукт          {row.get('product', '-')}\n"
        f"📂 Раздел             {row.get('scenario', '-')}\n"
        f"{status_icon} Статус              {status}\n\n"
        f"🕑 Обновлено    {row.get('updated_at', '-')}"
    )


def result_keyboard(row):
    buttons = []

    screen_url = row.get("screen_url", "")
    scenario_url = row.get("scenario_url", "")

    if screen_url:
        buttons.append([InlineKeyboardButton("🖼 Открыть сценарий", url=screen_url)])

    if scenario_url:
        buttons.append([InlineKeyboardButton("📂 Открыть раздел", url=scenario_url)])

    return InlineKeyboardMarkup(buttons)


def main_menu():
    return ReplyKeyboardMarkup(
        [["🔍 Найти макет", "📚 Открыть каталог"]],
        resize_keyboard=True
    )


def product_keyboard():
    buttons = [
        [InlineKeyboardButton(f"🩵 {product}", callback_data=f"product|{product}")]
        for product in CATALOG_PRODUCTS
    ]
    return InlineKeyboardMarkup(buttons)


def section_keyboard(product):
    rows = get_rows()
    sections = sorted({
        row.get("section", "")
        for row in rows
        if row.get("product") == product and row.get("section")
    })

    buttons = [
        [InlineKeyboardButton(f"📂 {section}", callback_data=f"section|{product}|{index}")]
        for index, section in enumerate(sections)
    ]

    buttons.append([InlineKeyboardButton("← Назад к продуктам", callback_data="catalog")])
    return InlineKeyboardMarkup(buttons)


def build_section_text_and_keyboard(product, section):
    rows = [
        row for row in get_rows()
        if row.get("product") == product and row.get("section") == section
    ]

    scenarios = {}
    for row in rows:
        scenario = row.get("scenario", "Без раздела")
        scenarios.setdefault(scenario, []).append(row)

    text = f"📚 {product} → {section}\n\n"

    buttons = []

    for scenario, scenario_rows in scenarios.items():
        text += f"📂 {scenario}\n"

        for row in scenario_rows:
            screen = row.get("screen", "Без названия")
            status_icon = get_status_icon(row.get("status", ""))
            text += f"   └ {status_icon} {screen}\n"

            buttons.append([
                InlineKeyboardButton(
                    f"🖼 {screen}",
                    callback_data=f"screen|{row['_id']}"
                )
            ])

        text += "\n"

    buttons.append([InlineKeyboardButton("← Назад к разделам", callback_data=f"product|{product}")])
    buttons.append([InlineKeyboardButton("← В каталог", callback_data="catalog")])

    return text, InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я помогу найти нужный сценарий или раздел в Figma.\n\n"
        "Выбери действие ниже или просто напиши, что ищешь.",
        reply_markup=main_menu()
    )


async def show_catalog_message(update: Update):
    await update.message.reply_text(
        "📚 Каталог\n\nВыбери продукт:",
        reply_markup=product_keyboard()
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text

    if query == "🔍 Найти макет":
        await update.message.reply_text(
            "Напиши название, фразу или ключевое слово.\n\n"
            "Например: добавление файла, создание запроса, информация о проверке"
        )
        return

    if query == "📚 Открыть каталог":
        await show_catalog_message(update)
        return

    results = search_makets(query)

    if not results:
        await update.message.reply_text(
            "Ничего не нашла 😔\n\n"
            "Попробуй другую формулировку или ключевое слово.",
            reply_markup=main_menu()
        )
        return

    await update.message.reply_text("🎉 Кое-что нашлось:")

    for row in results[:5]:
        await update.message.reply_text(
            format_result(row),
            reply_markup=result_keyboard(row)
        )


async def handle_catalog_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    rows = get_rows()

    if data == "catalog":
        await query.edit_message_text(
            "📚 Каталог\n\nВыбери продукт:",
            reply_markup=product_keyboard()
        )
        return

    if data.startswith("product|"):
        product = data.split("|", 1)[1]
        await query.edit_message_text(
            f"📚 {product}\n\nВыбери раздел:",
            reply_markup=section_keyboard(product)
        )
        return

    if data.startswith("section|"):
        _, product, section_index = data.split("|")
        sections = sorted({
            row.get("section", "")
            for row in rows
            if row.get("product") == product and row.get("section")
        })

        section = sections[int(section_index)]
        text, keyboard = build_section_text_and_keyboard(product, section)

        await query.edit_message_text(
            text,
            reply_markup=keyboard
        )
        return

    if data.startswith("screen|"):
        screen_id = int(data.split("|")[1])
        row = next((item for item in rows if item["_id"] == screen_id), None)

        if not row:
            await query.message.reply_text("Не смогла найти этот макет 😔")
            return

        await query.message.reply_text(
            format_result(row),
            reply_markup=result_keyboard(row)
        )


def main():
    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_catalog_click))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    app.run_polling()


if __name__ == "__main__":
    main()
