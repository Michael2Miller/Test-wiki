import os
import asyncio
import asyncpg
import logging
from typing import Union
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import re

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

# --- URL and Username Pattern Definition ---
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


# ====================================================================
# --- (A) Database Helper Functions (Must be defined early) ---
# ====================================================================

async def is_user_globally_banned(user_id):
    """يتحقق مما إذا كان المستخدم محظوراً بشكل شامل."""
    if not db_pool: return False
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT 1 FROM global_bans WHERE user_id = $1", user_id) is not None

async def init_database():
    """يتصل بقاعدة البيانات وينشئ الجداول."""
    global db_pool
    if not DATABASE_URL:
        logger.critical("CRITICAL: DATABASE_URL not found. Bot cannot start.")
        return False
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as connection:
            
            # (FIX) Ensuring 'language' column exists for existing tables
            try:
                await connection.execute("SELECT language FROM all_users LIMIT 1")
            except (asyncpg.exceptions.UndefinedColumnError, asyncpg.exceptions.UndefinedTableError):
                 try:
                    await connection.execute("ALTER TABLE all_users ADD COLUMN language VARCHAR(5) DEFAULT 'en'")
                    logger.info("Added 'language' column to all_users table.")
                 except Exception:
                     pass

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
                    user_id BIGINT PRIMARY KEY,
                    language VARCHAR(5) DEFAULT 'en' 
                );
            ''')
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS user_blocks (
                    blocker_id BIGINT,
                    blocked_id BIGINT,
                    PRIMARY KEY (blocker_id, blocked_id)
                );
            ''')
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

async def add_user_to_all_list(user_id, lang_code=None):
    """يضيف المستخدم إلى قائمة البث ويسجل اللغة المحددة."""
    if not db_pool: return
    lang_code_to_use = lang_code if lang_code else DEFAULT_LANG
    try:
        async with db_pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO all_users (user_id, language) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET language = EXCLUDED.language",
                user_id, lang_code_to_use
            )
    except Exception as e:
        logger.error(f"Failed to add/update user {user_id} in broadcast list: {e}")

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

# ====================================================================
# --- (B) Language & Subscription Helpers (Needs Access to DB functions above) ---
# ====================================================================

async def get_user_language(user_id):
    """يجلب كود لغة المستخدم من قاعدة البيانات."""
    if not db_pool: return DEFAULT_LANG
    try:
        async with db_pool.acquire() as connection:
            lang_code = await connection.fetchval("SELECT language FROM all_users WHERE user_id = $1", user_id)
            return lang_code if lang_code in SUPPORTED_LANGUAGES else DEFAULT_LANG
    except Exception as e:
        logger.error(f"Failed to fetch language for {user_id}: {e}")
        return DEFAULT_LANG

def _(key, lang_code):
    """دالة الترجمة. تسترجع الرسالة المناسبة باللغة المطلوبة."""
    return LANGUAGES.get(lang_code, LANGUAGES[DEFAULT_LANG]).get(key, LANGUAGES[DEFAULT_LANG].get(key, 'MISSING TRANSLATION'))

async def get_keyboard(lang_code):
    """تنشئ لوحة المفاتيح بناءً على اللغة."""
    keyboard_buttons = [
        [
            _(f'search_btn', lang_code), 
            _(f'next_btn', lang_code)
        ], 
        [
            _(f'block_btn', lang_code), 
            _(f'stop_btn', lang_code)
        ] 
    ]
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)

