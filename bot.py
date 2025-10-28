import os
from telegram import Update
# Import specific errors for handling
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- Settings ---
# 1. Get the Bot Token from Railway variables
TELEGRAM_TOKEN = os.environ.get('BOT_TOKEN')
# 2. Get your secret Log Group/Channel ID from Railway variables
LOG_CHANNEL_ID = os.environ.get('LOG_CHANNEL_ID') 

# --- State Management (In-Memory) ---
# 1. Dictionary for active chats: {user_A_id: user_B_id, user_B_id: user_A_id}
active_chats = {}
# 2. List for users waiting for a partner
waiting_queue = []

# --- Bot Command Handlers (English) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(English) Sends a welcome message for /start"""
    user_id = update.message.from_user.id
    
    # Check if user is already busy
    if user_id in active_chats:
        await update.message.reply_text("You are currently in a chat. Send /end to stop it.")
    elif user_id in waiting_queue:
        await update.message.reply_text("You are currently in the waiting queue. Send /end to cancel.")
    else:
        # Send the welcome message
        await update.message.reply_text(
            "Welcome to the Anonymous Chat Bot! 🕵️‍♂️\n\n"
            "Press /search to find a partner and start an anonymous chat.\n"
            "Press /end to stop the chat or cancel searching at any time.\n\n"
            "You can exchange (text, photos, videos, and files)."
        )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(English) Searches for a chat partner"""
    user_id = update.message.from_user.id
    
    # Check if user is already busy
    if user_id in active_chats:
        await update.message.reply_text("You are already in a chat! Send /end first to stop it.")
        return
    if user_id in waiting_queue:
        await update.message.reply_text("You are already searching. Please wait...")
        return

    # Check the waiting queue
    if not waiting_queue:
        # No one is waiting, add this user to the queue
        waiting_queue.append(user_id)
        await update.message.reply_text("🔎 Searching for a partner... Please wait.")
        print(f"User {user_id} added to queue. Queue: {waiting_queue}")
    else:
        # Partner found!
        partner_id = waiting_queue.pop(0) # Get the first user from the queue
        
        # Link them together
        active_chats[user_id] = partner_id
        active_chats[partner_id] = user_id
        
        print(f"Match found! {user_id} <-> {partner_id}.")
        
        # Notify both users
        await context.bot.send_message(chat_id=user_id, text="✅ Partner found! The chat has started. (You are anonymous).")
        await context.bot.send_message(chat_id=partner_id, text="✅ Partner found! The chat has started. (You are anonymous).")

async def end_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(English) Ends the current chat or cancels the search"""
    user_id = update.message.from_user.id
    
    if user_id in active_chats:
        # User is in an active chat
        partner_id = active_chats.pop(user_id) 
        if partner_id in active_chats: # Remove partner as well
            active_chats.pop(partner_id)
            
        print(f"Chat ended by {user_id}. Partner was {partner_id}.")
        
        # Notify both users
        await context.bot.send_message(chat_id=user_id, text="🔚 You have ended the chat.")
        await context.bot.send_message(chat_id=partner_id, text="⚠️ Your partner has left the chat.")
        
    elif user_id in waiting_queue:
        # User was waiting
        waiting_queue.remove(user_id)
        print(f"User {user_id} cancelled search.")
        await update.message.reply_text("Search cancelled.")
    else:
        # User is not in a chat or queue
        await update.message.reply_text("You are not currently in a chat or searching.")


# --- (!!!) The Core Function: Relay and Log (!!!) ---

