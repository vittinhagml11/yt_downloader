import os
import threading
import requests
import hashlib
import logging
import sqlite3
import base64
import urllib.parse
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
            username TEXT,
            joined_date TEXT,
            request_count INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            url TEXT,
            quality TEXT,
            timestamp TEXT,
            platform TEXT
        )
    ''')
    # Migrate old schema if needed
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN username TEXT')
    except sqlite3.OperationalError: pass
    try:
        cursor.execute('ALTER TABLE users ADD COLUMN request_count INTEGER DEFAULT 0')
    except sqlite3.OperationalError: pass
    try:
        cursor.execute('ALTER TABLE downloads ADD COLUMN platform TEXT')
    except sqlite3.OperationalError: pass
    conn.commit()
    conn.close()

def download_db_from_github():
    if not GH_REPO or not GH_TOKEN:
        logger.warning("GH_REPO or GH_TOKEN not set, skipping DB download.")
        return
    try:
        url = f"https://api.github.com/repos/{GH_REPO}/contents/{DB_NAME}"
        headers = {"Authorization": f"Bearer {GH_TOKEN}"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            content = base64.b64decode(resp.json()['content'])
            with open(DB_NAME, 'wb') as f:
                f.write(content)
            logger.info("Database downloaded from GitHub.")
        else:
            logger.warning(f"Database not found on GitHub (status {resp.status_code}).")
    except Exception as e:
        logger.error(f"Error downloading DB from GitHub: {e}")

def push_db_to_github():
    if not GH_REPO or not GH_TOKEN: return
    try:
        with open(DB_NAME, 'rb') as f:
            content = f.read()
        update_github_file(content, DB_NAME)
        logger.info("Database pushed to GitHub.")
    except Exception as e:
        logger.error(f"Error pushing DB to GitHub: {e}")

download_db_from_github()
init_db()

def extract_platform(url: str) -> str:
    try:
        domain = urllib.parse.urlparse(url).netloc.lower()
        if 'youtube.com' in domain or 'youtu.be' in domain:
            return 'YouTube'
        elif 'instagram.com' in domain:
            return 'Instagram'
        elif 'tiktok.com' in domain:
            return 'TikTok'
        elif 'twitter.com' in domain or 'x.com' in domain:
            return 'Twitter/X'
        elif 'rutube.ru' in domain:
            return 'Rutube'
        elif 'twitch.tv' in domain:
            return 'Twitch'
        else:
            return 'Other'
    except:
        return 'Unknown'

def register_user(chat_id: int, username: str | None = None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (chat_id, username, joined_date) 
        VALUES (?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET username = excluded.username
    ''', (chat_id, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    push_db_to_github()

def record_download(chat_id: int, url: str, quality: str):
    platform = extract_platform(url)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO downloads (chat_id, url, quality, timestamp, platform) VALUES (?, ?, ?, ?, ?)',
                   (chat_id, url, quality, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), platform))
    cursor.execute('UPDATE users SET request_count = request_count + 1 WHERE chat_id = ?', (chat_id,))
    conn.commit()
    conn.close()
    push_db_to_github()

