import os
import asyncio
import asyncpg
import logging
import re
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- Settings & Environment Variables ---
try:
    TELEGRAM_TOKEN = os.environ['BOT_TOKEN']
    DATABASE_URL = os.environ['DATABASE_URL']
    ADMIN_ID = int(os.environ['ADMIN_ID']) 
    CHANNEL_ID = os.environ['CHANNEL_ID']
    CHANNEL_INVITE_LINK = os.environ['CHANNEL_INVITE_LINK']
    LOG_CHANNEL_ID = os.environ.get('LOG_CHANNEL_ID') 
except KeyError as e:
    logging.critical(f"CRITICAL: Missing environment variable {e}. Bot cannot start.")
    exit(f"Missing environment variable: {e}")

db_pool = None

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Define Keyboard Buttons ---
keyboard_buttons = [
    ["Search 🔎", "Next 🎲"], 
    ["Block User 🚫", "Stop ⏹️"] 
]
main_keyboard = ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)
button_texts = ["Search 🔎", "Next 🎲", "Block User 🚫", "Stop ⏹️"]

# --- NEW: URL and Username Pattern Definition ---
URL_PATTERN = re.compile(
    r'(https?://|www\.|t\.me/|t\.co/|telegram\.me/|telegram\.dog/)'
    r'[\w\.-]+(\.[\w\.-]+)*([\w\-\._~:/\?#\[\]@!$&\'()*+,;=])*',
    re.IGNORECASE
)

