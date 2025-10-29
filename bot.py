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

# --- URL and Username Pattern Definition (مُفعلة الآن) ---
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
        await query.message.reply_text("Use the buttons below to control the chat:", reply_markup=main_keyboard)
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
            # --- (جدول الحظر الجديد) ---
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS user_blocks (
                    blocker_id BIGINT,
                    blocked_id BIGINT,
                    PRIMARY KEY (blocker_id, blocked_id)
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

# --- (3) Bot Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await add_user_to_all_list(user_id)
    
    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context)
        return
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
    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context)
        return
    if await get_partner_from_db(user_id):
        await update.message.reply_text("You are already in a chat! Press 'Stop' or 'Next' first.")
        return
    if await is_user_waiting_db(user_id):
        await update.message.reply_text("You are already searching. Please wait...")
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
                await context.bot.send_message(chat_id=user_id, text="✅ Partner found! The chat has started. (You are anonymous).", reply_markup=main_keyboard)
                await context.bot.send_message(chat_id=partner_id, text="✅ Partner found! The chat has started. (You are anonymous).", reply_markup=main_keyboard)
            else:
                await connection.execute("INSERT INTO waiting_queue (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                await update.message.reply_text("🔎 Searching for a partner... Please wait.")
                logger.info(f"User {user_id} added to DB queue.")

async def end_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context)
        return
    partner_id = await end_chat_in_db(user_id)
    if partner_id:
        logger.info(f"Chat ended by {user_id}. Partner was {partner_id}.")
        await context.bot.send_message(chat_id=user_id, text="🔚 You have ended the chat.", reply_markup=main_keyboard)
        try:
            await context.bot.send_message(chat_id=partner_id, text="⚠️ Your partner has left the chat.", reply_markup=main_keyboard)
        except (Forbidden, BadRequest) as e:
             logger.warning(f"Could not notify partner {partner_id} about chat end: {e}")
    elif await is_user_waiting_db(user_id):
        await remove_from_wait_queue_db(user_id)
        logger.info(f"User {user_id} cancelled search.")
        await update.message.reply_text("Search cancelled.", reply_markup=main_keyboard)
    else:
        await update.message.reply_text("You are not currently in a chat or searching.", reply_markup=main_keyboard)

