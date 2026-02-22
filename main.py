import os
import threading
import requests
import hashlib
import logging
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask для поддержания работы (keep-alive)
flask_app = Flask(__name__)
url_cache = {}

@flask_app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

# Переменные окружения
TOKEN    = os.getenv('BOT_TOKEN')
GH_TOKEN = os.getenv('GITHUB_TOKEN')
GH_REPO  = os.getenv('GITHUB_REPO')

SUPPORTED_DOMAINS = [
    "youtube.com", "youtu.be",
    "instagram.com", "tiktok.com",
    "twitter.com", "x.com",
    "soundcloud.com", "vimeo.com", "facebook.com",
]

def is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url

def get_url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:8]

def trigger_github_action(url: str, quality: str, chat_id: str, reply_to_message_id=None) -> bool:
    """Запускает workflow в GitHub Actions."""
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
        logger.info(f"GitHub Action: status={resp.status_code}, chat={chat_id}, quality={quality}, reply_to={reply_to_message_id}")
        return resp.status_code == 204
    except Exception as e:
        logger.error(f"Error triggering GitHub action: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Привет!* Я YouTube-Downloader Bot\n\n"
        "📺 *Поддерживаемые сайты:*\n"
        "• YouTube\n• Instagram\n• TikTok\n• Twitter/X\n• Vimeo\n• SoundCloud\n• Facebook\n\n"
        "/help — подробная справка\n\n"
        "В ЛС: просто отправь ссылку.\n"
        "В беседах: напиши моё имя и ссылку (например: `@бот https://...`)",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Справка*\n\n"
        "В ЛС: Просто кидай ссылку.\n"
        "В группе: Напиши `@имя_бота ссылка` и отправь как обычное сообщение.\n\n"
        "⚠️ Лимит Telegram — 50 МБ\n"
        "1080p только для YouTube",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения. В беседах реагирует ТОЛЬКО на упоминание @бота."""
    text = update.message.text or update.message.caption
    if not text:
        return

    chat_type = update.message.chat.type
    bot_username = f"@{context.bot.username}"

    # Если это беседа, проверяем, упомянули ли бота
    if chat_type in ['group', 'supergroup']:
        if bot_username not in text:
            return  # Игнорируем сообщения без упоминания бота

    url = None
    
    # Ищем ссылку среди текста
    for word in text.split():
        if any(domain in word for domain in SUPPORTED_DOMAINS) and "http" in word:
            url = word
            break

    # Если ссылку не нашли
    if not url:
        if chat_type == 'private':
            await update.message.reply_text("❌ Сайт не поддерживается или ссылка не найдена.")
        else:
            await update.message.reply_text("❌ Укажите поддерживаемую ссылку вместе с моим именем.")
        return

    # Сохраняем ссылку по хэшу
    url_hash = get_url_hash(url)
    url_cache[url_hash] = url
    context.application.bot_data[url_hash] = url

    # Формируем клавиатуру
    keyboard = [
        [InlineKeyboardButton("📺 720p",  callback_data=f'd_{url_hash}_720'),
         InlineKeyboardButton("📱 480p",  callback_data=f'd_{url_hash}_480')],
        [InlineKeyboardButton("🎵 MP3", callback_data=f'd_{url_hash}_mp3')]
    ]

    await update.message.reply_text(
        "🎛 *Выбери качество:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown',
        reply_to_message_id=update.message.message_id
    )

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline-запросы (@bot ссылка и ожидание всплывающего окна)."""
    try:
        query_text = update.inline_query.query
        if not query_text:
            return

        url = None
        for word in query_text.split():
            if any(domain in word for domain in SUPPORTED_DOMAINS):
                url = word
                break

        if not url:
            results = [InlineQueryResultArticle(
                id='help',
                title='🔍 Введите ссылку на видео',
                description='YouTube, TikTok, Instagram, Twitter, Vimeo, SoundCloud',
                input_message_content=InputTextMessageContent(
                    message_text='Напиши @bot https://youtube.com/...'
                )
            )]
            await update.inline_query.answer(results, cache_time=0)
            return

        url_hash = get_url_hash(url)
        url_cache[url_hash] = url
        context.application.bot_data[url_hash] = url

        label   = '720p'
        short_url = url[:50] + '...' if len(url) > 50 else url

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"📥 Скачать {label}", callback_data=f'd_{url_hash}_720')
        ]])

        results = [InlineQueryResultArticle(
            id=url_hash,
            title=f'📥 Скачать видео ({label})',
            description=short_url,
            input_message_content=InputTextMessageContent(message_text=url),
            reply_markup=keyboard
        )]

        await update.inline_query.answer(results, cache_time=0)

    except Exception as e:
        logger.error(f"inline_query error: {e}", exc_info=True)

async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Единый обработчик для всех кнопок скачивания (в ЛС, группах и inline)."""
    try:
        query = update.callback_query
        await query.answer("⏳ Запускаю...", show_alert=False)

        parts = query.data.split('_')  # ['d', hash, quality]
        if len(parts) < 3:
            await query.edit_message_text("❌ Ошибка формата.")
            return

        url_hash = parts[1]
        quality  = parts[2]

        url = context.application.bot_data.get(url_hash) or url_cache.get(url_hash)
        if not url:
            await query.edit_message_text("⚠️ Ссылка устарела. Отправь её заново.")
            return

        if query.message:
            # Сценарий 1: Обычное сообщение в беседе или ЛС (когда написали @бот ссылка и отправили)
            chat_id    = query.message.chat_id
            message_id = query.message.message_id
            
            await query.edit_message_text(f"⏳ Скачиваю {quality}...\nФайл придёт сюда через 1-2 минуты.")
            
            success = trigger_github_action(url, quality, chat_id, message_id)
            if not success:
                await context.bot.send_message(chat_id, "❌ Не удалось запустить задачу.")
        else:
            # Сценарий 2: INLINE РЕЖИМ (когда выбрали из всплывающего окошка над клавиатурой)
            chat_id    = query.from_user.id
            message_id = None
            
            await query.edit_message_text(
                f"⏳ Скачиваю {quality}...\n\n"
                "⚠️ *Ограничение Telegram:* При использовании всплывающего окна я не вижу ID беседы.\n"
                "👉 **Видео отправлено тебе в личные сообщения!**\n\n"
                "_Совет: Просто напиши моё имя и ссылку обычным сообщением, и я отправлю видео прямо в чат!_",
                parse_mode="Markdown"
            )
            
            success = trigger_github_action(url, quality, chat_id, message_id)
            if not success:
                await context.bot.send_message(chat_id, "❌ Не удалось запустить задачу.")

    except Exception as e:
        logger.error(f"download_callback error: {e}", exc_info=True)
        try:
            await query.edit_message_text(f"❌ Ошибка: {e}")
        except:
            pass

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(InlineQueryHandler(inline_query))
    
    # Обработчик кнопок (скачивания)
    app.add_handler(CallbackQueryHandler(download_callback, pattern='^d_'))
    
    # Слушаем обычные текстовые сообщения (теперь с проверкой упоминания для бесед)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()