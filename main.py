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

BASE_YDL_OPTS = {
    'outtmpl': 'downloads/%(id)s.%(ext)s',
    'cookiefile': 'cookies.txt',
    'nocheckcertificate': True,
    'source_address': '0.0.0.0',
    'socket_timeout': 30,
    'extractor_args': {
        'youtube': {
            'player_client': ['web_creator', 'web', 'android', 'ios'],
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

def try_download(url: str, quality: str) -> str:
    """
    Пробует несколько стратегий скачивания по очереди.
    Возвращает путь к файлу или бросает исключение.

    Ключевая идея: на серверных IP YouTube отдаёт только
    'best' (готовый объединённый поток). Форматы типа
    bestvideo+bestaudio там недоступны. Поэтому начинаем
    с самого простого и идём к сложному.
    """
    os.makedirs('downloads', exist_ok=True)

    if quality == 'mp3':
        strategies = [
            {'format': 'bestaudio/best',
             'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]},
            {'format': 'best',
             'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]},
        ]
    else:
        strategies = [
            # Стратегия 1: готовый файл нужного качества — без склейки ffmpeg
            {'format': f'best[height<={quality}]/best'},
            # Стратегия 2: явно mp4
            {'format': f'best[height<={quality}][ext=mp4]/best[ext=mp4]/best'},
            # Стратегия 3: склейка через ffmpeg (требует ffmpeg)
            {'format': f'bestvideo[height<={quality}]+bestaudio/bestvideo+bestaudio',
             'merge_output_format': 'mp4'},
        ]

    last_error = None
    for strategy in strategies:
        opts = {**BASE_YDL_OPTS, **strategy}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_id = info['id']

            if quality == 'mp3':
                candidates = glob.glob(f'downloads/{video_id}.mp3')
            else:
                candidates = glob.glob(f'downloads/{video_id}.*')
            if not candidates:
                candidates = glob.glob(f'downloads/{video_id}*')
            if candidates:
                return candidates[0]

        except yt_dlp.utils.DownloadError as e:
            last_error = e
            if 'Requested format is not available' in str(e):
                continue  # пробуем следующую стратегию
            raise  # другие ошибки сразу пробрасываем

    raise Exception(f"Все стратегии исчерпаны. Последняя ошибка: {last_error}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришли ссылку на YouTube!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("Это не ссылка на YouTube.")
        return
    context.user_data['current_url'] = url
    keyboard = [
        [InlineKeyboardButton("720p", callback_data='720'),
         InlineKeyboardButton("480p", callback_data='480')],
        [InlineKeyboardButton("MP3", callback_data='mp3')]
    ]
    await update.message.reply_text("Выбери качество:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url = context.user_data.get('current_url')
    quality = query.data

    await query.edit_message_text("⏳ Скачиваю, подожди...")

    try:
        filename = try_download(url, quality)
        file_size_mb = os.path.getsize(filename) / (1024 * 1024)

        if file_size_mb > 50:
            await context.bot.send_message(
                query.message.chat_id,
                f"❌ Файл слишком большой ({file_size_mb:.1f} МБ). Telegram принимает максимум 50 МБ."
            )
        else:
            with open(filename, 'rb') as f:
                if quality == 'mp3':
                    await context.bot.send_audio(chat_id=query.message.chat_id, audio=f)
                else:
                    await context.bot.send_video(chat_id=query.message.chat_id, video=f)

        os.remove(filename)

    except Exception as e:
        await context.bot.send_message(query.message.chat_id, f"❌ Ошибка: {e}")


if __name__ == '__main__':
    import shutil
    print(f"FFmpeg: {shutil.which('ffmpeg')}")
    threading.Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()