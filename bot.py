import os
from telegram import Update, ReplyKeyboardMarkup
# Import specific errors for handling
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- Settings ---
# 1. Get the Bot Token from Railway variables
TELEGRAM_TOKEN = os.environ.get('BOT_TOKEN')
# 2. Get your secret Log Group/Channel ID from Railway variables
LOG_CHANNEL_ID = os.environ.get('LOG_CHANNEL_ID') 

# --- State Management (In-Memory) ---
active_chats = {}
waiting_queue = []

# --- (NEW) Define Keyboard Buttons ---
keyboard_buttons = [
    ["Search 🔎", "Next ↪️"],
    ["Stop ⏹️"]
]
main_keyboard = ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)

# --- Bot Command Handlers (English) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(English) Sends a welcome message for /start and shows the keyboard"""
    user_id = update.message.from_user.id
    
    # Check if user is already busy
    if user_id in active_chats:
        await update.message.reply_text(
            "You are currently in a chat. Use the buttons below.",
            reply_markup=main_keyboard # Show keyboard
        )
    elif user_id in waiting_queue:
        await update.message.reply_text(
            "You are currently in the waiting queue. Use the buttons below.",
            reply_markup=main_keyboard # Show keyboard
        )
    else:
        # Send the welcome message (MODIFIED to include protection note)
        await update.message.reply_text(
            "Welcome to the Anonymous Chat Bot! 🕵️‍♂️\n\n"
            "Press 'Search' to find a partner.\n\n"
            "🔒 **Note:** All media in this chat is **protected**. Saving, forwarding, and screenshots are disabled.",
            reply_markup=main_keyboard # Show keyboard
        )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(English) Searches for a chat partner (handles /search and 'Search 🔎' button)"""
    user_id = update.message.from_user.id
    
    if user_id in active_chats:
        await update.message.reply_text("You are already in a chat! Press 'Stop' or 'Next' first.")
        return
    if user_id in waiting_queue:
        await update.message.reply_text("You are already searching. Please wait...")
        return

    # Check the waiting queue
    if not waiting_queue:
        waiting_queue.append(user_id)
        await update.message.reply_text("🔎 Searching for a partner... Please wait.")
        print(f"User {user_id} added to queue. Queue: {waiting_queue}")
    else:
        # Partner found!
        partner_id = waiting_queue.pop(0) 
        active_chats[user_id] = partner_id
        active_chats[partner_id] = user_id
        
        print(f"Match found! {user_id} <-> {partner_id}.")
        
        # Notify both users (and send keyboard to partner)
        await context.bot.send_message(chat_id=user_id, text="✅ Partner found! The chat has started. (You are anonymous).")
        await context.bot.send_message(chat_id=partner_id, text="✅ Partner found! The chat has started. (You are anonymous).", reply_markup=main_keyboard)

async def end_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(English) Ends the current chat or cancels the search (handles /end and 'Stop ⏹️' button)"""
    user_id = update.message.from_user.id
    
    if user_id in active_chats:
        # User is in an active chat
        partner_id = active_chats.pop(user_id) 
        if partner_id in active_chats: # Remove partner as well
            active_chats.pop(partner_id)
            
        print(f"Chat ended by {user_id}. Partner was {partner_id}.")
        
        # Notify both users (and send keyboard)
        await context.bot.send_message(chat_id=user_id, text="🔚 You have ended the chat.", reply_markup=main_keyboard)
        await context.bot.send_message(chat_id=partner_id, text="⚠️ Your partner has left the chat.", reply_markup=main_keyboard)
        
    elif user_id in waiting_queue:
        # User was waiting
        waiting_queue.remove(user_id)
        print(f"User {user_id} cancelled search.")
        await update.message.reply_text("Search cancelled.", reply_markup=main_keyboard)
    else:
        # User is not in a chat or queue
        await update.message.reply_text("You are not currently in a chat or searching.", reply_markup=main_keyboard)


