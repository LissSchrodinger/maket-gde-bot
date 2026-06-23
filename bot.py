import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters


TOKEN = os.getenv("BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот «А где макет?»\n\n"
        "Пока я только учусь искать макеты, но уже умею отвечать 🙂\n"
        "Напиши мне любой текст."
    )


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await update.message.reply_text(
        f"Я получил запрос:\n\n«{user_text}»\n\n"
        "Скоро буду искать по нему макеты."
    )


async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    await app.run_polling()


import asyncio

if __name__ == "__main__":
    asyncio.run(main())
