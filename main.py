import os
import threading
import requests
import hashlib
import logging
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
url_cache = {}

@flask_app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

TOKEN    = os.getenv('BOT_TOKEN')
GH_TOKEN = os.getenv('GITHUB_TOKEN')
GH_REPO  = os.getenv('GITHUB_REPO')

SUPPORTED_DOMAINS = [
    "youtube.com", "youtu.be",
    "instagram.com", "tiktok.com",
    "twitter.com", "x.com",
    "soundcloud.com", "vimeo.com", "facebook.com",
]

def get_url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:8]

def trigger_github_action(url: str, quality: str, chat_id: str, reply_to_message_id=None) -> bool:
    try:
        api_url = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/download.yml/dispatches"
        headers = {
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload = {
            "ref": "main",
            "inputs": {
                "url":        url,
                "quality":    quality,
                "chat_id":    str(chat_id),
                "bot_token":  TOKEN,
                "message_id": str(reply_to_message_id) if reply_to_message_id else "",
            }
        }
        resp = requests.post(api_url, json=payload, headers=headers, timeout=10)
        logger.info(f"GitHub Action: chat={chat_id}, quality={quality}, status={resp.status_code}")
        return resp.status_code == 204
    except Exception as e:
        logger.error(f"Error triggering GitHub action: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Привет!* Я YouTube-Downloader Bot\n\n"
        "В ЛС: просто отправь мне ссылку.\n"
        "В беседах: напиши моё @имя и ссылку обычным сообщением.\n"
        "(Например: `@твой_бот https://youtube.com/...`)",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Вот тут магия!
    В ЛС — бот реагирует на любые ссылки.
    В БЕСЕДЕ — бот молчит, ПОКА его не упомянут через @.
    """
    text = update.message.text
    if not text:
        return

    chat_type = update.message.chat.type
    bot_username = f"@{context.bot.username}"

    # Если мы в беседе, ищем упоминание бота
    if chat_type in ['group', 'supergroup']:
        # Если бота не упомянули — просто игнорируем сообщение (бот не будет спамить!)
        if bot_username not in text:
            return 

    # Ищем ссылку
    url = None
    for word in text.split():
        if any(domain in word for domain in SUPPORTED_DOMAINS) and "http" in word:
            url = word
            break

    if not url:
        if chat_type == 'private':
            await update.message.reply_text("❌ Сайт не поддерживается.")
        else:
            await update.message.reply_text(f"❌ {update.message.from_user.first_name}, вы упомянули меня, но я не нашел поддерживаемую ссылку.")
        return

    # Сохраняем кэш
    url_hash = get_url_hash(url)
    url_cache[url_hash] = url
    context.application.bot_data[url_hash] = url

    # Кнопки качества (Inline Keyboard)
    keyboard = [
        [InlineKeyboardButton("📺 720p",  callback_data=f'd_{url_hash}_720'),
         InlineKeyboardButton("📱 480p",  callback_data=f'd_{url_hash}_480')],
        [InlineKeyboardButton("🎵 MP3", callback_data=f'd_{url_hash}_mp3')]
    ]

    # Отправляем в чат!
    await update.message.reply_text(
        "🎛 *Выбери качество:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown',
        reply_to_message_id=update.message.message_id
    )

async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия на кнопки под сообщением"""
    try:
        query = update.callback_query
        await query.answer("⏳ Запускаю...", show_alert=False)

        parts = query.data.split('_')
        if len(parts) < 3:
            return
            
        url_hash, quality = parts[1], parts[2]

        url = context.application.bot_data.get(url_hash) or url_cache.get(url_hash)
        if not url:
            await query.edit_message_text("⚠️ Ссылка устарела. Отправь её заново.")
            return

        # Поскольку кнопки прикреплены к обычному сообщению, query.message ВСЕГДА существует!
        if query.message:
            chat_id = query.message.chat_id
            message_id = query.message.message_id
            
            await query.edit_message_text(f"⏳ Скачиваю {quality}...\nВидео придёт прямо сюда через 1-2 минуты.")
            
            # Запускаем GitHub Action, передав ID беседы!
            success = trigger_github_action(url, quality, chat_id, message_id)
            if not success:
                await context.bot.send_message(chat_id, "❌ Не удалось запустить задачу на GitHub.")

    except Exception as e:
        logger.error(f"download_callback error: {e}", exc_info=True)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    
    # Обработчик кнопок
    app.add_handler(CallbackQueryHandler(download_callback, pattern='^d_'))
    
    # Единый обработчик текста (он сам разберется, беседа это или ЛС)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()