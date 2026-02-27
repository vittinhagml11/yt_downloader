import os
import threading
import requests
import hashlib
import logging
import sqlite3
import base64
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Setup ---
DB_NAME = 'bot_database.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            joined_date TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            url TEXT,
            quality TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def register_user(chat_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (chat_id, joined_date) VALUES (?, ?)', 
                   (chat_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def record_download(chat_id: int, url: str, quality: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO downloads (chat_id, url, quality, timestamp) VALUES (?, ?, ?, ?)',
                   (chat_id, url, quality, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('SELECT COUNT(*) FROM downloads WHERE timestamp LIKE ?', (f'{today}%',))
    downloads_today = cursor.fetchone()[0]
    
    conn.close()
    return total_users, downloads_today

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id FROM users')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users


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
ADMIN_ID = os.getenv('ADMIN_ID')

SUPPORTED_DOMAINS = [
    "youtube.com", "youtu.be",
    "instagram.com", "tiktok.com",
    "twitter.com", "x.com",
    "soundcloud.com", "vimeo.com", "facebook.com",
]

def get_url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:8]

def update_github_file(content: bytes, filename: str = 'cookies.txt') -> bool:
    try:
        url = f"https://api.github.com/repos/{GH_REPO}/contents/{filename}"
        headers = {
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # First get the sha of the existing file
        resp = requests.get(url, headers=headers, timeout=10)
        sha = resp.json().get('sha') if resp.status_code == 200 else None
        
        payload = {
            "message": f"Update {filename} via Telegram bot",
            "content": base64.b64encode(content).decode('utf-8'),
            "branch": "main"
        }
        if sha:
            payload["sha"] = sha
            
        put_resp = requests.put(url, json=payload, headers=headers, timeout=10)
        return put_resp.status_code in [200, 201]
    except Exception as e:
        logger.error(f"Error updating github file: {e}")
        return False
        
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
    if update.effective_chat:
        register_user(update.effective_chat.id)
    await update.message.reply_text(
        "👋 *Привет!* Я YouTube-Downloader Bot\n\n"
        "Отправь мне ссылку или используй `@имя_бота ссылка`",
        parse_mode='Markdown'
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if ADMIN_ID and str(chat_id) != str(ADMIN_ID):
        return
        
    total_users, today_downloads = get_stats()
    text = (
        "👑 *Панель Администратора*\n\n"
        f"👥 Всего пользователей: *{total_users}*\n"
        f"📥 Скачиваний за сегодня: *{today_downloads}*\n\n"
        "📢 *Рассылка:*\n`/broadcast Текст сообщения`\n\n"
        "🍪 *Обновление cookies.txt:*\nОтправь файл `cookies.txt` и в подписи укажи `/update_cookie`."
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if ADMIN_ID and str(chat_id) != str(ADMIN_ID):
        return
        
    text = update.message.text.replace("/broadcast", "").strip()
    if not text:
        await update.message.reply_text("❌ Введи текст для рассылки после команды `/broadcast`.", parse_mode='Markdown')
        return
        
    users = get_all_users()
    sent = 0
    await update.message.reply_text("⏳ Начинаю рассылку...")
    for u in users:
        try:
            await context.bot.send_message(u, text)
            sent += 1
        except Exception:
            pass
            
    await update.message.reply_text(f"✅ Рассылка завершена. Доставлено: {sent} из {len(users)}.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if ADMIN_ID and str(chat_id) != str(ADMIN_ID):
        return
        
    doc = update.message.document
    caption = update.message.caption or ""
    
    if doc.file_name == 'cookies.txt' and '/update_cookie' in caption:
        status_msg = await update.message.reply_text("⏳ Обновляю `cookies.txt` в репозитории...", parse_mode='Markdown')
        try:
            file = await context.bot.get_file(doc.file_id)
            import io
            out = io.BytesIO()
            await file.download_to_memory(out)
            content = out.getvalue()
            
            success = update_github_file(content, 'cookies.txt')
            if success:
                await status_msg.edit_text("✅ Файл `cookies.txt` успешно обновлён в репозитории!")
            else:
                await status_msg.edit_text("❌ Ошибка при обновлении файла на GitHub (проверьте права токена).")
        except Exception as e:
            logger.error(f"Document error: {e}")
            await status_msg.edit_text("❌ Произошла внутренняя ошибка.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка обычных сообщений.
    В группе бот реагирует ТОЛЬКО если есть упоминание @бота.
    """
    text = update.message.text
    if not text:
        return

    chat_type = update.message.chat.type
    bot_username = f"@{context.bot.username}"

    # Если это группа, игнорируем всё, где нет юзернейма бота
    if chat_type in ['group', 'supergroup']:
        if bot_username not in text:
            return 

    url = None
    for word in text.split():
        if any(domain in word for domain in SUPPORTED_DOMAINS) and "http" in word:
            url = word
            break

    if not url:
        return

    url_hash = get_url_hash(url)
    url_cache[url_hash] = url
    context.application.bot_data[url_hash] = url

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

# Я ВЕРНУЛ ЭТУ ФУНКЦИЮ: Она отвечает за всплывающее окошко при вводе @бота
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
                input_message_content=InputTextMessageContent(message_text='Напиши @bot https://youtube.com/...')
            )]
            await update.inline_query.answer(results, cache_time=0)
            return

        url_hash = get_url_hash(url)
        url_cache[url_hash] = url
        context.application.bot_data[url_hash] = url

        label   = '720p'
        short_url = url[:50] + '...' if len(url) > 50 else url

        # Кнопка под inline сообщением
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
    """Единый обработчик для всех кнопок."""
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

        # Если кнопка была под ОБЫЧНЫМ сообщением (в ЛС или группе)
        if query.message:
            chat_id = query.message.chat_id
            message_id = query.message.message_id
            await query.edit_message_text(f"⏳ Скачиваю {quality}...\nФайл придёт сюда через 1-2 минуты.")
            
        # Если кнопка была под INLINE (всплывающим) сообщением
        else:
            chat_id = query.from_user.id # Telegram скрывает ID группы, отправляем в ЛС
            message_id = None
            await query.edit_message_text(f"⏳ Запускаю скачивание {quality}...\nВнимание: Файл придёт тебе в ЛС.")

        # Запускаем GitHub Action
        success = trigger_github_action(url, quality, chat_id, message_id)
        if success:
            record_download(chat_id, url, quality)
        else:
            await context.bot.send_message(chat_id, "❌ Не удалось запустить задачу.")

    except Exception as e:
        logger.error(f"download_callback error: {e}", exc_info=True)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    
    # ВОТ ОНО - возвращаем обработчик всплывающего окна
    app.add_handler(InlineQueryHandler(inline_query))
    
    app.add_handler(CallbackQueryHandler(download_callback, pattern='^d_'))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()