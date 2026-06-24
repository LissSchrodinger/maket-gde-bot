import json
import os
import sys
import time
import atexit
import logging
import threading
import asyncio
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
# httpx логирует каждый запрос к Telegram на INFO — приглушаем
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("maket-bot")


# ---------------- CONFIG ----------------

TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

CACHE = []
CACHE_TIME = 0
TTL = 300

PID_FILE = os.getenv("PID_FILE", "/tmp/bot.lock")


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

        # глушим стандартный спам BaseHTTPRequestHandler в stderr
        def log_message(self, fmt, *args):
            log.debug("health: " + fmt, *args)

    port = int(os.getenv("PORT", 10000))
    log.info("Health server listening on 0.0.0.0:%s", port)
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


# ---------------- DATA ----------------

def get_rows():
    global CACHE, CACHE_TIME

    if not GOOGLE_CREDENTIALS:
        log.warning("GOOGLE_CREDENTIALS is not set — returning empty dataset")
        return []

    now = time.time()
    if CACHE and now - CACHE_TIME < TTL:
        log.debug("Cache hit (%d rows, age %.0fs)", len(CACHE), now - CACHE_TIME)
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
        # Не роняем хендлер из-за сетевых/авторизационных ошибок Google.
        # Если есть устаревший кэш — отдаём его, иначе пустой список.
        log.exception("Failed to load rows from Google Sheets")
        return CACHE if CACHE else []

    clean = []
    for i, r in enumerate(rows):
        if r.get("product") and r.get("section"):
            r["_id"] = i
            clean.append(r)

    CACHE = clean
    CACHE_TIME = now
    log.info("Loaded %d rows from Google Sheets (%d after filtering)", len(rows), len(clean))
    return CACHE


# ---------------- UTILS ----------------

def norm(t):
    return str(t).lower().strip()


def h(t):
    return escape(str(t))


def hurl(t):
    # экранируем URL для атрибута href (включая кавычки)
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


def get_products(rows):
    return sorted({str(r.get("product")) for r in rows if r.get("product")})


def get_sections(rows, product):
    return sorted({
        str(r.get("section"))
        for r in rows
        if str(r.get("product")) == product and r.get("section")
    })


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


def catalog_kb(rows):
    products = get_products(rows)

    # продукт кодируем ИНДЕКСОМ, а не именем: имя может превысить
    # лимит callback_data в 64 байта и содержать служебный символ '|'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(p, callback_data=f"product|{i}")]
        for i, p in enumerate(products)
    ]) if products else None


# ---------------- HANDLERS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    log.info("START from user %s", update.effective_user.id if update.effective_user else "?")
    await update.message.reply_text(
        "Привет! Я помогу найти макеты.",
        reply_markup=menu()
    )


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    q = update.message.text
    uid = update.effective_user.id if update.effective_user else "?"

    if q == "🔍 Найти макет":
        await update.message.reply_text("Напиши запрос")
        return

    if q == "📚 Открыть каталог":
        rows = get_rows()
        kb = catalog_kb(rows)

        if not kb:
            await update.message.reply_text("Каталог пока пуст 🤷", reply_markup=menu())
            return

        await update.message.reply_text("📚 Каталог", reply_markup=kb)
        return

    if q == "❓ FAQ":
        await update.message.reply_text(
            "Раздел, который обычно никто не читает 🫠\n\n"

            "🔎 Поиск не нашёл макет\n"
            "Это не значит, что его нет.\n"
            "Попробуй другой поисковый запрос.\n\n"

            "🧠 «Я точно видел этот макет»\n"
            "Верю. Что-то точно случилось:\n"
            "— Макет переехал, а дизайнер не обновил ссылку\n"
            "— Это новый макет и его еще не добавили в базу\n"
            "— Никто ещё не догадался, как его назвать нормально\n"
            "Что бы ни случилось — пиши @G2_Schrodinger\n\n"

            "🔐 Нет доступа\n"
            "Свяжись с дизайнером.\n\n"
        )
        return

    if q == "💬 Связаться":
        await update.message.reply_text("@G2_Schrodinger")
        return

    log.info("Query from user %s: %r", uid, q)
    res = search(q)

    if not res:
        await update.message.reply_text("Ничего не найдено", reply_markup=menu())
        return

    await update.message.reply_text("Нашла:")

    for r in res[:5]:
        await update.message.reply_text(
            format_row(r),
            reply_markup=kb_row(r)
        )


