import os
import threading
import requests
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

TOKEN       = os.getenv('BOT_TOKEN')
GH_TOKEN    = os.getenv('GITHUB_TOKEN')   # Personal Access Token с правом actions:write
GH_REPO     = os.getenv('GITHUB_REPO')    # формат: username/repo-name

SUPPORTED_DOMAINS = [
    "youtube.com", "youtu.be",
    "instagram.com",
    "tiktok.com",
    "twitter.com", "x.com",
    "soundcloud.com",
    "vimeo.com",
    "facebook.com",
]

def trigger_github_action(url: str, quality: str, chat_id: str) -> bool:
    """Запускает workflow через GitHub API."""
    api_url = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/download.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "ref": "main",
        "inputs": {
            "url":       url,
            "quality":   quality,
            "chat_id":   str(chat_id),
            "bot_token": TOKEN,
        }
    }
    resp = requests.post(api_url, json=payload, headers=headers, timeout=10)
    return resp.status_code == 204


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Пришли ссылку на видео (YouTube, Instagram, TikTok, Twitter, Vimeo, SoundCloud, Facebook).")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not any(domain in url for domain in SUPPORTED_DOMAINS):
        await update.message.reply_text("❌ Сайт не поддерживается.")
        return
    context.user_data['current_url'] = url
    keyboard = [
        [InlineKeyboardButton("1080p", callback_data='1080'),
         InlineKeyboardButton("720p", callback_data='720'),
         InlineKeyboardButton("480p", callback_data='480')],
        [InlineKeyboardButton("MP3", callback_data='mp3')]
    ]
    await update.message.reply_text("Выбери качество:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url     = context.user_data.get('current_url')
    quality = query.data
    chat_id = query.message.chat_id

    # Предупреждение для 1080p
    if quality == '1080':
        await query.edit_message_text(
            "⚠️ Видео в 1080p могут быть больше 50 МБ и не отправятся в Telegram. Продолжить?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Да", callback_data='1080_confirm')],
                [InlineKeyboardButton("Нет", callback_data='cancel')]
            ])
        )
        return

    await query.edit_message_text("⏳ Запускаю скачивание... Файл придёт через 1-2 минуты.")

    success = trigger_github_action(url, quality, chat_id)
    if not success:
        await context.bot.send_message(chat_id, "❌ Не удалось запустить задачу. Проверь настройки GITHUB_TOKEN и GITHUB_REPO.")

async def button_callback_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url = context.user_data.get('current_url')
    chat_id = query.message.chat_id
    quality = query.data

    if quality == '1080_confirm':
        await query.edit_message_text("⏳ Запускаю скачивание в 1080p... Файл придёт через 1-2 минуты.")
        success = trigger_github_action(url, '1080', chat_id)
        if not success:
            await context.bot.send_message(chat_id, "❌ Не удалось запустить задачу. Проверь настройки GITHUB_TOKEN и GITHUB_REPO.")
    elif quality == 'cancel':
        await query.edit_message_text("❌ Отменено.")
        keyboard = [
            [InlineKeyboardButton("1080p", callback_data='1080'),
             InlineKeyboardButton("720p", callback_data='720'),
             InlineKeyboardButton("480p", callback_data='480')],
            [InlineKeyboardButton("MP3", callback_data='mp3')]
        ]
        await context.bot.send_message(chat_id, "Выбери качество:", reply_markup=InlineKeyboardMarkup(keyboard))


if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CallbackQueryHandler(button_callback_confirm))
    app.run_polling()