async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context)
        return
    partner_id = await end_chat_in_db(user_id)
    if partner_id:
        logger.info(f"Chat ended by {user_id} (via /next). Partner was {partner_id}.")
        await context.bot.send_message(chat_id=user_id, text="🔚 Chat ended. Searching for new partner...")
        try:
            await context.bot.send_message(chat_id=partner_id, text="⚠️ Your partner has left the chat.", reply_markup=main_keyboard)
        except (Forbidden, BadRequest) as e:
            logger.warning(f"Could not notify partner {partner_id} about chat end: {e}")
    elif await is_user_waiting_db(user_id):
        await update.message.reply_text("You are already searching. Please wait...")
        return
    else:
        await update.message.reply_text("🔎 Searching for a partner... Please wait.")

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
                    ORDER BY timestamp ASC LIMIT 1
                )
                RETURNING user_id
                """, user_id
            )
            # ----------------------------------------------------
            
            if partner_id_new:
                await connection.execute("INSERT INTO active_chats (user_id, partner_id) VALUES ($1, $2), ($2, $1)", user_id, partner_id_new)
                logger.info(f"Match found! {user_id} <-> {partner_id_new}.")
                await context.bot.send_message(chat_id=user_id, text="✅ Partner found! The chat has started.", reply_markup=main_keyboard)
                await context.bot.send_message(chat_id=partner_id_new, text="✅ Partner found! The chat has started.", reply_markup=main_keyboard)
            else:
                await connection.execute("INSERT INTO waiting_queue (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                logger.info(f"User {user_id} added/remains in DB queue (via /next).")

# --- (NEW) Block User Handlers ---

async def block_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context)
        return

    reported_id = await get_partner_from_db(user_id)
    
    if not reported_id:
        if await is_user_waiting_db(user_id):
            await update.message.reply_text("You cannot block anyone while searching. Use 'Stop ⏹️' first.")
        else:
            await update.message.reply_text("You are not currently in a chat to block anyone.", reply_markup=main_keyboard)
        return
    
    # 1. إرسال رسالة التأكيد مع الأزرار المضمنة (كما هو مطلوب)
    confirmation_markup = await get_confirmation_keyboard(reported_id)
    
    await update.message.reply_text(
        "🚫 **CONFIRM BLOCK AND REPORT**\n\n"
        "Are you sure you want to block the current partner and send a report to the Telegram Team?\n\n"
        "*(This action will end the chat immediately.)*",
        reply_markup=confirmation_markup,
        parse_mode=constants.ParseMode.MARKDOWN
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
        await query.edit_message_text(
            "🛑 Thank you! The user has been blocked and the chat has ended.\n\n"
            "Your report has been successfully sent for review.\n\n"
            "Press Next 🎲 to find a new partner.",
            reply_markup=None # إزالة أزرار التأكيد
        )
        
        # 6. إخطار الشريك المُبلغ عنه (إذا أمكن)
        if reported_id:
            logger.info(f"Chat ended by {user_id} (via Block & Report). Partner was {reported_id}.")
            try:
                await context.bot.send_message(chat_id=reported_id, text="⚠️ Your partner has ended the chat.", reply_markup=main_keyboard)
            except (Forbidden, BadRequest) as e:
                logger.warning(f"Could not notify partner {reported_id} about chat end: {e}")


# --- (4) Broadcast Command ---

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message = update.message
    
    # 1. التحقق من أن المستخدم هو الأدمن
    if user_id != ADMIN_ID:
        await message.reply_text("🚫 Access denied. This command is for the administrator only.")
        return

    # 2. تحديد نوع الإرسال والتحقق من المحتوى
    is_media_broadcast = message.photo or message.video or message.document
    
    if not is_media_broadcast and not context.args:
        await message.reply_text(
            "Usage:\n"
            "1. For text: `/broadcast Your message here`\n"
            "2. For media: Send the photo/video/document with `/broadcast` and your message in the caption."
        )
        return

    # 3. جلب جميع المستخدمين من قاعدة البيانات
    all_users = await get_all_users()
    
    if not all_users:
        await message.reply_text("No users found in the database to broadcast to.")
        return

    success_count = 0
    fail_count = 0
    
    # 4. بدء عملية البث
    await message.reply_text(f"Starting broadcast to {len(all_users)} users...")
    
    for target_user_id in all_users:
        try:
            if is_media_broadcast:
                # استخدام copy_message لإرسال الوسائط والتعليق بكفاءة
                await context.bot.copy_message(
                    chat_id=target_user_id,
                    from_chat_id=user_id,
                    message_id=message.message_id
                )
            else:
                # إرسال نصي كالمعتاد
                message_to_send = " ".join(context.args)
                await context.bot.send_message(chat_id=target_user_id, text=message_to_send, parse_mode=constants.ParseMode.MARKDOWN) 
            
            success_count += 1
        except Forbidden:
            fail_count += 1
            logger.warning(f"User {target_user_id} blocked the bot. Skipping.")
        except Exception as e:
            fail_count += 1
            logger.error(f"Failed to send broadcast to {target_user_id}: {e}")
            
    # 5. إرسال تقرير البث للأدمن
    await message.reply_text(
        f"✅ **Broadcast complete!**\n"
        f"Sent successfully to: {success_count} users.\n"
        f"Failed (Bot blocked/Error): {fail_count} users."
    )


# --- (5) Relay Message Handler (مع تفعيل فلاتر الروابط واليوزرات) ---

async def relay_and_log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.message.from_user.id
    message = update.message
    
    await add_user_to_all_list(sender_id) 
    
    if not await is_user_subscribed(sender_id, context):
        await send_join_channel_message(update, context)
        return
    
    partner_id = await get_partner_from_db(sender_id)
    
    if not partner_id:
        await message.reply_text("You are not in a chat. Press 'Search' to start.", reply_markup=main_keyboard)
        return

    # --- Step 1: Log the message (الأرشفة أولاً - Log-Before-Filter) ---
    if LOG_CHANNEL_ID:
        try:
            log_caption_md = f"Msg from: `{sender_id}`\nTo partner: `{partner_id}`\n\n{message.caption or ''}"
            log_text = f"[Text Msg]\nMsg from: `{sender_id}`\nTo partner: `{partner_id}`\n\nContent: {message.text or ''}"
            if message.photo: await context.bot.send_photo(chat_id=LOG_CHANNEL_ID, photo=message.photo[-1].file_id, caption=log_caption_md, parse_mode='Markdown')
            elif message.document: await context.bot.send_document(chat_id=LOG_CHANNEL_ID, document=message.document.file_id, caption=log_caption_md, parse_mode='Markdown')
            elif message.video: await context.bot.send_video(chat_id=LOG_CHANNEL_ID, video=message.video.file_id, caption=log_caption_md, parse_mode='Markdown')
            elif message.voice: await context.bot.send_voice(chat_id=LOG_CHANNEL_ID, voice=message.voice.file_id, caption=log_caption_md, parse_mode='Markdown')
            elif message.text: await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"CRITICAL: Failed to log message to {LOG_CHANNEL_ID}: {e}")
            
    # --- Step 2: Filter/Block Links and Usernames (هذا الجزء مُفعّل الآن) ---
    if message.text or message.caption:
        text_to_check = message.text or message.caption

        # 1. Link Filter (URL)
        if URL_PATTERN.search(text_to_check):
            await message.reply_text("⛔️ You cannot send links (URLs) in anonymous chat.", reply_markup=main_keyboard)
            return
        
        # 2. Username Filter (@)
        if '@' in text_to_check:
            await message.reply_text("⛔️ You cannot send user identifiers (usernames) in anonymous chat.", reply_markup=main_keyboard)
            return
            
    # --- Step 3: Relay the message (ترحيل محمي - تم التأكد من تفعيل الحماية) ---
    try:
        protect = True
        
        if message.photo: await context.bot.send_photo(chat_id=partner_id, photo=message.photo[-1].file_id, caption=message.caption, protect_content=protect)
        elif message.document: await context.bot.send_document(chat_id=partner_id, document=message.document.file_id, caption=message.caption, protect_content=protect)
        elif message.video: await context.bot.send_video(chat_id=partner_id, video=message.video.file_id, caption=message.caption, protect_content=protect)
        elif message.sticker: await context.bot.send_sticker(chat_id=partner_id, sticker=message.sticker.file_id, protect_content=protect)
        elif message.voice: await context.bot.send_voice(chat_id=partner_id, voice=message.voice.file_id, caption=message.caption, protect_content=protect)
        elif message.text: 
            # ملاحظة: تم التأكد من تطبيق protect_content=True للنص أيضاً
            await context.bot.send_message(chat_id=partner_id, text=message.text, protect_content=protect)
        
    except (Forbidden, BadRequest) as e:
        if "bot was blocked" in str(e).lower() or "user is deactivated" in str(e).lower() or "chat not found" in str(e).lower():
            logger.warning(f"Partner {partner_id} is unreachable. Ending chat initiated by {sender_id}.")
            await end_chat_in_db(sender_id)
            await message.reply_text("Your partner seems to have blocked the bot or left Telegram. The chat has ended.", reply_markup=main_keyboard)
        else:
            logger.error(f"Failed to send to partner {partner_id}: {e}")
            await message.reply_text("Sorry, your message failed to send. (Your partner might be temporarily unreachable).")
    except Exception as e:
        logger.error(f"An unexpected error occurred sending from {sender_id} to {partner_id}: {e}")

# --- Main Run Function ---

async def post_database_init(application: Application):
    if not await init_database():
        raise RuntimeError("Database connection failed. Aborting startup.")
    if not LOG_CHANNEL_ID:
        logger.warning("WARNING: LOG_CHANNEL_ID not found. Logging/archiving is DISABLED.")
    logger.info("Database connected. Bot is ready to start polling...")

def main():
    if not TELEGRAM_TOKEN:
        logger.critical("CRITICAL: BOT_TOKEN not found.")
        return
    logger.info("Bot starting up...")
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_database_init)
        .build()
    )

    # إضافة معالج زر التحقق من الاشتراك
    application.add_handler(CallbackQueryHandler(handle_join_check, pattern="^check_join$"))
    # إضافة معالج زر تأكيد الحظر
    application.add_handler(CallbackQueryHandler(handle_block_confirmation, pattern="^confirm_block_|^cancel_block$"))
    
    # أمر البث 
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("end", end_command))
    application.add_handler(CommandHandler("next", next_command))
    
    # معالجات الأزرار النصية 
    application.add_handler(MessageHandler(filters.Text(["Search 🔎"]), search_command))
    application.add_handler(MessageHandler(filters.Text(["Stop ⏹️"]), end_command))
    application.add_handler(MessageHandler(filters.Text(["Next 🎲"]), next_command))
    application.add_handler(MessageHandler(filters.Text(["Block User 🚫"]), block_user_command)) 

    
    # المعالج الرئيسي للرسائل
    button_texts = ["Search 🔎", "Stop ⏹️", "Next 🎲", "Block User 🚫"]
    
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.Text(button_texts),
        relay_and_log_message
    ))

    logger.info("Bot setup complete. Starting polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
