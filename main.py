import os
import threading
import requests
import hashlib
import logging
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes

# Логирование — видно в Render logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

# Кэш URL: хэш → полный URL
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
    "instagram.com",
    "tiktok.com",
    "twitter.com", "x.com",
    "soundcloud.com",
    "vimeo.com",
    "facebook.com",
]

def is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url

def get_url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:8]

def trigger_github_action(url: str, quality: str, chat_id: str) -> bool:
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
                "url":       url,
                "quality":   quality,
                "chat_id":   str(chat_id),
                "bot_token": TOKEN,
            }
        }
        resp = requests.post(api_url, json=payload, headers=headers, timeout=10)
        logger.info(f"GitHub Action triggered: status={resp.status_code}, chat_id={chat_id}, quality={quality}")
        return resp.status_code == 204
    except Exception as e:
        logger.error(f"Error triggering GitHub action: {e}")
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Привет!* Я YouTube-Downloader Bot\n\n"
        "📺 *Поддерживаемые сайты:*\n"
        "• YouTube\n• Instagram\n• TikTok\n• Twitter/X\n• Vimeo\n• SoundCloud\n• Facebook\n\n"
        "🎛 *Команды:*\n"
        "/help — подробная справка\n\n"
        "_Отправь мне ссылку на видео, чтобы начать!_\n\n"
        "💬 _Используй @ в любом чате, чтобы скачать видео!_",
        parse_mode='Markdown'
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Справка по боту*\n\n"
        "🎬 *Как скачать видео:*\n"
        "1. Отправь ссылку в ЛС боту или используй @ в чате\n"
        "2. Нажми кнопку для скачивания\n"
        "3. Дождись файл в Telegram\n\n"
        "⚠️ *Важно:*\n"
        "• Telegram ограничивает размер файла 50 МБ\n"
        "• 1080p доступно только для YouTube\n\n"
        "📋 *Команды:*\n"
        "/start — главное меню\n"
        "/help — эта справка",
        parse_mode='Markdown'
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text

    if not any(domain in url for domain in SUPPORTED_DOMAINS):
        await update.message.reply_text("❌ _Сайт не поддерживается._", parse_mode='Markdown')
        return

    context.user_data['current_url'] = url
    youtube = is_youtube(url)

    if youtube:
        keyboard = [
            [InlineKeyboardButton("🎬 1080p", callback_data='y_1080'),
             InlineKeyboardButton("📺 720p",  callback_data='y_720'),
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
    """Кнопки в ЛС бота (y_ и o_)."""
    try:
        query = update.callback_query
        await query.answer()

        url = context.user_data.get('current_url')
        if not url:
            await query.edit_message_text("❌ _Ссылка не найдена. Отправьте ссылку ещё раз._", parse_mode='Markdown')
            return

        q = query.data[2:]  # убираем y_ или o_
        chat_id = query.message.chat_id

        await query.edit_message_text(
            "⏳ _Запускаю скачивание..._\n📊 _Файл придёт через 1-2 минуты._",
            parse_mode='Markdown'
        )

        success = trigger_github_action(url, q, chat_id)
        if not success:
            await context.bot.send_message(chat_id, "❌ _Не удалось запустить задачу._", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in button_callback: {e}")


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline-запросы (@bot ссылка)."""
    try:
        query_text = update.inline_query.query
        logger.info(f"Inline query received: '{query_text}'")

        if not query_text:
            return

        url = None
        for word in query_text.split():
            if any(domain in word for domain in SUPPORTED_DOMAINS):
                url = word
                break

        if not url:
            results = [
                InlineQueryResultArticle(
                    id='help',
                    title='🔍 Введите ссылку на видео',
                    description='YouTube, TikTok, Instagram, Twitter, Vimeo, SoundCloud',
                    input_message_content=InputTextMessageContent(
                        message_text='Отправь ссылку боту в ЛС или напиши @bot https://youtube.com/...',
                    )
                )
            ]
            await update.inline_query.answer(results, cache_time=0)
            return

        youtube = is_youtube(url)
        url_hash = get_url_hash(url)
        url_cache[url_hash] = url
        logger.info(f"Cached URL: hash={url_hash}, url={url[:50]}")

        # Формируем callback_data с URL прямо внутри — не зависим от кэша
        # Telegram ограничивает callback_data до 64 байт, поэтому кодируем качество + хэш
        # URL передаём через хэш, но также дублируем в bot_data (context.application)
        context.application.bot_data[url_hash] = url

        youtube = is_youtube(url)
        quality = '1080' if youtube else '720'
        label = '1080p' if youtube else '720p'
        short_url = url[:50] + '...' if len(url) > 50 else url

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"📥 Скачать {label}",
                callback_data=f'd_{url_hash}_{quality}'
            )
        ]])

        results = [
            InlineQueryResultArticle(
                id=url_hash,
                title=f'📥 Скачать видео ({label})',
                description=short_url,
                input_message_content=InputTextMessageContent(
                    message_text=f"🎬 Видео для скачивания:\n{url}\n\nНажми кнопку ниже 👇",
                ),
                reply_markup=keyboard
            )
        ]

        await update.inline_query.answer(results, cache_time=0)
        logger.info(f"Inline results sent for url_hash={url_hash}")

    except Exception as e:
        logger.error(f"Error in inline_query: {e}", exc_info=True)


async def inline_download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка скачивания из inline-сообщения (d_)."""
    try:
        query = update.callback_query
        logger.info(f"inline_download_callback triggered: data={query.data}")

        await query.answer("⏳ Запускаю...", show_alert=False)

        # Парсим d_{url_hash}_{quality}
        parts = query.data.split('_')
        # parts = ['d', url_hash, quality]
        if len(parts) < 3:
            logger.error(f"Bad callback data format: {query.data}")
            await query.edit_message_text("❌ Ошибка формата запроса.")
            return

        url_hash = parts[1]
        quality  = parts[2]
        logger.info(f"Parsed: url_hash={url_hash}, quality={quality}")

        # Ищем URL — сначала в bot_data (надёжнее), потом в url_cache
        url = context.application.bot_data.get(url_hash) or url_cache.get(url_hash)
        logger.info(f"URL from cache: {url}")

        if not url:
            logger.warning(f"URL not found for hash={url_hash}")
            await query.edit_message_text(
                "⚠️ Ссылка устарела — бот перезапускался. Отправь ссылку боту в ЛС заново."
            )
            return

        # В inline-режиме query.message — это сообщение в чужом чате
        # chat_id берём оттуда, если доступно
        if query.message:
            chat_id = query.message.chat_id
            logger.info(f"chat_id from message: {chat_id}")
        else:
            # Если message недоступен — используем from_user (личка пользователя)
            chat_id = query.from_user.id
            logger.info(f"chat_id from from_user: {chat_id}")

        await query.edit_message_text(
            f"⏳ Запускаю скачивание {quality}...\nФайл придёт через 1-2 минуты.\n\n🔗 {url[:60]}"
        )

        success = trigger_github_action(url, quality, chat_id)
        if not success:
            await context.bot.send_message(chat_id, "❌ Не удалось запустить задачу. Попробуй ещё раз.")

    except Exception as e:
        logger.error(f"Error in inline_download_callback: {e}", exc_info=True)
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

    # ВАЖНО: d_ должен быть ПЕРВЫМ — иначе button_callback перехватит
    app.add_handler(CallbackQueryHandler(inline_download_callback, pattern='^d_'))
    app.add_handler(CallbackQueryHandler(button_callback, pattern='^[yo]_'))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()