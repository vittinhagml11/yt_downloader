import os
import asyncio
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
    # Render дает порт в переменной окружения PORT
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
    
    status_msg = await query.edit_message_text("Скачиваю...")

    ydl_opts = {
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'cookiefile': 'cookies.txt',
        'merge_output_format': 'mp4',
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
        # Сначала пробуем скачать один готовый файл (single file), 
        # где видео и звук уже вместе — это надежнее всего для серверов.
        # Если такого нет, пробуем склеить лучшее видео + звук.
        ydl_opts.update({
            'format': f'best[height<={quality}][ext=mp4]/bestvideo[height<={quality}]+bestaudio/best',
        })
    try:
        if not os.path.exists('downloads'): os.makedirs('downloads')
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if quality == 'mp3': filename = os.path.splitext(filename)[0] + '.mp3'

        file_size = os.path.getsize(filename) / (1024 * 1024)
        if file_size > 50:
            await context.bot.send_message(query.message.chat_id, "Файл больше 50МБ!")
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
    print(f"Проверка FFmpeg: {shutil.which('ffmpeg')}") # Должно вывести путь, если он есть
    # Запускаем Flask в отдельном потоке
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Запускаем бота
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()