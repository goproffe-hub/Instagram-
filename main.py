import os
import asyncio
import uuid
import shutil
import logging
from flask import Flask
from threading import Thread
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import yt_dlp

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
PORT = int(os.environ.get('PORT', 5000))
download_semaphore = asyncio.Semaphore(2)

# --- LOGGING ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is Alive!"

def run_flask():
    app.run(host='0.0.0.0', port=PORT, use_reloader=False)

# --- ENGINE ---
def get_yt_dlp_opts(unique_id, download_type='video'):
    temp_dir = os.path.abspath(f"downloads/{unique_id}")
    os.makedirs(temp_dir, exist_ok=True)

    # COOKIE CHECK
    cookie_path = "cookies.txt"
    if os.path.exists(cookie_path):
        print(f"✅ Using cookies.txt ({os.path.getsize(cookie_path)} bytes)")
    else:
        print("⚠️ cookies.txt NOT FOUND - Download might fail")
        cookie_path = None

    common_opts = {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'outtmpl': f'{temp_dir}/%(title)s.%(ext)s',
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    }

    if cookie_path:
        common_opts['cookiefile'] = cookie_path

    if download_type == 'audio':
        common_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        common_opts.update({
            'format': 'best[filesize<50M]/best[ext=mp4]/best', 
            'max_filesize': 50 * 1024 * 1024, 
        })
    
    return common_opts, temp_dir

def download_media(url, unique_id, download_type='video'):
    opts, temp_dir = get_yt_dlp_opts(unique_id, download_type)
    
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if download_type == 'audio':
                filename = filename.rsplit('.', 1)[0] + '.mp3'
            return filename, info.get('title', 'Media'), temp_dir
        except Exception as e:
            return None, str(e), temp_dir

# --- LOGIC ---
async def process_request(update: Update, context: ContextTypes.DEFAULT_TYPE, url, req_type):
    unique_id = f"{update.effective_user.id}_{uuid.uuid4().hex[:6]}"
    
    try:
        status_msg = await update.message.reply_text("⏳ <b>Queued...</b>", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Reply error: {e}")
        return

    async with download_semaphore:
        try:
            await status_msg.edit_text("⚡ <b>Processing...</b>", parse_mode='HTML')
            
            action = ChatAction.UPLOAD_AUDIO if req_type == 'audio' else ChatAction.UPLOAD_VIDEO
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=action)

            loop = asyncio.get_event_loop()
            file_path, result, temp_dir = await loop.run_in_executor(None, download_media, url, unique_id, req_type)

            if file_path and os.path.exists(file_path):
                file_size = os.path.getsize(file_path) / (1024 * 1024)
                if file_size > 49.9:
                    await status_msg.edit_text("❌ <b>File too large</b> (>50MB Telegram Limit).", parse_mode='HTML')
                else:
                    await status_msg.edit_text("⬆️ <b>Uploading...</b>", parse_mode='HTML')
                    with open(file_path, 'rb') as f:
                        if req_type == 'audio':
                            await update.message.reply_audio(audio=f, title=result)
                        else:
                            await update.message.reply_video(video=f, caption=result)
                    await status_msg.delete()
            else:
                # SAFE ERROR REPORTING (HTML ESCAPED)
                error_text = result if result else "Unknown Error"
                error_text = error_text.replace("<", "&lt;").replace(">", "&gt;") # Escape HTML tags in error
                if len(error_text) > 500: error_text = error_text[:500] + "..."
                
                await status_msg.edit_text(
                    f"❌ <b>Download Failed</b>\n<pre>{error_text}</pre>", 
                    parse_mode='HTML'
                )

        except Exception as e:
            await status_msg.edit_text(f"❌ System Error: {str(e)}")
        finally:
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cookie_status = "✅ Cookies Found" if os.path.exists("cookies.txt") else "⚠️ Cookies Missing"
    await update.message.reply_text(
        f"🤖 <b>Bot Ready</b>\nStatus: {cookie_status}\n\nSend a link or use <code>/song link</code>", 
        parse_mode='HTML'
    )

async def song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        await process_request(update, context, context.args[0], 'audio')
    else:
        await update.message.reply_text("Usage: <code>/song link</code>", parse_mode='HTML')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "http" in text and "://" in text:
        await process_request(update, context, text, 'video')

if __name__ == '__main__':
    Thread(target=run_flask).start()
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN is missing.")
        exit(1)
    
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("song", song))
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    
    print("Bot is polling...")
    app_bot.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
