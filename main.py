import os
import re
import json
import sqlite3
import threading
import requests
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

flask_app = Flask(__name__)

# Инициализация SQLite
DB_PATH = 'history.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            quality TEXT NOT NULL,
            duration TEXT,
            uploader TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def save_to_history(chat_id, url, title, quality, duration, uploader):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO history (chat_id, url, title, quality, duration, uploader)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (chat_id, url, title, quality, duration, uploader))
    conn.commit()
    conn.close()

def get_history(chat_id, limit=10):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT title, quality, duration, url FROM history 
        WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?
    ''', (chat_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return rows

@flask_app.route('/')
def home():
    return "I'm alive!"

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Получает метаданные от GitHub Actions для сохранения в историю."""
    data = request.json
    chat_id = data.get('chat_id')
    url = data.get('url')
    title = data.get('title', 'Unknown')
    quality = data.get('quality')
    duration = data.get('duration', 'N/A')
    uploader = data.get('uploader', 'Unknown')
    
    if chat_id and url and quality:
        save_to_history(chat_id, url, title, quality, duration, uploader)
    
    return jsonify({'status': 'ok'}), 200

def run_flask():
    init_db()
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

TOKEN       = os.getenv('BOT_TOKEN')
GH_TOKEN    = os.getenv('GITHUB_TOKEN')
GH_REPO     = os.getenv('GITHUB_REPO')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')  # URL вашего бота для webhook

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
            "webhook_url": WEBHOOK_URL or "",
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
        "/help — подробная справка\n"
        "/history — история загрузок\n\n"
        "_Отправь мне ссылку на видео, чтобы начать!_",
        parse_mode='Markdown'
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Справка по боту*\n\n"
        "🎬 *Как скачать видео:*\n"
        "1. Отправь ссылку на видео\n"
        "2. Выбери качество (1080p, 720p, 480p) или MP3\n"
        "3. Дождись файл в Telegram\n\n"
        "⚠️ *Важно:*\n"
        "• Telegram ограничивает размер файла 50 МБ\n"
        "• 1080p видео могут не отправиться из-за размера\n"
        "• Для YouTube используется web-плеер\n\n"
        "🎵 *MP3:*\n"
        "Извлекает аудио из видео в формате MP3\n\n"
        "📋 *Команды:*\n"
        "/start — главное меню\n"
        "/help — эта справка\n"
        "/history — история загрузок",
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
    keyboard = [
        [InlineKeyboardButton("🎬 1080p", callback_data='1080'),
         InlineKeyboardButton("📺 720p", callback_data='720'),
         InlineKeyboardButton("📱 480p", callback_data='480')],
        [InlineKeyboardButton("🎵 MP3", callback_data='mp3')]
    ]
    await update.message.reply_text(
        "🎛 *Выбери качество:*\n\n"
        "_1080p может превышать лимит 50 МБ_",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url = context.user_data.get('current_url')
    quality = query.data
    chat_id = query.message.chat_id

    # Предупреждение для 1080p
    if quality == '1080':
        await query.edit_message_text(
            "⚠️ _Видео в 1080p могут быть больше 50 МБ и не отправятся в Telegram._\n\n"
            "*Продолжить?*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Да", callback_data='1080_confirm')],
                [InlineKeyboardButton("❌ Нет", callback_data='cancel')]
            ]),
            parse_mode='Markdown'
        )
        return

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


async def button_callback_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url = context.user_data.get('current_url')
    chat_id = query.message.chat_id
    quality = query.data

    if quality == '1080_confirm':
        await query.edit_message_text(
            "⏳ _Запускаю скачивание в 1080p..._\n\n"
            "📊 _Файл придёт через 1-2 минуты._",
            parse_mode='Markdown'
        )
        # Преобразуем 1080_confirm -> 1080 перед отправкой
        success = trigger_github_action(url, '1080', chat_id)
        if not success:
            await context.bot.send_message(
                chat_id,
                "❌ _Не удалось запустить задачу._\n\n"
                "_Проверь настройки GITHUB_TOKEN и GITHUB_REPO._",
                parse_mode='Markdown'
            )
    elif quality == 'cancel':
        await query.edit_message_text("❌ _Отменено._", parse_mode='Markdown')
        keyboard = [
            [InlineKeyboardButton("🎬 1080p", callback_data='1080'),
             InlineKeyboardButton("📺 720p", callback_data='720'),
             InlineKeyboardButton("📱 480p", callback_data='480')],
            [InlineKeyboardButton("🎵 MP3", callback_data='mp3')]
        ]
        await context.bot.send_message(
            chat_id,
            "🎛 *Выбери качество:*\n\n"
            "_1080p может превышать лимит 50 МБ_",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    history = get_history(chat_id)
    
    if not history:
        await update.message.reply_text(
            "📋 *История пуста*\n\n"
            "_Пока вы ничего не скачали._",
            parse_mode='Markdown'
        )
        return
    
    text = "📋 *История загрузок:*\n\n"
    for i, (title, quality, duration, url) in enumerate(history, 1):
        # Обрезаем заголовок если слишком длинный
        short_title = title[:35] + "..." if len(title) > 35 else title
        text += f"{i}. {short_title}\n"
        text += f"   📺 {quality}p | ⏱ {duration}\n"
        text += f"   🔗 `{url[:50]}...`\n\n"
    
    # Разбиваем на сообщения если слишком длинное
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        await update.message.reply_text(chunk, parse_mode='Markdown')


if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(CallbackQueryHandler(button_callback_confirm))
    app.run_polling()
