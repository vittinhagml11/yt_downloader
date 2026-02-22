import os
import threading
import requests
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

TOKEN       = os.getenv('BOT_TOKEN')
GH_TOKEN    = os.getenv('GITHUB_TOKEN')
GH_REPO     = os.getenv('GITHUB_REPO')

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
    await update.message.reply_text(
        "👋 *Привет!* Я YouTube-Downloader Bot\n\n"
        "📺 *Поддерживаемые сайты:*\n"
        "• YouTube\n• Instagram\n• TikTok\n• Twitter/X\n• Vimeo\n• SoundCloud\n• Facebook\n\n"
        "🎛 *Команды:*\n"
        "/help — подробная справка\n\n"
        "_Отправь мне ссылку на видео, чтобы начать!_\n\n"
        "💬 _Используй @ в любом чате, чтобы скачать видео прямо в беседу!_",
        parse_mode='Markdown'
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Справка по боту*\n\n"
        "🎬 *Как скачать видео:*\n"
        "1. Отправь ссылку на видео\n"
        "2. Выбери качество (1080p, 720p, 480p) или MP3\n"
        "3. Дождись файл в Telegram\n\n"
        "💬 *Inline-режим:*\n"
        "В любом чате введи @username_bot и ссылку — бот предложит скачать видео\n\n"
        "⚠️ *Важно:*\n"
        "• Telegram ограничивает размер файла 50 МБ\n"
        "• 1080p доступно только для YouTube\n"
        "• Для YouTube используется web-плеер\n\n"
        "🎵 *MP3:*\n"
        "Извлекает аудио из видео в формате MP3\n\n"
        "📋 *Команды:*\n"
        "/start — главное меню\n"
        "/help — эта справка",
        parse_mode='Markdown'
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    
    # Проверка на поддерживаемые домены
    if not any(domain in url for domain in SUPPORTED_DOMAINS):
        await update.message.reply_text(
            "❌ _Сайт не поддерживается._\n\n"
            "_Отправь ссылку на YouTube, Instagram, TikTok, Twitter, Vimeo, SoundCloud или Facebook._",
            parse_mode='Markdown'
        )
        return
    
    context.user_data['current_url'] = url
    youtube = is_youtube(url)
    
    # 1080p только для YouTube
    if youtube:
        keyboard = [
            [InlineKeyboardButton("🎬 1080p", callback_data='1080'),
             InlineKeyboardButton("📺 720p", callback_data='720'),
             InlineKeyboardButton("📱 480p", callback_data='480')],
            [InlineKeyboardButton("🎵 MP3", callback_data='mp3')]
        ]
        caption = "🎛 *Выбери качество:*\n\n_1080p может превышать лимит 50 МБ_"
    else:
        keyboard = [
            [InlineKeyboardButton("📺 720p", callback_data='720'),
             InlineKeyboardButton("📱 480p", callback_data='480')],
            [InlineKeyboardButton("🎵 MP3", callback_data='mp3')]
        ]
        caption = "🎛 *Выбери качество:*\n\n_1080p недоступен для этого сайта_"
    
    await update.message.reply_text(caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url = context.user_data.get('current_url')
    quality = query.data
    chat_id = query.message.chat_id

    await query.edit_message_text(
        "⏳ _Запускаю скачивание..._\n\n"
        "📊 _Файл придёт через 1-2 минуты._",
        parse_mode='Markdown'
    )

    success = trigger_github_action(url, quality, chat_id)
    if not success:
        await context.bot.send_message(
            chat_id,
            "❌ _Не удалось запустить задачу._\n\n"
            "_Проверь настройки GITHUB_TOKEN и GITHUB_REPO._",
            parse_mode='Markdown'
        )


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка inline-запросов (@bot в чате)."""
    query = update.inline_query.query
    if not query:
        return
    
    # Ищем URL в запросе
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
                description='Поддерживаются: YouTube, TikTok, Instagram, Twitter, Vimeo, SoundCloud',
                input_message_content=InputTextMessageContent(
                    message_text='🔍 _Введите ссылку на видео после @бота_',
                    parse_mode='Markdown'
                )
            )
        ]
    else:
        youtube = is_youtube(url)
        if youtube:
            desc = "🎬 YouTube — доступно 1080p, 720p, 480p, MP3"
        else:
            desc = "📺 Другой сайт — доступно 720p, 480p, MP3"
        
        results = [
            InlineQueryResultArticle(
                id='1',
                title='🎬 Скачать видео',
                description=desc,
                input_message_content=InputTextMessageContent(
                    message_text=f'⏳ _Скачиваю видео:_ {url[:50]}...',
                    parse_mode='Markdown'
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎬 1080p" if youtube else "📺 720p", callback_data=f'inline_1080_{url}' if youtube else f'inline_720_{url}'),
                     InlineKeyboardButton("📺 720p", callback_data=f'inline_720_{url}')],
                    [InlineKeyboardButton("📱 480p", callback_data=f'inline_480_{url}'),
                     InlineKeyboardButton("🎵 MP3", callback_data=f'inline_mp3_{url}')]
                ])
            )
        ]
    
    await update.inline_query.answer(results, cache_time=0)


async def inline_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок из inline-режима."""
    query = update.callback_query
    await query.answer()
    
    # Парсим callback_data: inline_{quality}_{url}
    data = query.data
    if not data.startswith('inline_'):
        return
    
    parts = data.split('_', 2)
    if len(parts) < 3:
        return
    
    quality = parts[1]
    url = parts[2]
    chat_id = query.message.chat_id
    
    # Для не-Youtube сайтов 1080p недоступен
    if not is_youtube(url) and quality == '1080':
        quality = '720'
    
    await query.edit_message_text(
        f"⏳ _Запускаю скачивание..._\n\n📊 _Файл придёт через 1-2 минуты._\n\n🔗 `{url[:50]}...`",
        parse_mode='Markdown'
    )
    
    success = trigger_github_action(url, quality, chat_id)
    if not success:
        await context.bot.send_message(
            chat_id,
            "❌ _Не удалось запустить задачу._",
            parse_mode='Markdown'
        )


if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CallbackQueryHandler(inline_button_callback, pattern='^inline_'))
    app.run_polling()
