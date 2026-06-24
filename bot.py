import json
import os
import sys
import time
import logging
import asyncio
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

# ---------------- LOGGING ----------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    stream=sys.stdout,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("maket-bot")


# ---------------- CONFIG ----------------

TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

CACHE = []
CACHE_TIME = 0
TTL = 300




# ---------------- DATA ----------------

def get_rows():
    global CACHE, CACHE_TIME

    now = time.time()
    if CACHE and now - CACHE_TIME < TTL:
        return CACHE

    try:
        creds = json.loads(GOOGLE_CREDENTIALS)

        client = gspread.authorize(
            Credentials.from_service_account_info(
                creds,
                scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
            )
        )

        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        rows = sheet.get_all_records()

    except Exception:
        log.exception("Google Sheets error")
        return CACHE

    clean = []
    for i, r in enumerate(rows):
        if r.get("product") and r.get("section"):
            r["_id"] = i
            clean.append(r)

    CACHE = clean
    CACHE_TIME = now

    log.info("Loaded %d rows", len(clean))
    return CACHE


# ---------------- UTILS ----------------

def norm(t):
    return str(t).lower().strip()


def h(t):
    return escape(str(t))


def hurl(t):
    return escape(str(t), quote=True)


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
        blob = " ".join([
            norm(r.get("product", "")),
            norm(r.get("section", "")),
            norm(r.get("scenario", "")),
            norm(r.get("screen", "")),
            norm(r.get("keywords", "")),
            norm(r.get("status", "")),
        ])

        if all(w in blob for w in words):
            res.append(r)

    log.info("Search %r → %d results", query, len(res))
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
        kb.append([InlineKeyboardButton("Открыть сценарий", url=r["screen_url"])])

    if r.get("scenario_url"):
        kb.append([InlineKeyboardButton("Открыть раздел", url=r["scenario_url"])])

    return InlineKeyboardMarkup(kb)


# ---------------- CATALOG ----------------

def get_products(rows):
    return sorted({str(r.get("product")) for r in rows if r.get("product")})


def get_sections(rows, product):
    return sorted({
        str(r.get("section"))
        for r in rows
        if str(r.get("product")) == product and r.get("section")
    })


def catalog_kb(rows):
    products = get_products(rows)

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(p, callback_data=f"product|{i}")]
        for i, p in enumerate(products)
    ]) if products else None


# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет 👋\n"
        "Я бот-каталог макетов Систем Безопасности.\n\n"
        "Если ищете макет — просто напишите название сценария.\n"
        "А если не уверены, как он называется, то воспользуйтесь каталогом.",
        reply_markup=menu()
    )


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text

    if q == "🔍 Найти макет":
        await update.message.reply_text("Отправьте название сценария, который хотите найти")
        return

    if q == "📚 Открыть каталог":
        rows = get_rows()
        kb = catalog_kb(rows)

        if not kb:
            await update.message.reply_text("Каталог сейчас отдыхает, скоро вернется на место")
            return

        await update.message.reply_text("📚 Каталог", reply_markup=kb)
        return

    if q == "❓ FAQ":
        await update.message.reply_text(
            "❓Как найти нужный макет?\n"
            "Если знаете название — вы уже на полпути к успеху.\n"
            "Нажмите кнопку «Найти макет» и введите название сценария.\n\n"
            "❓Я не знаю название сценария. Что делать?\n"
            "Воспользуйтесь каталогом. В нем все макеты сгруппированы по разделам и сценариям.\n\n"
            "❓Поиск ничего не нашел. Почему?\n"
            "Возможно, в запросе есть опечатка, сценарий называется иначе или макет еще не добавлен в каталог.\n\n"
            "❓Все перепробовал, но нужного макета нет.\n"
            "— Макет переехал, а дизайнер не обновил ссылку;\n"
            "— Это новый макет и его еще нет в базе;\n"
            "— Никто ещё не догадался, как его назвать нормально.\n"
            "Свяжитесь с дизайнером, вам помогут.\n\n"
            "❓Нашел ошибку / не нашел макет / нужен совет.\n"
            "Для этого и существует кнопка «Связаться» ✨"
        )
        return

    if q == "💬 Связаться":
        await update.message.reply_text("👉 @G2_Schrodinger")
        return

    res = search(q)

    if not res:
        await update.message.reply_text("Ничего не найдено", reply_markup=menu())
        return

    for r in res[:5]:
        await update.message.reply_text(
            format_row(r),
            reply_markup=kb_row(r)
        )


# ---------------- CALLBACKS (FULL CATALOG RESTORED) ----------------

async def catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    rows = get_rows()

    try:
        log.info("Callback %r", data)

        # ---------------- catalog root ----------------
        if data == "catalog":
            kb = catalog_kb(rows)
            await q.edit_message_text("📚 Каталог", reply_markup=kb)
            return

        # ---------------- product level ----------------
        if data.startswith("product|"):
            pi = int(data.split("|")[1])

            products = get_products(rows)
            product = products[pi]

            sections = get_sections(rows, product)

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(s, callback_data=f"section|{pi}|{si}")]
                for si, s in enumerate(sections)
            ] + [[InlineKeyboardButton("← Назад", callback_data="catalog")]])

            await q.edit_message_text(f"📚 {product}", reply_markup=kb)
            return

        # ---------------- section level ----------------
        if data.startswith("section|"):
            _, pi, si = data.split("|")
            pi, si = int(pi), int(si)

            products = get_products(rows)
            product = products[pi]

            sections = get_sections(rows, product)
            section = sections[si]

            items = [
                r for r in rows
                if str(r.get("product")) == product and str(r.get("section")) == section
            ]

            scenarios = {}
            for r in items:
                scenarios.setdefault(r.get("scenario", "Без сценария"), []).append(r)

            text = f"📚 {h(product)} → {h(section)}\n\n"

            for sc, lst in scenarios.items():
                url = lst[0].get("scenario_url")

                if url:
                    text += f"📂 <a href='{hurl(url)}'>{h(sc)}</a>\n"
                else:
                    text += f"📂 {h(sc)}\n"

                for r in lst:
                    su = r.get("screen_url")

                    if su:
                        text += f"   └ {icon(r.get('status',''))} <a href='{hurl(su)}'>{h(r.get('screen',''))}</a>\n"
                    else:
                        text += f"   └ {icon(r.get('status',''))} {h(r.get('screen',''))}\n"

                text += "\n"

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("← Назад", callback_data=f"product|{pi}")],
                [InlineKeyboardButton("← Каталог", callback_data="catalog")]
            ])

            await q.edit_message_text(
                text,
                reply_markup=kb,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            return

    except Exception:
        log.exception("Callback error")
        await q.edit_message_text("Ошибка. Открой каталог заново.")


# ---------------- ERROR ----------------

async def error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled error", exc_info=context.error)





# ---------------- MAIN ----------------
def main():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class PingHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass

    def run_ping():
        HTTPServer(("0.0.0.0", 8081), PingHandler).serve_forever()

    threading.Thread(target=run_ping, daemon=True).start()

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_handler))
    app.add_handler(CallbackQueryHandler(catalog_callback))
    app.add_error_handler(error)

    log.info("BOT STARTED (FULL WEBHOOK VERSION)")

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        url_path=TOKEN,
        webhook_url=f"{os.environ['WEBHOOK_URL']}/{TOKEN}",
    )

if __name__ == "__main__":
    main()