def get_stats():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute('SELECT COUNT(*) FROM downloads WHERE timestamp LIKE ?', (f'{today}%',))
    downloads_today = cursor.fetchone()[0]
    
    cursor.execute('SELECT platform, COUNT(*) FROM downloads WHERE platform IS NOT NULL AND timestamp LIKE ? GROUP BY platform', (f'{today}%',))
    platform_stats_today = dict(cursor.fetchall())
    
    cursor.execute('SELECT platform, COUNT(*) FROM downloads WHERE platform IS NOT NULL GROUP BY platform')
    platform_stats_total = dict(cursor.fetchall())
    
    # Get Top-20 users
    cursor.execute('SELECT username, chat_id, request_count FROM users ORDER BY request_count DESC LIMIT 20')
    top_users = cursor.fetchall()
    
    conn.close()
    return total_users, downloads_today, platform_stats_today, platform_stats_total, top_users

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
    "rutube.ru", "twitch.tv", "clips.twitch.tv",
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
                "admin_id":   str(ADMIN_ID) if ADMIN_ID else ""
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
        user = update.effective_user
        username = f"@{user.username}" if user and user.username else (user.full_name if user else None)
        register_user(update.effective_chat.id, username)
    await update.message.reply_text(
        "👋 <b>Привет!</b> Я твой медиа-бот!\n\n"
        "🎬 Я умею скачивать видео из <b>YouTube, Instagram, TikTok, Twitter/X, Rutube (полные видео), Twitch (клипы)</b>.\n\n"
        "👇 <b>Как использовать:</b>\n"
        "• Просто отправь мне ссылку в этот чат.\n"
        "• Или добавь меня в беседу и напиши <code>@dwnlo_bot ссылка</code> в чате, чтобы выбрать качество!\n\n"
        "✨ <i>Жду твою ссылку...</i>",
        parse_mode='HTML'
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if ADMIN_ID and str(chat_id) != str(ADMIN_ID):
        return
        
    total_users, today_downloads, p_today, p_total, top_users = get_stats()
    
    stats_today_str = "\n".join([f"  • {p}: {c}" for p, c in p_today.items()]) if p_today else "  Нет данных"
    stats_total_str = "\n".join([f"  • {p}: {c}" for p, c in p_total.items()]) if p_total else "  Нет данных"
    
    top_users_str = "\n".join([f"  • {u or 'Hidden' if u else c}: {r}" for u, c, r in top_users if r > 0]) or "  Нет запросов"
    
    text = (
        "👑 <b>Панель Администратора</b>\n\n"
        f"👥 <b>Всего пользователей (включая группы):</b> {total_users}\n"
        f"📥 <b>Скачиваний за сегодня:</b> {today_downloads}\n\n"
        f"📊 <b>По платформам (сегодня):</b>\n{stats_today_str}\n\n"
        f"📈 <b>По платформам (всего):</b>\n{stats_total_str}\n\n"
        f"🏆 <b>Топ-20 пользователей:</b>\n{top_users_str}\n\n"
        "📢 <b>Рассылка:</b>\n<code>/broadcast Текст сообщения</code>\n\n"
        "🍪 <b>Обновление cookies:</b>\nОтправь файл <code>cookies.txt</code> или <code>insta_cookies.txt</code> и в подписи укажи <code>/update_cookie</code>."
    )
    await update.message.reply_text(text, parse_mode='HTML')

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
    await update.message.reply_text("⏳ <b>Начинаю рассылку...</b>", parse_mode='HTML')
    for u in users:
        try:
            await context.bot.send_message(u, text, parse_mode='HTML')
            sent += 1
        except Exception:
            pass
            
    await update.message.reply_text(f"✅ <b>Рассылка завершена.</b>\nДоставлено: <b>{sent}</b> из <b>{len(users)}</b>.", parse_mode='HTML')

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if ADMIN_ID and str(chat_id) != str(ADMIN_ID):
        return
        
    doc = update.message.document
    caption = update.message.caption or ""
    
    if doc.file_name in ['cookies.txt', 'insta_cookies.txt'] and '/update_cookie' in caption:
        status_msg = await update.message.reply_text(f"⏳ Обновляю <code>{doc.file_name}</code> в репозитории...", parse_mode='HTML')
        try:
            file = await context.bot.get_file(doc.file_id)
            import io
            out = io.BytesIO()
            await file.download_to_memory(out)
            content = out.getvalue()
            
            success = update_github_file(content, doc.file_name)
            if success:
                await status_msg.edit_text(f"✅ Файл <code>{doc.file_name}</code> успешно обновлён в репозитории!", parse_mode='HTML')
            else:
                await status_msg.edit_text("❌ Ошибка при обновлении файла на GitHub (проверьте права токена).", parse_mode='HTML')
        except Exception as e:
            logger.error(f"Document error: {e}")
            await status_msg.edit_text("❌ Произошла внутренняя ошибка.", parse_mode='HTML')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка обычных сообщений.
    В группе бот реагирует ТОЛЬКО если есть упоминание @бота.
    """
    if update.effective_chat:
        register_user(update.effective_chat.id)

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
        
    url_str = str(url)

    if 'twitch.tv' in url_str and '/clip/' not in url_str and 'clips.twitch.tv' not in url_str:
        await update.message.reply_text("⚠️ <b>Ошибка:</b> Я могу скачивать только клипы с Twitch, а не полные трансляции.", parse_mode='HTML', reply_to_message_id=update.message.message_id)
        return

    url_hash = get_url_hash(url_str)
    url_cache[url_hash] = url_str
    context.application.bot_data[url_hash] = url_str

    keyboard = [
        [InlineKeyboardButton("📺 720p",  callback_data=f'd_{url_hash}_720'),
         InlineKeyboardButton("📱 480p",  callback_data=f'd_{url_hash}_480')]
    ]

    await update.message.reply_text(
        "🎛 <b>Выбери качество:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML',
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
                description='YouTube, TikTok, Insta, Twitter, Rutube, Twitch Клипы',
                input_message_content=InputTextMessageContent(message_text='Отправь правильную ссылку на видео (@bot ссылка)')
            )]
            await update.inline_query.answer(results, cache_time=0)
            return
            
        url_str = str(url)
        if 'twitch.tv' in url_str and '/clip/' not in url_str and 'clips.twitch.tv' not in url_str:
            results = [InlineQueryResultArticle(
                id='help',
                title='🔍 Введите ссылку на клип Twitch',
                description='Принимаются только клипы (не трансляции)',
                input_message_content=InputTextMessageContent(message_text='Отправь правильную ссылку на клип.')
            )]
            await update.inline_query.answer(results, cache_time=0)
            return
            
        url_hash = get_url_hash(url_str)
        url_cache[url_hash] = url_str
        context.application.bot_data[url_hash] = url_str

        label   = '720p'
        short_url = url_str[:50] + '...' if len(url_str) > 50 else url_str

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
            
        url_str = str(url)

        # Если кнопка была под ОБЫЧНЫМ сообщением (в ЛС или группе)
        if query.message:
            chat_id = query.message.chat_id
            message_id = query.message.message_id
            await query.edit_message_text(f"⏳ <b>Скачиваю {quality}...</b>\n<i>Файл придёт сюда через 1-2 минуты.</i>", parse_mode='HTML')
            
        # Если кнопка была под INLINE (всплывающим) сообщением
        else:
            chat_id = query.from_user.id # Telegram скрывает ID группы, отправляем в ЛС
            message_id = None
            await query.edit_message_text(f"⏳ <b>Запускаю скачивание {quality}...</b>\n<i>Внимание: Файл придёт тебе в ЛС.</i>", parse_mode='HTML')

        # Запускаем GitHub Action
        success = trigger_github_action(url_str, quality, str(chat_id), message_id)
        if success:
            record_download(chat_id, url_str, quality)
        else:
            await context.bot.send_message(chat_id, "❌ <b>Не удалось запустить задачу.</b>", parse_mode='HTML')

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