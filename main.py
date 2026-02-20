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

def trigger_github_action(url, quality, chat_id):
    github_token = "ТВОЙ_GITHUB_PERSONAL_ACCESS_TOKEN" # Нужно создать в настройках GitHub
    repo = "vittinhagml11/yt_downloader"
    
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    data = {
        "event_type": "download_video", # Должно совпадать с 'types' в YAML
        "client_payload": {
            "url": url,
            "quality": quality,
            "chat_id": chat_id
        }
    }
    
    response = requests.post(
        f"https://api.github.com/repos/{repo}/dispatches",
        json=data,
        headers=headers
    )
    return response.status_code

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Пришли ссылку на YouTube-видео.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("Это не ссылка на YouTube.")
        return
    context.user_data['current_url'] = url
    keyboard = [
        [InlineKeyboardButton("720p", callback_data='720'),
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

    await query.edit_message_text("⏳ Запускаю скачивание... Файл придёт через 1-2 минуты.")

    success = trigger_github_action(url, quality, chat_id)
    if not success:
        await context.bot.send_message(chat_id, "❌ Не удалось запустить задачу. Проверь настройки GITHUB_TOKEN и GITHUB_REPO.")


if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()