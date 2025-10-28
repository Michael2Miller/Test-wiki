import os
import asyncio
import asyncpg # (مكتبة قاعدة البيانات الجديدة)
from telegram import Update, ReplyKeyboardMarkup
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- Settings ---
TELEGRAM_TOKEN = os.environ.get('BOT_TOKEN')
LOG_CHANNEL_ID = os.environ.get('LOG_CHANNEL_ID')
DATABASE_URL = os.environ.get('DATABASE_URL') # (جلب رابط قاعدة البيانات)

# (هام) هذا المتغير سيحمل الاتصال الدائم بقاعدة البيانات
db_pool = None

# --- (NEW) Define Keyboard Buttons ---
keyboard_buttons = [
    ["Search 🔎", "Next ↪️"],
    ["Stop ⏹️"]
]
main_keyboard = ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)


# --- (NEW) Database Helper Functions ---

async def init_database():
    """يتصل بقاعدة البيانات وينشئ الجداول إذا لم تكن موجودة."""
    global db_pool
    if not DATABASE_URL:
        print("CRITICAL: DATABASE_URL not found. Bot cannot start.")
        return False
        
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        # إنشاء الجداول الدائمة
        async with db_pool.acquire() as connection:
            # 1. جدول لتخزين المحادثات النشطة
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS active_chats (
                    user_id BIGINT PRIMARY KEY,
                    partner_id BIGINT NOT NULL UNIQUE
                );
            ''')
            # 2. جدول لتخزين قائمة الانتظار
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS waiting_queue (
                    user_id BIGINT PRIMARY KEY,
                    timestamp TIMESTAMPTZ DEFAULT (NOW() AT TIME ZONE 'UTC')
                );
            ''')
        print("Database connected and tables verified.")
        return True
    except Exception as e:
        print(f"CRITICAL: Failed to connect to database: {e}")
        return False

async def get_partner_from_db(user_id):
    """(جديد) يتحقق مما إذا كان المستخدم في محادثة نشطة ويعيد الشريك."""
    if not db_pool: return None
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT partner_id FROM active_chats WHERE user_id = $1", user_id)

async def is_user_waiting_db(user_id):
    """(جديد) يتحقق مما إذا كان المستخدم في قائمة الانتظار."""
    if not db_pool: return False
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT 1 FROM waiting_queue WHERE user_id = $1", user_id) is not None

async def end_chat_in_db(user_id):
    """(جديد) ينهي المحادثة في قاعدة البيانات ويعيد الشريك."""
    if not db_pool: return None
    async with db_pool.acquire() as connection:
        async with connection.transaction(): # (نستخدم معاملة لضمان الحذف)
            # 1. احذف المستخدم وأعد شريكه
            partner_id = await connection.fetchval("DELETE FROM active_chats WHERE user_id = $1 RETURNING partner_id", user_id)
            if partner_id:
                # 2. احذف الشريك أيضاً
                await connection.execute("DELETE FROM active_chats WHERE user_id = $1", partner_id)
            return partner_id

async def remove_from_wait_queue_db(user_id):
    """(جديد) يزيل المستخدم من قائمة الانتظار."""
    if not db_pool: return
    async with db_pool.acquire() as connection:
        await connection.execute("DELETE FROM waiting_queue WHERE user_id = $1", user_id)