async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(NEW) Ends the current chat and immediately starts searching for a new one"""
    user_id = update.message.from_user.id
    
    # --- 1. End Chat Logic (copied from end_command) ---
    if user_id in active_chats:
        partner_id = active_chats.pop(user_id) 
        if partner_id in active_chats:
            active_chats.pop(partner_id)
        
        print(f"Chat ended by {user_id} (via /next). Partner was {partner_id}.")
        
        # Notify both users
        await context.bot.send_message(chat_id=user_id, text="🔚 Chat ended. Searching for new partner...")
        await context.bot.send_message(chat_id=partner_id, text="⚠️ Your partner has left the chat.", reply_markup=main_keyboard) # Give partner keyboard
        
    elif user_id in waiting_queue:
        await update.message.reply_text("You are already searching. Please wait...")
        return # Stop here, they are already searching
    else:
        # User was not in chat, just wants to search
        await update.message.reply_text("🔎 Searching for a partner... Please wait.")

    # --- 2. Search Logic (copied from search_command) ---
    if not waiting_queue:
        waiting_queue.append(user_id)
        print(f"User {user_id} added to queue (via /next). Queue: {waiting_queue}")
    else:
        # Partner found!
        partner_id = waiting_queue.pop(0) 
        active_chats[user_id] = partner_id
        active_chats[partner_id] = user_id
        
        print(f"Match found! {user_id} <-> {partner_id}.")
        
        await context.bot.send_message(chat_id=user_id, text="✅ Partner found! The chat has started.")
        await context.bot.send_message(chat_id=partner_id, text="✅ Partner found! The chat has started.", reply_markup=main_keyboard)


async def relay_and_log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Relays the message to the partner (WITH PROTECTION)
    + logs a copy to the admin channel (WITHOUT PROTECTION)
    """
    
    sender_id = update.message.from_user.id
    message = update.message

    if sender_id not in active_chats:
        await message.reply_text("You are not in a chat. Press 'Search' to start.", reply_markup=main_keyboard)
        return

    partner_id = active_chats[sender_id]

    # --- Step 1: Log the message to the Admin Group (Unprotected) ---
    if LOG_CHANNEL_ID:
        try:
            log_caption = (
                f"Message from: `{sender_id}`\n"
                f"To partner: `{partner_id}`\n\n"
                f"{message.caption or ''}" 
            )
            
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
                 await context.bot.send_message(
                    chat_id=LOG_CHANNEL_ID, text=f"[Text Message]\n{log_caption}\n\nContent: {message.text}", parse_mode='Markdown'
                )
            
            print(f"Logged message from {sender_id} to {partner_id}")
        except Exception as e:
            print(f"CRITICAL: Failed to log message to {LOG_CHANNEL_ID}: {e}")
    
    # --- Step 2: Relay the message to the partner (Anonymously & WITH PROTECTION) ---
    try:
        #
        #
        # # --- (!!!) ADMIN TOGGLE: CONTENT PROTECTION (!!!) ---
        #
        #   To DISABLE protection (allow save/forward):
        #   Change all 'protect_content=True' to 'protect_content=False' below.
        #
        #   To ENABLE protection (block save/forward):
        #   Change all 'protect_content=False' to 'protect_content=True' below.
        #
        #
        
        if message.photo:
            await context.bot.send_photo(
                chat_id=partner_id, photo=message.photo[-1].file_id, caption=message.caption, protect_content=True
            )
        elif message.document:
            await context.bot.send_document(
                chat_id=partner_id, document=message.document.file_id, caption=message.caption, protect_content=True
            )
        elif message.video:
            await context.bot.send_video(
                chat_id=partner_id, video=message.video.file_id, caption=message.caption, protect_content=True
            )
        elif message.sticker:
            await context.bot.send_sticker(
                chat_id=partner_id, sticker=message.sticker.file_id, protect_content=True
            )
        elif message.voice:
            await context.bot.send_voice(
                chat_id=partner_id, voice=message.voice.file_id, caption=message.caption, protect_content=True
            )
        elif message.text:
            await context.bot.send_message(
                chat_id=partner_id, text=message.text, protect_content=True
            )

    except (Forbidden, BadRequest) as e:
        if "bot was blocked" in str(e) or "user is deactivated" in str(e) or "chat not found" in str(e):
            print(f"Partner {partner_id} is unreachable. Ending chat.")
            # We must pass the *original* update object to end_command
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
        
    print("Bot (1-on-1 + Logging + Protection + Buttons) is running...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # --- (MODIFIED) Add handlers for commands AND buttons ---
    
    # 1. Command Handlers (for typing manually)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("end", end_command))
    application.add_handler(CommandHandler("next", next_command)) # (New)

    # 2. Text Handlers (for the buttons)
    # We link the button text to the same command functions
    application.add_handler(MessageHandler(filters.Text(["Search 🔎"]), search_command))
    application.add_handler(MessageHandler(filters.Text(["Stop ⏹️"]), end_command))
    application.add_handler(MessageHandler(filters.Text(["Next ↪️"]), next_command)) # (New)
    
    # 3. Main Message Handler (for media, text, etc.)
    # (MODIFIED) We must now *exclude* the button text from being relayed!
    button_texts = ["Search 🔎", "Stop ⏹️", "Next ↪️"]
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.Text(button_texts), 
        relay_and_log_message
    ))

    # Run the bot
    application.run_polling()

if __name__ == "__main__":
    main()
