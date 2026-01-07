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

# --- CONCURRENCY CONTROL ---
# Strictly limit to 2 simultaneous downloads to survive Render's 512MB RAM limit
download_semaphore = asyncio.Semaphore(2)

# --- FLASK KEEPALIVE ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Render Bot is Alive."

def run_flask():
    # Run Flask without the reloader to save RAM
    app.run(host='0.0.0.0', port=PORT, use_reloader=False)

# --- ENGINE ---
def get_yt_dlp_opts(unique_id, download_type='video'):
    # Absolute path for temp directory to avoid path errors
    temp_dir = os.path.abspath(f"downloads/{unique_id}")
    os.makedirs(temp_dir, exist_ok=True)

    common_opts = {
        'quiet': True,              # No console spam
        'no_warnings': True,
        'noprogress': True,         # Save CPU cycles
        'outtmpl': f'{temp_dir}/%(title)s.%(ext)s',
        # Fake a real browser to bypass basic blocks
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    }

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
        # Smart Format Selection: Prefer <50MB, else best mp4
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
        except Exception:
            # We silently catch errors here to handle them in the async loop
            return None, None, temp_dir

# --- LOGIC ---
async def process_request(update: Update, context: ContextTypes.DEFAULT_TYPE, url, req_type):
    # Create unique ID based on UserID + UUID to prevent collisions in groups
    unique_id = f"{update.effective_user.id}_{uuid.uuid4().hex[:6]}"
    
    # Notify user (silently fails if message can't be sent)
    try:
        status_msg = await update.message.reply_text(f"⏳ **Queued...**", parse_mode='Markdown')
    except:
        return # If we can't reply, stop.

    async with download_semaphore:
        try:
            await status_msg.edit_text(f"⚡ **Processing...**")
            
            # Send action (Typing/Uploading)
            action = ChatAction.UPLOAD_AUDIO if req_type == 'audio' else ChatAction.UPLOAD_VIDEO
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=action)

            # Execute Download
            loop = asyncio.get_event_loop()
            file_path, title, temp_dir = await loop.run_in_executor(None, download_media, url, unique_id, req_type)

            if file_path and os.path.exists(file_path):
                # Final Size Check
                if os.path.getsize(file_path) > 49.9 * 1024 * 1024:
                    await status_msg.edit_text("❌ **File > 50MB** (Telegram Limit).")
                else:
                    await status_msg.edit_text(f"⬆️ **Uploading...**")
                    with open(file_path, 'rb') as f:
                        if req_type == 'audio':
                            await update.message.reply_audio(audio=f, title=title)
                        else:
                            await update.message.reply_video(video=f, caption=title)
                    
                    # Success: Delete status message
                    await status_msg.delete()
            else:
                await status_msg.edit_text("❌ **Failed.** (Private/Geo-locked/Too Big)")

        except Exception as e:
            # Catch network errors or Telegram API timeouts
            try:
                await status_msg.edit_text("❌ **Error.** Try again.")
            except:
                pass
        finally:
            # AGGRESSIVE CLEANUP: Ensure disk space is freed on Render
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 **Ready.** Send links or `/song <link>`.")

async def song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        await process_request(update, context, context.args[0], 'audio')
    else:
        await update.message.reply_text("Usage: `/song <link>`")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    # Simple check: Does it look like a URL?
    if "http" in text and "://" in text:
        await process_request(update, context, text, 'video')

# --- MAIN ---
if __name__ == '__main__':
    # 1. Start Flask (Keep-Alive)
    Thread(target=run_flask).start()

    # 2. Start Bot
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN is missing.")
        exit(1)
        
    app_bot = ApplicationBuilder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("song", song))
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    
    print("Bot is running...")
    app_bot.run_polling()

