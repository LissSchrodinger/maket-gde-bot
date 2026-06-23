import json
import os
import threading
import time
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
CACHE_TTL = 300  # 5 минут


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


def get_rows():
    global ROWS_CACHE, LAST_UPDATE

    now = time.time()

    if ROWS_CACHE and now - LAST_UPDATE < CACHE_TTL:
        return ROWS_CACHE

    credentials_info = json.loads(GOOGLE_CREDENTIALS)
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    credentials = Credentials.from_service_account_info(credentials_info, scopes=scopes)

    client = gspread.authorize(credentials)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    rows = sheet.get_all_records()

    cleaned_rows = []

    for row in rows:
        if not row.get("product") or not row.get("section"):
            continue
        cleaned_rows.append(row)

    for index, row in enumerate(cleaned_rows):
        row["_id"] = index

    ROWS_CACHE = cleaned_rows
    LAST_UPDATE = now

    return ROWS_CACHE


def normalize(text):
    return str(text).lower().strip()


def html(text):
    return escape(str(text))


def make_link(text, url):
    text = html(text)

    if url:
        return f'<a href="{html(url)}">{text}</a>'

    return text


def get_status_icon(status):
    status = normalize(status)

    if "Готово" in status:
        return "✅"
    if "На ревью" in status:
        return "👀"
    if "В работе" in status:
        return "🛠️"
    if "Холд" in status:
        return "⏸️"
    if "Архив" in status:
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
        buttons.append([InlineKeyboardButton("Открыть сценарий", url=screen_url)])

    if scenario_url:
        buttons.append([InlineKeyboardButton("Открыть раздел", url=scenario_url)])

    return InlineKeyboardMarkup(buttons)


def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["🔍 Найти макет", "📚 Открыть каталог"],
            ["❓ FAQ", "💬 Связаться"],
        ],
        resize_keyboard=True
    )


def product_keyboard():
    rows = get_rows()
    products = sorted({
        row.get("product", "")
        for row in rows
        if row.get("product")
    })

    buttons = [
        [InlineKeyboardButton(product, callback_data=f"product|{product}")]
        for product in products
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
        [InlineKeyboardButton(section, callback_data=f"section|{product}|{index}")]
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

    text = f"📚 {html(product)} → {html(section)}\n\n"

    for scenario, scenario_rows in scenarios.items():
        scenario_url = scenario_rows[0].get("scenario_url", "")
        text += f"📂 {make_link(scenario, scenario_url)}\n"

        for row in scenario_rows:
            screen = row.get("screen", "Без названия")
            screen_url = row.get("screen_url", "")
            status_icon = get_status_icon(row.get("status", ""))

            text += f"   └ {status_icon} {make_link(screen, screen_url)}\n"

        text += "\n"

    buttons = [
        [InlineKeyboardButton("← Назад к разделам", callback_data=f"product|{product}")],
        [InlineKeyboardButton("← В каталог", callback_data="catalog")],
    ]

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
    print(f"Got message: {query}", flush=True)

    if query == "🔍 Найти макет":
        await update.message.reply_text(
            "Напиши название, фразу или ключевое слово.\n\n"
            "Например: добавление файла, создание запроса, информация о проверке"
        )
        return

    if query == "📚 Открыть каталог":
        await show_catalog_message(update)
        return

    if query == "❓ FAQ":
        await update.message.reply_text(
            "❓ <b>Что умеет бот?</b>\n"
            "Помогает быстро найти нужный экран, сценарий или раздел в Figma. "
            "Можно искать по названию, ключевым словам или пользоваться каталогом.\n\n"

            "❓ <b>Не помню название экрана. Что делать?</b>\n"
            "Открой каталог и пройди по структуре:\n"
            "Продукт → Раздел → Сценарий.\n\n"

            "❓ <b>Почему я не могу найти макет?</b>\n"
            "Обычно причина одна из трёх:\n"
            "• Макет ещё не добавлен в каталог;\n"
            "• Он называется не так, как ты ожидаешь;\n"
            "• Макет действительно потерялся.\n"
            "Последний случай пока не зафиксирован 😏\n\n"

            "❓ <b>Что делать, если ссылка сломалась или ведёт не туда?</b>\n"
            "В меню бота кликни «Написать дизайнеру». "
            "Желательно сразу приложить ссылку, по которой переходил.\n\n"

            "❓ <b>Кому писать, если всё плохо?</b>\n"
            "Если ничего не находится, каталог выглядит странно, ссылки не работают "
            "или просто хочется пожаловаться на жизнь —\n\n"
            "👉 В меню бота кликни «Написать дизайнеру»",
            parse_mode="HTML"
        )
        return

    if query == "💬 Связаться":
        await update.message.reply_text(
            "Нашёл ошибку?\n"
            "Сломалась ссылка?\n"
            "Не хватает макета?\n"
            "Есть идея для улучшения?\n\n"
            "Пиши:\n"
            "👉 @G2_Schrodinger"
        )
        return

    results = search_makets(query)

    if not results:
        await update.message.reply_text(
            "😔 Ничего не нашла\n\n"
            "Попробуй:\n"
            "• другое ключевое слово\n"
            "• открыть каталог\n"
            "• поискать по разделам\n\n"
            "Если макет точно существует или должен быть добавлен — напиши:\n"
            "@G2_Schrodinger",
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
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return


def main():
    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_catalog_click))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))

    print("Bot polling started", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