async def is_user_subscribed(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """تتحقق مما إذا كان المستخدم عضواً في القناة."""
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

# ... (Rest of the code remains the same, ensuring all functions are now defined) ...

# ====================================================================
# --- (C) Command and Message Handlers (Where the logic happens) ---
# ====================================================================

async def send_join_channel_message(update_or_query: Union[Update, Update.callback_query], context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    """ترسل رسالة الاشتراك الإجباري."""
    
    join_text = _('join_channel_msg', lang_code)
    join_btn_text = _('join_channel_btn', lang_code)
    
    keyboard = [
        [
            InlineKeyboardButton(join_btn_text, url=CHANNEL_INVITE_LINK),
            InlineKeyboardButton("✅ " + _('joined_success', lang_code).split(' ')[1], callback_data=f"check_join_{lang_code}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # تحديد وظيفة الإرسال: إما edit_message_text (للكولباك) أو reply_text (للرسالة العادية)
    if isinstance(update_or_query, Update): # رسالة عادية (مثل /start)
        sender = update_or_query.message.reply_text
        await sender(
            join_text,
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            protect_content=True
        )
    elif isinstance(update_or_query, Update.callback_query): # استجابة من زر (بعد اختيار اللغة)
        await update_or_query.edit_message_text(
            join_text,
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            protect_content=True
        )
        return
    else:
        return
        

async def handle_join_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعالج ضغطة زر '✅ I have joined' للتحقق من الاشتراك."""
    query = update.callback_query
    user_id = query.from_user.id
    lang_code = query.data.split('_')[2] if len(query.data.split('_')) > 2 else DEFAULT_LANG
    
    await query.answer(_('joined_success', lang_code).split(' ')[1])
    
    if await is_user_subscribed(user_id, context):
        await query.edit_message_text(
            _('joined_success', lang_code),
            reply_markup=None, 
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            protect_content=True
        )
        await query.message.reply_text(_('use_buttons_msg', lang_code), reply_markup=await get_keyboard(lang_code), protect_content=True)
    else:
        await query.answer(_('join_channel_btn', lang_code), show_alert=True)


async def show_initial_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض واجهة اختيار اللغة الأولية للمستخدمين الجدد."""
    
    language_buttons = []
    for code in SUPPORTED_LANGUAGES:
        name = LANGUAGES[code]['language_name']
        language_buttons.append([InlineKeyboardButton(name, callback_data=f"initial_set_lang_{code}")])
        
    reply_markup = InlineKeyboardMarkup(language_buttons)
    
    await update.message.reply_text(
        _('initial_selection_msg', DEFAULT_LANG),
        reply_markup=reply_markup,
        parse_mode=constants.ParseMode.MARKDOWN,
        protect_content=True
    )

async def handle_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    await query.answer()

    # --- Initial Setup Logic (Flow: Select Language -> Force Join) ---
    if data.startswith("initial_set_lang_"):
        new_lang_code = data.split('_')[3]
        
        await add_user_to_all_list(user_id, new_lang_code) 
            
        lang_name = LANGUAGES[new_lang_code]['language_name']
        
        await query.edit_message_text(
            _('settings_saved', new_lang_code).format(lang_name=lang_name), 
            reply_markup=None,
            parse_mode=constants.ParseMode.MARKDOWN
        )
        
        await send_join_channel_message(query, context, new_lang_code) 
        
        logger.info(f"New User {user_id} set initial language to {new_lang_code} and started verification.")
        return

    # --- Existing user language selection logic (regular settings) ---
    if data.startswith("set_lang_"):
        new_lang_code = data.split('_')[2]
        
        if new_lang_code not in SUPPORTED_LANGUAGES:
            await query.answer("Invalid language selection.")
            return
        
        try:
            await add_user_to_all_list(user_id, new_lang_code) 
                
            lang_name = LANGUAGES[new_lang_code]['language_name']
            
            if new_lang_code == 'ar':
                 settings_guidance = "\n\n🌐 يمكنك تغيير اللغة في أي وقت بإرسال /settings."
            elif new_lang_code == 'es':
                 settings_guidance = "\n\n🌐 Puedes cambiar el idioma en cualquier momento escribiendo /settings."
            else:
                 settings_guidance = "\n\n🌐 You can change the language anytime by typing /settings."
            
            await query.edit_message_text(
                _('settings_saved', new_lang_code).format(lang_name=lang_name) + settings_guidance,
                reply_markup=None,
                parse_mode=constants.ParseMode.MARKDOWN
            )
            
            await query.message.reply_text(
                 _('use_buttons_msg', new_lang_code),
                 reply_markup=await get_keyboard(new_lang_code), 
                 protect_content=True
            )
            
            logger.info(f"User {user_id} set language to {new_lang_code}")
            
        except Exception as e:
            logger.error(f"Failed to update language for {user_id}: {e}")
            await query.answer("An error occurred while saving your preference.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if await is_user_globally_banned(user_id):
        await update.message.reply_text(_('globally_banned', DEFAULT_LANG), protect_content=True)
        return
    
    user_in_db = await check_if_user_exists(user_id)
    
    if not user_in_db:
        await show_initial_language_selection(update, context)
        return
        
    lang_code = await get_user_language(user_id)
    keyboard = await get_keyboard(lang_code)

    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context, lang_code)
        return
        
    if await get_partner_from_db(user_id):
        await update.message.reply_text(_('already_in_chat', lang_code), reply_markup=keyboard, protect_content=True)
    elif await is_user_waiting_db(user_id):
        await update.message.reply_text(_('already_searching', lang_code), reply_markup=keyboard, protect_content=True)
    else:
        await update.message.reply_text(
            _('welcome', lang_code),
            reply_markup=keyboard,
            protect_content=True
        )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if await is_user_globally_banned(user_id):
        await update.message.reply_text(_('globally_banned', DEFAULT_LANG), protect_content=True)
        return
    
    lang_code = await get_user_language(user_id)
    keyboard = await get_keyboard(lang_code)

    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context, lang_code)
        return
    if await get_partner_from_db(user_id):
        await update.message.reply_text(_('search_already_in_chat', lang_code), protect_content=True)
        return
    if await is_user_waiting_db(user_id):
        await update.message.reply_text(_('search_already_searching', lang_code), protect_content=True)
        return
    
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            current_user_lang = await get_user_language(user_id) 
            
            partner_id = await connection.fetchval(
                """
                DELETE FROM waiting_queue
                WHERE user_id = (
                    SELECT w.user_id 
                    FROM waiting_queue w
                    JOIN all_users au ON w.user_id = au.user_id 
                    WHERE w.user_id != $1 
                      AND au.language = $2 
                      AND w.user_id NOT IN (SELECT blocked_id FROM user_blocks WHERE blocker_id = $1)
                      AND $1 NOT IN (SELECT blocked_id FROM user_blocks WHERE blocker_id = w.user_id)
                      AND w.user_id NOT IN (SELECT user_id FROM global_bans)
                    ORDER BY w.timestamp ASC LIMIT 1
                )
                RETURNING user_id
                """, user_id, current_user_lang
            )
            
            if partner_id:
                await connection.execute("INSERT INTO active_chats (user_id, partner_id) VALUES ($1, $2), ($2, $1)", user_id, partner_id)
                logger.info(f"Match found! {user_id} <-> {partner_id}. Lang: {current_user_lang}")
                
                partner_lang = await get_user_language(partner_id)
                await context.bot.send_message(chat_id=user_id, text=_('partner_found', lang_code), reply_markup=keyboard, protect_content=True)
                await context.bot.send_message(chat_id=partner_id, text=_('partner_found', partner_lang), reply_markup=await get_keyboard(partner_lang), protect_content=True)
            else:
                await connection.execute("INSERT INTO waiting_queue (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                await update.message.reply_text(_('search_wait', lang_code), protect_content=True)
                logger.info(f"User {user_id} added to DB queue. Lang: {current_user_lang}")

async def end_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if await is_user_globally_banned(user_id):
        await update.message.reply_text(_('globally_banned', DEFAULT_LANG), protect_content=True)
        return

    lang_code = await get_user_language(user_id)
    keyboard = await get_keyboard(lang_code)

    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context, lang_code)
        return
        
    partner_id = await end_chat_in_db(user_id)
    
    if partner_id:
        logger.info(f"Chat ended by {user_id}. Partner was {partner_id}.")
        await context.bot.send_message(chat_id=user_id, text=_('end_msg_user', lang_code), reply_markup=keyboard, protect_content=True)
        try:
            partner_lang = await get_user_language(partner_id)
            await context.bot.send_message(chat_id=partner_id, text=_('end_msg_partner', partner_lang), reply_markup=await get_keyboard(partner_lang), protect_content=True)
        except (Forbidden, BadRequest) as e:
             logger.warning(f"Could not notify partner {partner_id} about chat end: {e}")
    elif await is_user_waiting_db(user_id):
        await remove_from_wait_queue_db(user_id)
        logger.info(f"User {user_id} cancelled search.")
        await update.message.reply_text(_('end_search_cancel', lang_code), reply_markup=keyboard, protect_content=True)
    else:
        await update.message.reply_text(_('end_not_in_chat', lang_code), reply_markup=keyboard, protect_content=True)

async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if await is_user_globally_banned(user_id):
        await update.message.reply_text(_('globally_banned', DEFAULT_LANG), protect_content=True)
        return

    lang_code = await get_user_language(user_id)
    keyboard = await get_keyboard(lang_code)

    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context, lang_code)
        return
        
    # --- 1. End Chat Logic ---
    partner_id = await end_chat_in_db(user_id)
    
    if partner_id:
        logger.info(f"Chat ended by {user_id} (via /next). Partner was {partner_id}.")
        await context.bot.send_message(chat_id=user_id, text=_('next_msg_user', lang_code), protect_content=True)
        try:
            partner_lang = await get_user_language(partner_id)
            await context.bot.send_message(chat_id=partner_id, text=_('end_msg_partner', partner_lang), reply_markup=await get_keyboard(partner_lang), protect_content=True)
        except (Forbidden, BadRequest) as e:
            logger.warning(f"Could not notify partner {partner_id} about chat end: {e}")
    elif await is_user_waiting_db(user_id):
        await update.message.reply_text(_('next_already_searching', lang_code), protect_content=True)
        return
    else:
        await update.message.reply_text(_('next_not_in_chat', lang_code), protect_content=True)

    # --- 2. Search Logic (Matching with Language Filter) ---
    async with db_pool.acquire() as connection:
        async with connection.transaction():
            current_user_lang = await get_user_language(user_id)
            
            partner_id_new = await connection.fetchval(
                """
                DELETE FROM waiting_queue
                WHERE user_id = (
                    SELECT w.user_id 
                    FROM waiting_queue w
                    JOIN all_users au ON w.user_id = au.user_id 
                    WHERE w.user_id != $1 
                      AND au.language = $2 
                      AND w.user_id NOT IN (SELECT blocked_id FROM user_blocks WHERE blocker_id = $1)
                      AND $1 NOT IN (SELECT blocked_id FROM user_blocks WHERE blocker_id = w.user_id)
                      AND w.user_id NOT IN (SELECT user_id FROM global_bans)
                    ORDER BY w.timestamp ASC LIMIT 1
                )
                RETURNING user_id
                """, user_id, current_user_lang
            )
            
            if partner_id_new:
                await connection.execute("INSERT INTO active_chats (user_id, partner_id) VALUES ($1, $2), ($2, $1)", user_id, partner_id_new)
                logger.info(f"Match found! {user_id} <-> {partner_id_new}. Lang: {current_user_lang}")
                
                partner_lang = await get_user_language(partner_id_new)
                await context.bot.send_message(chat_id=user_id, text=_('partner_found', lang_code), reply_markup=keyboard, protect_content=True)
                await context.bot.send_message(chat_id=partner_id_new, text=_('partner_found', partner_lang), reply_markup=await get_keyboard(partner_lang), protect_content=True)
            else:
                await connection.execute("INSERT INTO waiting_queue (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                logger.info(f"User {user_id} added/remains in DB queue (via /next). Lang: {current_user_lang}")

# --- (7) Reporting and Block Handlers ---

async def block_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if await is_user_globally_banned(user_id):
        await update.message.reply_text(_('globally_banned', DEFAULT_LANG), protect_content=True)
        return

    lang_code = await get_user_language(user_id)
    keyboard = await get_keyboard(lang_code)

    if not await is_user_subscribed(user_id, context):
        await send_join_channel_message(update, context, lang_code)
        return

    reported_id = await get_partner_from_db(user_id)
    
    if not reported_id:
        if await is_user_waiting_db(user_id):
            await update.message.reply_text(_('block_while_searching', lang_code), protect_content=True)
        else:
            await update.message.reply_text(_('block_not_in_chat', lang_code), reply_markup=keyboard, protect_content=True)
        return
    
    # 1. إرسال رسالة التأكيد مع الأزرار المضمنة
    confirmation_markup, confirm_text = await get_confirmation_keyboard(reported_id, lang_code)
    
    await update.message.reply_text(
        confirm_text,
        reply_markup=confirmation_markup,
        parse_mode=constants.ParseMode.MARKDOWN,
        protect_content=True
    )

async def handle_block_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    # استخراج اللغة وال ID من الـ Callback Data
    parts = data.split('_')
    lang_code = parts[-1] if len(parts) > 2 else DEFAULT_LANG
    
    await query.answer()
    keyboard = await get_keyboard(lang_code)
    
    if data.startswith("cancel_block_"):
        await query.edit_message_text(_('block_cancelled', lang_code))
        return

    if data.startswith("confirm_block_"):
        reported_id = int(parts[2])
        
        await add_user_block(user_id, reported_id) 
        
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

        partner_id = await end_chat_in_db(user_id)
        
        await query.edit_message_text(
            _('block_success', lang_code),
            reply_markup=None, 
            protect_content=True
        )
        await query.message.reply_text(_('use_buttons_msg', lang_code), reply_markup=keyboard, protect_content=True)
        
        if reported_id:
            logger.info(f"Chat ended by {user_id} (via Block & Report). Partner was {reported_id}.")
            try:
                partner_lang = await get_user_language(reported_id)
                await context.bot.send_message(chat_id=reported_id, text=_('end_msg_partner', partner_lang), reply_markup=await get_keyboard(partner_lang), protect_content=True)
            except (Forbidden, BadRequest) as e:
                logger.warning(f"Could not notify partner {reported_id} about chat end: {e}")

# --- (4) Broadcast Command ---

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message = update.message
    
    if user_id != ADMIN_ID:
        await message.reply_text("🚫 Access denied. This command is for the administrator only.", protect_content=True)
        return

    is_media_broadcast = message.photo or message.video or message.document
    
    if not is_media_broadcast and not context.args:
        await message.reply_text(
            "Usage:\n"
            "1. For text: `/broadcast Your message here`\n"
            "2. For media: Send the photo/video/document with `/broadcast` and your message in the caption.",
            protect_content=True
        )
        return

    all_users = await get_all_users()
    
    if not all_users:
        await message.reply_text("No users found in the database to broadcast to.", protect_content=True)
        return

    success_count = 0
    fail_count = 0
    
    await message.reply_text(f"Starting broadcast to {len(all_users)} users...", protect_content=True)
    
    for target_user_id in all_users:
        try:
            if is_media_broadcast:
                await context.bot.copy_message(
                    chat_id=target_user_id,
                    from_chat_id=user_id,
                    message_id=message.message_id
                )
            else:
                message_to_send = " ".join(context.args)
                await context.bot.send_message(
                    chat_id=target_user_id, 
                    text=message_to_send, 
                    parse_mode=constants.ParseMode.MARKDOWN,
                    protect_content=True
                ) 
            
            success_count += 1
        except Forbidden:
            fail_count += 1
            logger.warning(f"User {target_user_id} blocked the bot. Skipping.")
        except Exception as e:
            fail_count += 1
            logger.error(f"Failed to send broadcast to {target_user_id}: {e}")
            
    await message.reply_text(
        f"✅ **Broadcast complete!**\n"
        f"Sent successfully to: {success_count} users.\n"
        f"Failed (Bot blocked/Error): {fail_count} users.",
        protect_content=True
    )

# --- (6) Relay Message Handler (مع تفعيل فلاتر الروابط واليوزرات) ---

async def relay_and_log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.message.from_user.id
    message = update.message
    
    if await is_user_globally_banned(sender_id):
        await update.message.reply_text(_('globally_banned', DEFAULT_LANG), protect_content=True)
        return
    
    lang_code = await get_user_language(sender_id) 
    
    if not await is_user_subscribed(sender_id, context):
        await send_join_channel_message(update, context, lang_code)
        return
    
    partner_id = await get_partner_from_db(sender_id)
    
    if not partner_id:
        await message.reply_text(_('not_in_chat_msg', lang_code), reply_markup=await get_keyboard(lang_code), protect_content=True)
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
            
    # --- Step 2: Filter/Block Links and Usernames ---
    if message.text or message.caption:
        text_to_check = message.text or message.caption

        # 1. Link Filter (URL)
        if URL_PATTERN.search(text_to_check):
            await message.reply_text(_('link_blocked', lang_code), reply_markup=await get_keyboard(lang_code), protect_content=True)
            return
        
        # 2. Username Filter (@)
        if '@' in text_to_check:
            await message.reply_text(_('username_blocked', lang_code), reply_markup=await get_keyboard(lang_code), protect_content=True)
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
            await context.bot.send_message(chat_id=partner_id, text=message.text, protect_content=protect)
        
    except (Forbidden, BadRequest) as e:
        if "bot was blocked" in str(e).lower() or "user is deactivated" in str(e).lower() or "chat not found" in str(e).lower():
            logger.warning(f"Partner {partner_id} is unreachable. Ending chat initiated by {sender_id}.")
            await end_chat_in_db(sender_id)
            await message.reply_text(_('unreachable_partner', lang_code), reply_markup=await get_keyboard(lang_code), protect_content=True)
        else:
            logger.error(f"Failed to send to partner {partner_id}: {e}")
            await message.reply_text("Sorry, your message failed to send. (Your partner might be temporarily unreachable).", protect_content=True)
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

    # 1. معالجات الأزرار المضمنة (Inline Buttons)
    application.add_handler(CallbackQueryHandler(handle_join_check, pattern=r"^check_join_"))
    application.add_handler(CallbackQueryHandler(handle_block_confirmation, pattern=r"^confirm_block_|^cancel_block$"))
    application.add_handler(CallbackQueryHandler(handle_language_selection, pattern=r"^set_lang_|initial_set_lang_")) 
    
    # 2. أوامر الأدمن
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("sendid", sendid_command)) 
    application.add_handler(CommandHandler("banuser", banuser_command))
    
    # 3. أوامر المستخدم
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("end", end_command))
    application.add_handler(CommandHandler("next", next_command))
    application.add_handler(CommandHandler("settings", settings_command))
    
    # 4. معالجات الأزرار النصية (Reply Buttons)
    
    search_texts = [lang['search_btn'] for lang in LANGUAGES.values()]
    stop_texts = [lang['stop_btn'] for lang in LANGUAGES.values()]
    next_texts = [lang['next_btn'] for lang in LANGUAGES.values()]
    block_texts = [lang['block_btn'] for lang in LANGUAGES.values()]

    application.add_handler(MessageHandler(filters.Text(search_texts), search_command)) 
    application.add_handler(MessageHandler(filters.Text(stop_texts), end_command))   
    application.add_handler(MessageHandler(filters.Text(next_texts), next_command))   
    application.add_handler(MessageHandler(filters.Text(block_texts), block_user_command)) 
    
    # 5. المعالج الرئيسي للرسائل (يجب أن يكون الأخير)
    all_button_texts = search_texts + stop_texts + next_texts + block_texts
    
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.Text(all_button_texts),
        relay_and_log_message
    ))

    logger.info("Bot setup complete. Starting polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