# ---------------- CALLBACK ----------------

async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()

    data = q.data or ""
    rows = get_rows()
    log.info("Callback %r", data)

    try:
        if data == "catalog":
            kb = catalog_kb(rows)
            if not kb:
                await q.edit_message_text("Каталог пока пуст 🤷")
                return
            await q.edit_message_text("📚 Каталог", reply_markup=kb)
            return

        if data.startswith("product|"):
            pi = int(data.split("|", 1)[1])
            products = get_products(rows)

            if pi >= len(products):
                await q.edit_message_text("Каталог обновился, открой заново 🔄")
                return

            product = products[pi]
            sections = get_sections(rows, product)

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(s, callback_data=f"section|{pi}|{si}")]
                for si, s in enumerate(sections)
            ] + [[InlineKeyboardButton("← Назад", callback_data="catalog")]])

            await q.edit_message_text(f"📚 {product}", reply_markup=kb)
            return

        if data.startswith("section|"):
            # формат: section|<product_idx>|<section_idx>
            parts = data.split("|")
            pi, si = int(parts[1]), int(parts[2])

            products = get_products(rows)
            if pi >= len(products):
                await q.edit_message_text("Каталог обновился, открой заново 🔄")
                return

            product = products[pi]
            sections = get_sections(rows, product)
            if si >= len(sections):
                await q.edit_message_text("Каталог обновился, открой заново 🔄")
                return

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
                [InlineKeyboardButton("← В каталог", callback_data="catalog")]
            ])

            await q.edit_message_text(
                text,
                reply_markup=kb,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
    except (ValueError, IndexError):
        log.warning("Bad callback data: %r", data)
        await q.edit_message_text("Что-то пошло не так, открой каталог заново 🔄")


# ---------------- ERROR HANDLER ----------------

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled error while processing update", exc_info=context.error)


# ---------------- WEBHOOK FIX ----------------

async def post_init(app):
    for i in range(3):
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
            log.info("Webhook cleared")
            return
        except Exception as e:
            log.warning("Webhook clear attempt %d failed: %s", i + 1, e)
            await asyncio.sleep(2)


# ---------------- LOCK ----------------

def pid_is_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def acquire_lock():
    """Создаёт lock-файл. Если файл есть, но PID мёртв — перехватывает блокировку."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
        except (ValueError, OSError):
            old_pid = None

        if old_pid and pid_is_alive(old_pid):
            log.error("Another instance is running (pid=%s) → exit", old_pid)
            return False

        log.warning("Stale lock file (pid=%s) found — overriding", old_pid)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    log.info("Lock acquired: %s (pid=%s)", PID_FILE, os.getpid())
    return True


def cleanup():
    try:
        os.remove(PID_FILE)
        log.info("Lock released: %s", PID_FILE)
    except OSError:
        pass


# регистрируем cleanup СРАЗУ, до запуска блокирующего polling
atexit.register(cleanup)


# ---------------- MAIN ----------------

def main():
    if not TOKEN:
        log.error("BOT_TOKEN is not set → exit")
        return

    if not acquire_lock():
        return

    try:
        threading.Thread(target=run_server, daemon=True).start()

        app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(catalog))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_handler))
        app.add_error_handler(on_error)

        log.info("BOT STARTED")

        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )
    finally:
        # гарантированно снимаем блокировку при любом завершении polling
        cleanup()


if __name__ == "__main__":
    main()