async def relay_and_log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Relays the message to the partner + logs a copy to the admin channel
    """
    
    sender_id = update.message.from_user.id
    message = update.message

    # Check if the user is in an active chat
    if sender_id not in active_chats:
        await message.reply_text("You are not in a chat. Press /search to start.")
        return

    # Find the partner
    partner_id = active_chats[sender_id]

    # --- Step 1: Log the message to the Admin Group ---
    if LOG_CHANNEL_ID:
        try:
            # Create a log caption with sender and receiver info for the admin
            log_caption = (
                f"Message from: `{sender_id}`\n"
                f"To partner: `{partner_id}`\n\n"
                f"{message.caption or ''}" # Add original caption if it exists
            )
            
            # Forward the message to the log channel using its file_id
            if message.photo:
                await context.bot.send_photo(
                    chat_id=LOG_CHANNEL_ID, photo=message.photo[-1].file_id, caption=log_caption, parse_mode='Markdown'
                )
            elif message.document:
                await context.bot.send_document(
                    chat_id=LOG_CHANNEL_ID, document=message.document.file_id, caption=log_caption, parse_mode='Markdown'
                )
            elif message.video:
                await context.bot.send_video(
                    chat_id=LOG_CHANNEL_ID, video=message.video.file_id, caption=log_caption, parse_mode='Markdown'
                )
            elif message.voice:
                 await context.bot.send_voice(
                    chat_id=LOG_CHANNEL_ID, voice=message.voice.file_id, caption=log_caption, parse_mode='Markdown'
                )
            elif message.text:
                 # For text, we create a new message
                 await context.bot.send_message(
                    chat_id=LOG_CHANNEL_ID, text=f"[Text Message]\n{log_caption}\n\nContent: {message.text}", parse_mode='Markdown'
                )
            
            print(f"Logged message from {sender_id} to {partner_id}")

        except Exception as e:
            # Don't stop the relay, just print the logging error to Railway console
            print(f"CRITICAL: Failed to log message to {LOG_CHANNEL_ID}: {e}")
            # This can happen if bot is not admin in the log group
    
    # --- Step 2: Relay the message to the partner (Anonymously) ---
    try:
        # We send the media *without* the admin log_caption
        if message.photo:
            await context.bot.send_photo(chat_id=partner_id, photo=message.photo[-1].file_id, caption=message.caption)
        elif message.document:
            await context.bot.send_document(chat_id=partner_id, document=message.document.file_id, caption=message.caption)
        elif message.video:
            await context.bot.send_video(chat_id=partner_id, video=message.video.file_id, caption=message.caption)
        elif message.sticker:
            await context.bot.send_sticker(chat_id=partner_id, sticker=message.sticker.file_id)
        elif message.voice:
            await context.bot.send_voice(chat_id=partner_id, voice=message.voice.file_id, caption=message.caption)
        elif message.text:
            await context.bot.send_message(chat_id=partner_id, text=message.text)

    except (Forbidden, BadRequest) as e:
        # If the partner blocked the bot, end the chat for both
        if "bot was blocked" in str(e) or "user is deactivated" in str(e) or "chat not found" in str(e):
            print(f"Partner {partner_id} is unreachable. Ending chat.")
            # Call the end_command function to handle cleanup for both users
            await end_command(update, context) 
        else:
            print(f"Failed to send to partner {partner_id}: {e}")
            await message.reply_text("Sorry, your message failed to send. (Your partner may have blocked the bot).")
    except Exception as e:
        print(f"An unexpected error occurred sending to {partner_id}: {e}")

# --- Main Run Function ---

def main():
    """Main function to start the bot"""
    if not TELEGRAM_TOKEN:
        print("Cannot start bot: BOT_TOKEN not found in environment variables.")
        return
    
    if not LOG_CHANNEL_ID:
        print("WARNING: LOG_CHANNEL_ID not found. Bot will work, but logging/archiving is DISABLED.")
        # We don't stop the bot, just warn the admin
        
    print("Bot (1-on-1 Random + Logging) is running...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Add handlers for commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("end", end_command))
    
    # Add the main message handler (for all non-command messages)
    # This filters for private chats and messages that are not commands
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, relay_and_log_message))

    # Run the bot until manually stopped
    application.run_polling()

if __name__ == "__main__":
    main()
