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

def is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url

def get_url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:8]

def trigger_github_action(url: str, quality: str, chat_id: str, reply_to_message_id=None) -> bool:
    """Запускает workflow. reply_to_message_id — ID сообщения в беседе для ответа."""
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
        "_Отправь ссылку на видео или используй @ в любом чате!_",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Справка*\n\n"
        "1. Отправь ссылку боту в ЛС или @бот в чате\n"
        "2. Выбери качество\n"
        "3. Получи файл\n\n"
        "⚠️ Лимит Telegram — 50 МБ\n"
        "1080p только для YouTube",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not any(domain in url for domain in SUPPORTED_DOMAINS):
        await update.message.reply_text("❌ Сайт не поддерживается.")
        return

    context.user_data['current_url'] = url

    if is_youtube(url):
        keyboard = [
            [InlineKeyboardButton("📺 720p",  callback_data='y_720'),
             InlineKeyboardButton("📱 480p",  callback_data='y_480')],
            [InlineKeyboardButton("🎵 MP3", callback_data='y_mp3')]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("📺 720p", callback_data='o_720'),
             InlineKeyboardButton("📱 480p", callback_data='o_480')],
            [InlineKeyboardButton("🎵 MP3", callback_data='o_mp3')]
        ]

    await update.message.reply_text(
        "🎛 *Выбери качество:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопки в ЛС (y_ и o_) — reply_to_message_id не нужен."""
    try:
        query = update.callback_query
        await query.answer()
        url = context.user_data.get('current_url')
        if not url:
            await query.edit_message_text("❌ Ссылка не найдена. Отправь ещё раз.")
            return
        q = query.data[2:]
        chat_id = query.message.chat_id
        await query.edit_message_text("⏳ Запускаю скачивание...\nФайл придёт через 1-2 минуты.")
        success = trigger_github_action(url, q, chat_id)
        if not success:
            await context.bot.send_message(chat_id, "❌ Не удалось запустить задачу.")
    except Exception as e:
        logger.error(f"button_callback error: {e}", exc_info=True)

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline-запросы (@bot ссылка в чате)."""
    try:
        query_text = update.inline_query.query
        logger.info(f"Inline query: '{query_text}'")
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

        youtube = is_youtube(url)
        quality = '720'
        label   = '720p'
        short_url = url[:50] + '...' if len(url) > 50 else url

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"📥 Скачать {label}", callback_data=f'd_{url_hash}_{quality}')
        ]])

        results = [InlineQueryResultArticle(
            id=url_hash,
            title=f'📥 Скачать видео ({label})',
            description=short_url,
            input_message_content=InputTextMessageContent(
                message_text=url
            ),
            reply_markup=keyboard
        )]

        await update.inline_query.answer(results, cache_time=0)
        logger.info(f"Inline result sent: hash={url_hash}")

    except Exception as e:
        logger.error(f"inline_query error: {e}", exc_info=True)

async def inline_download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка из inline-сообщения в беседе (d_). Отвечаем в ТУ ЖЕ беседу."""
    try:
        query = update.callback_query
        logger.info(f"inline_download_callback: data={query.data}")
        await query.answer("⏳ Запускаю...", show_alert=False)

        parts = query.data.split('_')  # ['d', hash, quality]
        if len(parts) < 3:
            await query.edit_message_text("❌ Ошибка формата.")
            return

        url_hash = parts[1]
        quality  = parts[2]

        url = context.application.bot_data.get(url_hash) or url_cache.get(url_hash)
        if not url:
            logger.warning(f"URL not found for hash={url_hash}")
            await query.edit_message_text(
                "⚠️ Ссылка устарела (бот перезапускался).\n"
                "Отправь ссылку боту в ЛС, нажми кнопку — файл придёт сюда в беседу."
            )
            return

        # chat_id беседы где нажали кнопку
        if query.message:
            chat_id    = query.message.chat_id
            message_id = query.message.message_id  # ID сообщения в беседе — для reply
        else:
            chat_id    = query.from_user.id
            message_id = None

        logger.info(f"Sending to chat_id={chat_id}, reply_to={message_id}")

        await query.edit_message_text(
            f"⏳ Скачиваю {quality}...\nФайл придёт сюда через 1-2 минуты."
        )

        # Передаём message_id — workflow ответит reply на это сообщение в беседе
        success = trigger_github_action(url, quality, chat_id, message_id)
        if not success:
            await context.bot.send_message(chat_id, "❌ Не удалось запустить задачу.")

    except Exception as e:
        logger.error(f"inline_download_callback error: {e}", exc_info=True)
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
    app.add_handler(CallbackQueryHandler(inline_download_callback, pattern='^d_'))
    app.add_handler(CallbackQueryHandler(button_callback, pattern='^[yo]_'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_message))
    app.run_polling()