# --- Define Confirmation Keyboard ---
async def get_confirmation_keyboard(reported_id):
    keyboard = [
        [InlineKeyboardButton("✅ Block and Report Now", callback_data=f"confirm_block_{reported_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_block")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- (1) Force Subscribe Helper Functions ---

async def is_user_subscribed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except BadRequest as e:
        if "user not found" in e.message:
            logger.warning(f"User {user_id} not found in channel {CHANNEL_ID}, likely not joined.")
        else:
            logger.error(f"Error checking channel membership for {user_id} in {CHANNEL_ID}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error checking membership for {user_id} in {CHANNEL_ID}: {e}")
        return False

async def send_join_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ترسل رسالة الاشتراك الإجباري."""
    keyboard = [
        [
            InlineKeyboardButton("🔗 Join Channel", url=CHANNEL_INVITE_LINK),
            InlineKeyboardButton("✅ I have joined", callback_data="check_join")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        sender = update.message.reply_text
    elif update.callback_query:
        sender = update.callback_query.message.reply_text
    else:
        return
        
    await sender(
        r"👋 **Welcome to Random Partner 🎲\!**" + "\n\n"
        r"To use this bot, you are required to join our official channel\." + "\n\n"
        r"Please join the channel using the button below, then press '✅ I have joined'\.",
        reply_markup=reply_markup,
        protect_content=True,
        parse_mode=constants.ParseMode.MARKDOWN_V2
    )

async def handle_join_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعالج ضغطة زر '✅ I have joined' للتحقق من الاشتراك."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer("Checking your membership...")
    
    if await is_user_subscribed(user_id, context):
        await query.edit_message_text(
            r"🎉 **Thank you for joining\!**" + "\n\n"
            r"You can now use the bot\. Press /start or use the buttons below\.",
            reply_markup=None, 
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        await query.message.reply_text(
            "Use the buttons below to control the chat:", 
            reply_markup=main_keyboard,
            protect_content=True
        )
    else:
        await query.answer("Please subscribe to the channel first.", show_alert=True)

# --- (2) Database Helper Functions ---

async def init_database():
    """يتصل بقاعدة البيانات وينشئ الجداول."""
    global db_pool
    if not DATABASE_URL:
        logger.critical("CRITICAL: DATABASE_URL not found. Bot cannot start.")
        return False
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as connection:
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS active_chats (
                    user_id BIGINT PRIMARY KEY,
                    partner_id BIGINT NOT NULL UNIQUE
                );
            ''')
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS waiting_queue (
                    user_id BIGINT PRIMARY KEY,
                    timestamp TIMESTAMPTZ DEFAULT (NOW() AT TIME ZONE 'UTC')
                );
            ''')
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS all_users (
                    user_id BIGINT PRIMARY KEY
                );
            ''')
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS user_blocks (
                    blocker_id BIGINT,
                    blocked_id BIGINT,
                    PRIMARY KEY (blocker_id, blocked_id)
                );
            ''')
            # --- (جدول الحظر الكلي الجديد) ---
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS global_bans (
                    user_id BIGINT PRIMARY KEY
                );
            ''')
        logger.info("Database connected and tables verified.")
        return True
    except Exception as e:
        logger.critical(f"CRITICAL: Failed to connect to database: {e}")
        return False

async def add_user_to_all_list(user_id):
    """يضيف المستخدم إلى قائمة البث إذا لم يكن موجوداً."""
    if not db_pool: return
    try:
        async with db_pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO all_users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                user_id
            )
    except Exception as e:
        logger.error(f"Failed to add user {user_id} to broadcast list: {e}")

async def get_all_users():
    """يجلب جميع المستخدمين المسجلين في قائمة البث."""
    if not db_pool: return []
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT ARRAY_AGG(user_id) FROM all_users") or []

async def get_partner_from_db(user_id):
    if not db_pool: return None
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT partner_id FROM active_chats WHERE user_id = $1", user_id)

async def is_user_waiting_db(user_id):
    if not db_pool: return False
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT 1 FROM waiting_queue WHERE user_id = $1", user_id) is not None

async def end_chat_in_db(user_id):
    if not db_pool: return None
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            partner_id = await connection.fetchval("DELETE FROM active_chats WHERE user_id = $1 RETURNING partner_id", user_id)
            if partner_id:
                await connection.execute("DELETE FROM active_chats WHERE user_id = $1", partner_id)
            return partner_id

async def remove_from_wait_queue_db(user_id):
    if not db_pool: return
    async with db_pool.acquire() as connection:
        await connection.execute("DELETE FROM waiting_queue WHERE user_id = $1", user_id)

async def add_user_block(blocker_id, blocked_id):
    """يسجل حظراً متبادلاً."""
    if not db_pool: return
    async with db_pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO user_blocks (blocker_id, blocked_id) VALUES ($1, $2) ON CONFLICT (blocker_id, blocked_id) DO NOTHING",
            blocker_id, blocked_id
        )

async def is_user_globally_banned(user_id):
    """(جديد) يتحقق مما إذا كان المستخدم محظوراً حظراً كلياً من البوت."""
    if not db_pool: return False
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT 1 FROM global_bans WHERE user_id = $1", user_id) is not None


# --- (3) Bot Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await add_user_to_all_list(user_id)
    
    # 🛑 (جديد) التحقق من الحظر الكلي أولاً
    if await is_user_globally_banned(user_id):
        await update.message.reply_text("Your access to this bot has been permanently suspended.", protect_content=True)
        return
    
    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context)
        return
    if await get_partner_from_db(user_id):
        await update.message.reply_text("You are currently in a chat. Use the buttons below.", reply_markup=main_keyboard, protect_content=True) 
    elif await is_user_waiting_db(user_id):
        await update.message.reply_text("You are currently in the waiting queue. Use the buttons below.", reply_markup=main_keyboard, protect_content=True) 
    else:
        await update.message.reply_text(
            "Welcome to the Anonymous Chat Bot! 🕵️‍♂️\n\n"
            "Press 'Search' to find a partner.\n\n"
            "🔒 **Note:** All media in this chat is **protected**.",
            reply_markup=main_keyboard,
            protect_content=True
        )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if await is_user_globally_banned(user_id): return # 🛑 حظر كلي
    
    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context)
        return
    if await get_partner_from_db(user_id):
        await update.message.reply_text("You are already in a chat! Press 'Stop' or 'Next' first.", protect_content=True) 
        return
    if await is_user_waiting_db(user_id):
        await update.message.reply_text("You are already searching. Please wait...", protect_content=True) 
        return
    
    # --- (تعديل استعلام البحث لاستبعاد المستخدمين المحظورين) ---
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            partner_id = await connection.fetchval(
                """
                DELETE FROM waiting_queue
                WHERE user_id = (
                    SELECT user_id 
                    FROM waiting_queue 
                    WHERE user_id != $1 
                      AND user_id NOT IN (SELECT blocked_id FROM user_blocks WHERE blocker_id = $1)
                      AND $1 NOT IN (SELECT blocked_id FROM user_blocks WHERE blocker_id = user_id)
                      AND user_id NOT IN (SELECT user_id FROM global_bans) -- 🛑 استبعاد المحظورين كليا
                    ORDER BY timestamp ASC LIMIT 1
                )
                WHERE user_id IN (SELECT user_id FROM waiting_queue WHERE user_id != $1)
                RETURNING user_id
                """, user_id
            )
            # ----------------------------------------------------
            
            if partner_id:
                await connection.execute("INSERT INTO active_chats (user_id, partner_id) VALUES ($1, $2), ($2, $1)", user_id, partner_id)
                logger.info(f"Match found! {user_id} <-> {partner_id}.")
                await context.bot.send_message(chat_id=user_id, text="✅ Partner found! The chat has started. (You are anonymous).", reply_markup=main_keyboard, protect_content=True) 
                await context.bot.send_message(chat_id=partner_id, text="✅ Partner found! The chat has started. (You are anonymous).", reply_markup=main_keyboard, protect_content=True) 
            else:
                await connection.execute("INSERT INTO waiting_queue (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                await update.message.reply_text("🔎 Searching for a partner... Please wait.", protect_content=True) 
                logger.info(f"User {user_id} added to DB queue.")

async def end_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if await is_user_globally_banned(user_id): return # 🛑 حظر كلي
    
    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context)
        return
    partner_id = await end_chat_in_db(user_id)
    if partner_id:
        logger.info(f"Chat ended by {user_id}. Partner was {partner_id}.")
        await context.bot.send_message(chat_id=user_id, text="🔚 You have ended the chat.", reply_markup=main_keyboard, protect_content=True) 
        try:
            await context.bot.send_message(chat_id=partner_id, text="⚠️ Your partner has left the chat.", reply_markup=main_keyboard, protect_content=True) 
        except (Forbidden, BadRequest) as e:
             logger.warning(f"Could not notify partner {partner_id} about chat end: {e}")
    elif await is_user_waiting_db(user_id):
        await remove_from_wait_queue_db(user_id)
        logger.info(f"User {user_id} cancelled search.")
        await update.message.reply_text("Search cancelled.", reply_markup=main_keyboard, protect_content=True) 
    else:
        await update.message.reply_text("You are not currently in a chat or searching.", reply_markup=main_keyboard, protect_content=True) 

async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if await is_user_globally_banned(user_id): return # 🛑 حظر كلي
    
    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context)
        return
    partner_id = await end_chat_in_db(user_id)
    if partner_id:
        logger.info(f"Chat ended by {user_id} (via /next). Partner was {partner_id}.")
        await context.bot.send_message(chat_id=user_id, text="🔚 Chat ended. Searching for new partner...", protect_content=True) 
        try:
            await context.bot.send_message(chat_id=partner_id, text="⚠️ Your partner has left the chat.", reply_markup=main_keyboard, protect_content=True) 
        except (Forbidden, BadRequest) as e:
            logger.warning(f"Could not notify partner {partner_id} about chat end: {e}")
    elif await is_user_waiting_db(user_id):
        await update.message.reply_text("You are already searching. Please wait...", protect_content=True) 
        return
    else:
        await update.message.reply_text("🔎 Searching for a partner... Please wait.", protect_content=True) 

    # --- (تعديل استعلام البحث لاستبعاد المستخدمين المحظورين) ---
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            partner_id_new = await connection.fetchval(
                """
                DELETE FROM waiting_queue
                WHERE user_id = (
                    SELECT user_id 
                    FROM waiting_queue 
                    WHERE user_id != $1 
                      AND user_id NOT IN (SELECT blocked_id FROM user_blocks WHERE blocker_id = $1)
                      AND $1 NOT IN (SELECT blocked_id FROM user_blocks WHERE blocker_id = user_id)
                      AND user_id NOT IN (SELECT user_id FROM global_bans) -- 🛑 استبعاد المحظورين كليا
                    ORDER BY timestamp ASC LIMIT 1
                )
                RETURNING user_id
                """, user_id
            )
            # ----------------------------------------------------
            
            if partner_id_new:
                await connection.execute("INSERT INTO active_chats (user_id, partner_id) VALUES ($1, $2), ($2, $1)", user_id, partner_id_new)
                logger.info(f"Match found! {user_id} <-> {partner_id_new}.")
                await context.bot.send_message(chat_id=user_id, text="✅ Partner found! The chat has started.", reply_markup=main_keyboard, protect_content=True) 
                await context.bot.send_message(chat_id=partner_id_new, text="✅ Partner found! The chat has started.", reply_markup=main_keyboard, protect_content=True) 
            else:
                await connection.execute("INSERT INTO waiting_queue (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                logger.info(f"User {user_id} added/remains in DB queue (via /next).")

# --- (NEW) Admin Global Ban Command ---

async def banuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("🚫 Access denied. Admin command only.", protect_content=True)
        return

    if len(context.args) != 1:
        await update.message.reply_text("Usage: /banuser <User_ID_to_Ban>", protect_content=True)
        return

    try:
        banned_id = int(context.args[0])
        
        # 1. تسجيل الحظر في جدول global_bans
        async with db_pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO global_bans (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                banned_id
            )
        
        # 2. إخراج المستخدم من أي محادثة حالية أو قائمة انتظار
        await end_chat_in_db(banned_id)
        await remove_from_wait_queue_db(banned_id)
        
        # 3. إخطار الأدمن
        await update.message.reply_text(f"✅ User ID {banned_id} has been permanently blocked from using the chat features.", protect_content=True)
        
    except ValueError:
        await update.message.reply_text("❌ Invalid ID format. Must be a number.", protect_content=True)
    except Exception as e:
        logger.error(f"Error banning user: {e}")
        await update.message.reply_text(f"❌ An error occurred during the ban process: {e}", protect_content=True)

# --- (NEW) Admin Direct Message Command ---

async def sendid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("🚫 Access denied. This command is for the administrator only.", protect_content=True)
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /sendid <Recipient_User_ID> <Your Message>", protect_content=True)
        return

    try:
        target_id = int(context.args[0])
        message_to_send = " ".join(context.args[1:])
        
        # 1. محاولة الإرسال (حماية مضافة)
        await context.bot.send_message(
            chat_id=target_id,
            text=f"📢 **Admin Message:**\n\n{message_to_send}",
            parse_mode='Markdown',
            protect_content=True
        )
        
        # 2. تأكيد الإرسال للأدمن
        await update.message.reply_text(f"✅ Message sent successfully to User ID: {target_id}", protect_content=True)
        
    except BadRequest as e:
        await update.message.reply_text(f"❌ Failed to send: User ID {target_id} is unreachable or invalid. Error: {e.message}", protect_content=True)
    except Exception as e:
        await update.message.reply_text(f"❌ An unexpected error occurred: {e}", protect_content=True)

# --- (NEW) Block User Handlers (Confirmation) ---

async def block_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context)
        return

    reported_id = await get_partner_from_db(user_id)
    
    if not reported_id:
        if await is_user_waiting_db(user_id):
            await update.message.reply_text("You cannot block anyone while searching. Use 'Stop ⏹️' first.", protect_content=True) 
        else:
            await update.message.reply_text("You are not currently in a chat to block anyone.", reply_markup=main_keyboard, protect_content=True) 
        return
    
    # 1. إرسال رسالة التأكيد مع الأزرار المضمنة (كما هو مطلوب)
    confirmation_markup = await get_confirmation_keyboard(reported_id)
    
    await update.message.reply_text(
        "🚫 **CONFIRM BLOCK AND REPORT**\n\n"
        "Are you sure you want to block the current partner and send a report to the Telegram Team?\n\n"
        "*(This action will end the chat immediately.)*",
        reply_markup=confirmation_markup,
        parse_mode=constants.ParseMode.MARKDOWN,
        protect_content=True
    )

async def handle_block_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    await query.answer()
    
    if data == "cancel_block":
        await query.edit_message_text("🚫 Block/Report operation cancelled. You can continue chatting.")
        return

    if data.startswith("confirm_block_"):
        # 1. استخراج ID المُبلغ عنه
        reported_id = int(data.split('_')[2])
        
        # 2. تسجيل الحظر (يسجل الحظر في قاعدة البيانات)
        await add_user_block(user_id, reported_id) 
        
        # 3. إرسال التقرير المفصل إلى الأدمن (LOG_CHANNEL_ID)
        if LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    chat_id=LOG_CHANNEL_ID,
                    text=f"🚨 **NEW REPORT RECEIVED (Chat Blocked)** 🚨\n\n"
                         f"**Reported User ID (Blocked):** `{reported_id}`\n"
                         f"**Reporter User ID (Blocker):** `{user_id}`\n\n"
                         f"**Action:** User {user_id} permanently blocked {reported_id}.",
                    parse_mode=constants.ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Failed to process report for {reported_id}: {e}")

        # 4. إنهاء المحادثة لكلا الطرفين
        partner_id = await end_chat_in_db(user_id)
        
        # 5. إرسال رسالة التأكيد للمستخدم (المُبلِّغ)
complete
