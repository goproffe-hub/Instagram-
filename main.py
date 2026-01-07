import os
import asyncio
import uuid
import shutil
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

    # DEBUG: Check if file exists and print size
    if os.path.exists("cookies.txt"):
        print(f"✅ Found cookies.txt ({os.path.getsize('cookies.txt')} bytes)")
        cookie_file = "cookies.txt"
    else:
        print("❌ cookies.txt NOT FOUND in root directory")
        cookie_file = None

    common_opts = {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'outtmpl': f'{temp_dir}/%(title)s.%(ext)s',
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    }

    if cookie_file:
        common_opts['cookiefile'] = cookie_file

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
            # RETURN THE ACTUAL ERROR MESSAGE
            return None, str(e), temp_dir

# --- LOGIC ---
async def process_request(update: Update, context: ContextTypes.DEFAULT_TYPE, url, req_type):
    unique_id = f"{update.effective_user.id}_{uuid.uuid4().hex[:6]}"
    
    try:
        status_msg = await update.message.reply_text(f"⏳ **Queued...**", parse_mode='Markdown')
    except:
        return

    async with download_semaphore:
        try:
            await status_msg.edit_text(f"⚡ **Processing...**")
            action = ChatAction.UPLOAD_AUDIO if req_type == 'audio' else ChatAction.UPLOAD_VIDEO
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=action)

            loop = asyncio.get_event_loop()
            # Capture the specific error message
            file_path, result_info, temp_dir = await loop.run_in_executor(None, download_media, url, unique_id, req_type)

            if file_path and os.path.exists(file_path):
                if os.path.getsize(file_path) > 49.9 * 1024 * 1024:
                    await status_msg.edit_text("❌ **File > 50MB** (Telegram Limit).")
                else:
                    await status_msg.edit_text(f"⬆️ **Uploading...**")
                    with open(file_path, 'rb') as f:
                        if req_type == 'audio':
                            await update.message.reply_audio(audio=f, title=result_info)
                        else:
                            await update.message.reply_video(video=f, caption=result_info)
                    await status_msg.delete()
            else:
                # SHOW THE REAL ERROR TO THE USER
                error_text = result_info if result_info else "Unknown Error"
                # Shorten error if too long
                if len(error_text) > 200: error_text = error_text[:200] + "..."
                await status_msg.edit_text(f"❌ **Error:**\n`{error_text}`", parse_mode='Markdown')

        except Exception as e:
            await status_msg.edit_text(f"❌ **System Error:** {e}")
        finally:
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if cookies exist on startup
    cookie_status = "✅ Cookies Found" if os.path.exists("cookies.txt") else "❌ Cookies Missing"
    await update.message.reply_text(f"🤖 **Bot Ready.**\nStatus: {cookie_status}\nSend links!")

async def song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        await process_request(update, context, context.args[0], 'audio')
    else:
        await update.message.reply_text("Usage: `/song <link>`")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "http" in text and "://" in text:
        await process_request(update, context, text, 'video')

# --- MAIN ---
if __name__ == '__main__':
    Thread(target=run_flask).start()
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN is missing.")
        exit(1)
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("song", song))
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    app_bot.run_polling()
