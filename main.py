import os
import threading
import requests
import hashlib
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes

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
        return resp.status_code == 204
    except Exception as e:
        print(f"Error triggering GitHub action: {e}")
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
    """Обработка кнопок в ЛС бота (y_ и o_ префиксы)."""
    try:
        query = update.callback_query
        if query is None:
            return
        await query.answer()

        url = context.user_data.get('current_url')
        if not url:
            await query.edit_message_text("❌ _Ссылка не найдена. Отправьте ссылку ещё раз._", parse_mode='Markdown')
            return

        # Убираем префикс y_ или o_
        q = query.data[2:]
        chat_id = query.message.chat_id

        await query.edit_message_text(
            "⏳ _Запускаю скачивание..._\n📊 _Файл придёт через 1-2 минуты._",
            parse_mode='Markdown'
        )

        success = trigger_github_action(url, q, chat_id)
        if not success:
            await context.bot.send_message(chat_id, "❌ _Не удалось запустить задачу._", parse_mode='Markdown')
    except Exception as e:
        print(f"Error in button_callback: {e}")


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка inline-запросов (@bot в чате)."""
    try:
        query = update.inline_query.query
        if not query:
            return

        url = None
        for word in query.split():
            if any(domain in word for domain in SUPPORTED_DOMAINS):
                url = word
                break

        if not url:
            results = [
                InlineQueryResultArticle(
                    id='1',
                    title='🔍 Введите ссылку на видео',
                    description='YouTube, TikTok, Instagram, Twitter, Vimeo, SoundCloud',
                    input_message_content=InputTextMessageContent(
                        message_text='🔍 _Введите ссылку после @бота_\n\nПример: `@bot https://youtube.com/...`',
                        parse_mode='Markdown'
                    )
                )
            ]
        else:
            youtube = is_youtube(url)
            quality = '1080' if youtube else '720'
            label = '1080p' if youtube else '720p'

            url_hash = get_url_hash(url)
            url_cache[url_hash] = url

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"📥 Скачать {label}", callback_data=f'd_{url_hash}_{quality}')
            ]])

            short_url = url[:40] + '...' if len(url) > 40 else url

            results = [
                InlineQueryResultArticle(
                    id=url_hash,
                    title=f'📥 Скачать видео ({label})',
                    description='Нажми кнопку для скачивания',
                    input_message_content=InputTextMessageContent(
                        message_text=f"🎬 _Видео для скачивания:_\n\n🔗 `{short_url}`\n\n_Нажми кнопку ниже 👇_",
                        parse_mode='Markdown'
                    ),
                    reply_markup=keyboard
                )
            ]

        await update.inline_query.answer(results, cache_time=0)
    except Exception as e:
        print(f"Error in inline_query: {e}")


async def inline_download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки скачивания из inline-режима (префикс d_)."""
    try:
        query = update.callback_query
        if query is None:
            return

        await query.answer("⏳ Запускаю...", show_alert=False)

        data = query.data
        # Парсим: d_{url_hash}_{quality}
        parts = data[2:].split('_')   # убираем 'd_', делим остаток
        if len(parts) < 2:
            await query.edit_message_text("❌ _Ошибка формата запроса._", parse_mode='Markdown')
            return

        url_hash = parts[0]
        quality  = parts[1]

        url = url_cache.get(url_hash)
        if not url:
            await query.edit_message_text(
                "⚠️ _Ссылка устарела. Попробуйте отправить запрос ещё раз._",
                parse_mode='Markdown'
            )
            return

        chat_id = query.message.chat_id

        await query.edit_message_text(
            f"⏳ _Запускаю скачивание в {quality}..._\n📊 _Файл придёт через 1-2 минуты._\n\n🔗 `{url[:40]}...`",
            parse_mode='Markdown'
        )

        success = trigger_github_action(url, quality, chat_id)
        if not success:
            await context.bot.send_message(chat_id, "❌ _Не удалось запустить задачу._", parse_mode='Markdown')
    except Exception as e:
        print(f"Error in inline_download_callback: {e}")


if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(InlineQueryHandler(inline_query))

    # ВАЖНО: сначала конкретный паттерн d_, потом общий — иначе button_callback перехватит всё
    app.add_handler(CallbackQueryHandler(inline_download_callback, pattern='^d_'))
    app.add_handler(CallbackQueryHandler(button_callback, pattern='^[yo]_'))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()