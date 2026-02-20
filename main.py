import os
import glob
import threading
from flask import Flask
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# --- ВЕБ-СЕРВЕР ДЛЯ ПОДДЕРЖАНИЯ ЖИЗНИ ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

# --- ЛОГИКА БОТА ---
TOKEN = os.getenv('BOT_TOKEN')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришли ссылку на YouTube!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("Это не ссылка на YouTube.")
        return
    context.user_data['current_url'] = url
    keyboard = [[InlineKeyboardButton("720p", callback_data='720'),
                 InlineKeyboardButton("480p", callback_data='480')],
                [InlineKeyboardButton("MP3", callback_data='mp3')]]
    await update.message.reply_text("Качество:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url = context.user_data.get('current_url')
    quality = query.data

    await query.edit_message_text("Скачиваю...")

    if not os.path.exists('downloads'):
        os.makedirs('downloads')

    # Базовые настройки — ключевые параметры для обхода блокировок на серверах
    ydl_opts = {
        'outtmpl': 'downloads/%(id)s.%(ext)s',  # используем id вместо title — нет проблем с кодировкой
        'cookiefile': 'cookies.txt',
        'nocheckcertificate': True,
        'source_address': '0.0.0.0',
        'extractor_args': {
            'youtube': {
                # web_creator обходит серверные блокировки лучше всего в 2024-2025
                'player_client': ['web_creator', 'web', 'android'],
            }
        },
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
        },
    }

    if quality == 'mp3':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            }],
        })
    else:
        ydl_opts.update({
            'format': f'best[height<={quality}][ext=mp4]/best[height<={quality}]/best[ext=mp4]/best',
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info['id']

        # Ищем файл по id — надёжнее чем prepare_filename после постпроцессинга
        if quality == 'mp3':
            pattern = f'downloads/{video_id}.mp3'
        else:
            pattern = f'downloads/{video_id}.*'

        files = glob.glob(pattern)
        if not files:
            # Запасной вариант — любой файл с этим id
            files = glob.glob(f'downloads/{video_id}*')

        if not files:
            await context.bot.send_message(query.message.chat_id, "Ошибка: файл не найден после скачивания.")
            return

        filename = files[0]
        file_size = os.path.getsize(filename) / (1024 * 1024)

        if file_size > 50:
            await context.bot.send_message(query.message.chat_id, f"Файл слишком большой ({file_size:.1f} МБ), Telegram не принимает файлы больше 50 МБ.")
        else:
            with open(filename, 'rb') as f:
                if quality == 'mp3':
                    await context.bot.send_audio(chat_id=query.message.chat_id, audio=f)
                else:
                    await context.bot.send_video(chat_id=query.message.chat_id, video=f)

        os.remove(filename)

    except Exception as e:
        await context.bot.send_message(query.message.chat_id, f"Ошибка: {e}")


if __name__ == '__main__':
    import shutil
    print(f"Проверка FFmpeg: {shutil.which('ffmpeg')}")
    threading.Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()