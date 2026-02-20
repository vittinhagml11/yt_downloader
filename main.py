import os
import glob
import threading
import requests
from flask import Flask
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "I'm alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

TOKEN = os.getenv('BOT_TOKEN')

# Публичные Invidious-инстансы — они делают запросы к YouTube от своего имени,
# обходя блокировку датацентровых IP Render.
# Если один не работает — пробуем следующий.
INVIDIOUS_INSTANCES = [
    "https://invidious.nerdvpn.de",
    "https://inv.nadeko.net",
    "https://invidious.privacyredirect.com",
    "https://yt.cdaut.de",
    "https://invidious.fdn.fr",
]

def get_video_id(url: str) -> str:
    """Извлекает video_id из любой YouTube-ссылки."""
    import re
    patterns = [
        r'(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    raise ValueError(f"Не удалось извлечь video_id из: {url}")


def get_stream_url_via_invidious(video_id: str, quality: str) -> tuple[str, str, str]:
    """
    Запрашивает у Invidious прямые ссылки на стримы.
    Возвращает (stream_url, title, ext).
    """
    last_error = None
    for instance in INVIDIOUS_INSTANCES:
        try:
            api_url = f"{instance}/api/v1/videos/{video_id}"
            resp = requests.get(api_url, timeout=15)
            if resp.status_code != 200:
                continue

            data = resp.json()
            title = data.get('title', video_id)
            adaptive_formats = data.get('adaptiveFormats', [])
            format_streams = data.get('formatStreams', [])  # готовые объединённые потоки

            if quality == 'mp3':
                # Берём лучший аудио-поток
                audio_formats = [f for f in adaptive_formats if f.get('type', '').startswith('audio')]
                if not audio_formats:
                    continue
                # Сортируем по битрейту
                audio_formats.sort(key=lambda x: x.get('bitrate', 0), reverse=True)
                best = audio_formats[0]
                return best['url'], title, 'mp3'
            else:
                target_height = int(quality)
                # Сначала ищем в готовых объединённых потоках (видео+аудио вместе)
                video_streams = [
                    f for f in format_streams
                    if f.get('type', '').startswith('video')
                ]
                # Сортируем по качеству (убывание) и берём подходящее
                video_streams.sort(key=lambda x: int(x.get('resolution', '0p').replace('p', '') or 0), reverse=True)
                for s in video_streams:
                    res = int(s.get('resolution', '0p').replace('p', '') or 0)
                    if res <= target_height:
                        ext = 'mp4' if 'mp4' in s.get('type', '') else 'webm'
                        return s['url'], title, ext

                # Если не нашли — берём просто лучший из format_streams
                if video_streams:
                    s = video_streams[-1]  # наименьшее качество как запасной
                    ext = 'mp4' if 'mp4' in s.get('type', '') else 'webm'
                    return s['url'], title, ext

        except Exception as e:
            last_error = e
            continue

    raise Exception(f"Все Invidious-инстансы недоступны. Последняя ошибка: {last_error}")


def download_via_invidious(url: str, quality: str) -> str:
    """Скачивает файл через Invidious и возвращает путь."""
    os.makedirs('downloads', exist_ok=True)
    video_id = get_video_id(url)
    stream_url, title, ext = get_stream_url_via_invidious(video_id, quality)

    # Для mp3 — скачиваем аудио и конвертируем через ffmpeg
    if quality == 'mp3':
        tmp_path = f'downloads/{video_id}.tmp_audio'
        out_path = f'downloads/{video_id}.mp3'
    else:
        out_path = f'downloads/{video_id}.{ext}'

    # Скачиваем через requests с прогрессом
    headers = {'User-Agent': 'Mozilla/5.0'}
    with requests.get(stream_url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        path = tmp_path if quality == 'mp3' else out_path
        with open(path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                f.write(chunk)

    # Конвертируем в mp3 если нужно
    if quality == 'mp3':
        import subprocess
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', tmp_path, '-vn', '-ar', '44100', '-ac', '2', '-b:a', '192k', out_path],
            capture_output=True, text=True
        )
        os.remove(tmp_path)
        if result.returncode != 0:
            raise Exception(f"ffmpeg ошибка: {result.stderr[-300:]}")

    return out_path


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришли ссылку на YouTube-видео и выбери формат."
    )

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

    await query.edit_message_text("⏳ Скачиваю через Invidious, подожди...")

    try:
        filename = download_via_invidious(url, quality)
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