# --- Bot Command Handlers (Modified for DB) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if await get_partner_from_db(user_id):
        await update.message.reply_text("You are currently in a chat. Use the buttons below.", reply_markup=main_keyboard)
    elif await is_user_waiting_db(user_id):
        await update.message.reply_text("You are currently in the waiting queue. Use the buttons below.", reply_markup=main_keyboard)
    else:
        await update.message.reply_text(
            "Welcome to the Anonymous Chat Bot! 🕵️‍♂️\n\n"
            "Press 'Search' to find a partner.\n\n"
            "🔒 **Note:** All media in this chat is **protected**.",
            reply_markup=main_keyboard
        )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if await get_partner_from_db(user_id):
        await update.message.reply_text("You are already in a chat! Press 'Stop' or 'Next' first.")
        return
    if await is_user_waiting_db(user_id):
        await update.message.reply_text("You are already searching. Please wait...")
        return

    # --- (MODIFIED) DB Logic ---
    async with db_pool.acquire() as connection:
        async with connection.transaction(): # (نستخدم معاملة لضمان المطابقة)
            # 1. ابحث عن أي شخص في الانتظار
            partner_id = await connection.fetchval("DELETE FROM waiting_queue ORDER BY timestamp ASC RETURNING user_id")
            
            if partner_id:
                # 2. وجدنا شريكاً! قم بتسجيل المحادثة
                await connection.execute(
                    "INSERT INTO active_chats (user_id, partner_id) VALUES ($1, $2), ($2, $1)",
                    user_id, partner_id
                )
                print(f"Match found! {user_id} <-> {partner_id}.")
                
                # 3. إبلاغ الطرفين
                await context.bot.send_message(chat_id=user_id, text="✅ Partner found! The chat has started. (You are anonymous).")
                await context.bot.send_message(chat_id=partner_id, text="✅ Partner found! The chat has started. (You are anonymous).", reply_markup=main_keyboard)
            else:
                # 4. لا أحد ينتظر، أضف هذا المستخدم إلى الانتظار
                await connection.execute("INSERT INTO waiting_queue (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                await update.message.reply_text("🔎 Searching for a partner... Please wait.")
                print(f"User {user_id} added to DB queue.")

async def end_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    # --- (MODIFIED) DB Logic ---
    partner_id = await end_chat_in_db(user_id)
    
    if partner_id:
        # كان في محادثة
        print(f"Chat ended by {user_id}. Partner was {partner_id}.")
        await context.bot.send_message(chat_id=user_id, text="🔚 You have ended the chat.", reply_markup=main_keyboard)
        await context.bot.send_message(chat_id=partner_id, text="⚠️ Your partner has left the chat.", reply_markup=main_keyboard)
    elif await is_user_waiting_db(user_id):
        # كان في الانتظار
        await remove_from_wait_queue_db(user_id)
        print(f"User {user_id} cancelled search.")
        await update.message.reply_text("Search cancelled.", reply_markup=main_keyboard)
    else:
        await update.message.reply_text("You are not currently in a chat or searching.", reply_markup=main_keyboard)

async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    # --- 1. (MODIFIED) End Chat Logic ---
    partner_id = await end_chat_in_db(user_id)
    
    if partner_id:
        print(f"Chat ended by {user_id} (via /next). Partner was {partner_id}.")
        await context.bot.send_message(chat_id=user_id, text="🔚 Chat ended. Searching for new partner...")
        await context.bot.send_message(chat_id=partner_id, text="⚠️ Your partner has left the chat.", reply_markup=main_keyboard)
    elif await is_user_waiting_db(user_id):
        await update.message.reply_text("You are already searching. Please wait...")
        return
    else:
        await update.message.reply_text("🔎 Searching for a partner... Please wait.")

    # --- 2. (MODIFIED) Search Logic ---
    # (نفس منطق 'search_command' بالضبط)
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            partner_id_new = await connection.fetchval("DELETE FROM waiting_queue ORDER BY timestamp ASC RETURNING user_id")
            if partner_id_new:
                await connection.execute(
                    "INSERT INTO active_chats (user_id, partner_id) VALUES ($1, $2), ($2, $1)",
                    user_id, partner_id_new
                )
                print(f"Match found! {user_id} <-> {partner_id_new}.")
                await context.bot.send_message(chat_id=user_id, text="✅ Partner found! The chat has started.")
                await context.bot.send_message(chat_id=partner_id_new, text="✅ Partner found! The chat has started.", reply_markup=main_keyboard)
            else:
                await connection.execute("INSERT INTO waiting_queue (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                print(f"User {user_id} added to DB queue (via /next).")


async def relay_and_log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.message.from_user.id
    message = update.message

    # --- (MODIFIED) DB Check ---
    partner_id = await get_partner_from_db(sender_id)
    
    if not partner_id:
        await message.reply_text("You are not in a chat. Press 'Search' to start.", reply_markup=main_keyboard)
        return

    # --- Step 1: Log the message (Unprotected) ---
    if LOG_CHANNEL_ID:
        try:
            log_caption = (f"Message from: `{sender_id}`\nTo partner: `{partner_id}`\n\n{message.caption or ''}")
            
            if message.photo:
                await context.bot.send_photo(chat_id=LOG_CHANNEL_ID, photo=message.photo[-1].file_id, caption=log_caption, parse_mode='Markdown')
            elif message.document:
                await context.bot.send_document(chat_id=LOG_CHANNEL_ID, document=message.document.file_id, caption=log_caption, parse_mode='Markdown')
            elif message.video:
                await context.bot.send_video(chat_id=LOG_CHANNEL_ID, video=message.video.file_id, caption=log_caption, parse_mode='Markdown')
            elif message.voice:
                 await context.bot.send_voice(chat_id=LOG_CHANNEL_ID, voice=message.voice.file_id, caption=log_caption, parse_mode='Markdown')
            elif message.text:
                 await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=f"[Text Message]\n{log_caption}\n\nContent: {message.text}", parse_mode='Markdown')
            
            print(f"Logged message from {sender_id} to {partner_id}")
        except Exception as e:
            print(f"CRITICAL: Failed to log message to {LOG_CHANNEL_ID}: {e}")
    
    # --- Step 2: Relay the message (Protected) ---
    try:
        #
        # # --- (!!!) ADMIN TOGGLE: CONTENT PROTECTION (!!!) ---
        #   To DISABLE protection: Change 'protect_content=True' to 'protect_content=False'
        #   To ENABLE protection: Change 'protect_content=False' to 'protect_content=True'
        #
        
        if message.photo:
            await context.bot.send_photo(chat_id=partner_id, photo=message.photo[-1].file_id, caption=message.caption, protect_content=True)
        elif message.document:
            await context.bot.send_document(chat_id=partner_id, document=message.document.file_id, caption=message.caption, protect_content=True)
        elif message.video:
            await context.bot.send_video(chat_id=partner_id, video=message.video.file_id, caption=message.caption, protect_content=True)
        elif message.sticker:
            await context.bot.send_sticker(chat_id=partner_id, sticker=message.sticker.file_id, protect_content=True)
        elif message.voice:
            await context.bot.send_voice(chat_id=partner_id, voice=message.voice.file_id, caption=message.caption, protect_content=True)
        elif message.text:
            await context.bot.send_message(chat_id=partner_id, text=message.text, protect_content=True)

    except (Forbidden, BadRequest) as e:
        if "bot was blocked" in str(e) or "user is deactivated" in str(e) or "chat not found" in str(e):
            print(f"Partner {partner_id} is unreachable. Ending chat.")
            # (هام) يجب أن نزيلهم من قاعدة البيانات
            await end_chat_in_db(sender_id) # سينهي المحادثة لكلا الطرفين
            await message.reply_text("Your partner seems to have blocked the bot. The chat has ended.", reply_markup=main_keyboard)
        else:
            print(f"Failed to send to partner {partner_id}: {e}")
            await message.reply_text("Sorry, your message failed to send. (Your partner may have blocked the bot).")
    except Exception as e:
        print(f"An unexpected error occurred sending to {partner_id}: {e}")

# --- (MODIFIED) Main Run Function (Async) ---

async def main_async():
    """الدالة الرئيسية غير المتزامنة لتشغيل البوت"""
    if not TELEGRAM_TOKEN:
        print("CRITICAL: BOT_TOKEN not found.")
        return
    
    # (جديد) الاتصال بقاعدة البيانات أولاً
    if not await init_database():
        print("CRITICAL: Bot shutting down due to database connection failure.")
        return
    
    if not LOG_CHANNEL_ID:
        print("WARNING: LOG_CHANNEL_ID not found. Bot will work, but logging/archiving is DISABLED.")
        
    print("Bot (1-on-1 + DB + Logging + Protection + Buttons) is running...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # إضافة المعالجات (تبقى كما هي)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("end", end_command))
    application.add_handler(CommandHandler("next", next_command))

    application.add_handler(MessageHandler(filters.Text(["Search 🔎"]), search_command))
    application.add_handler(MessageHandler(filters.Text(["Stop ⏹️"]), end_command))
    application.add_handler(MessageHandler(filters.Text(["Next ↪️"]), next_command))
    
    button_texts = ["Search 🔎", "Stop ⏹️", "Next ↪️"]
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.Text(button_texts), 
        relay_and_log_message
    ))

    # تشغيل البوت
    await application.run_polling()

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("Bot stopped by admin.")

if __name__ == "__main__":
    main()
