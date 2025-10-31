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

# --- (1) Translation Dictionaries and Helpers ---
LANGUAGES = {
    'en': {
        'language_name': "English 🇬🇧",
        'welcome': "Welcome to 🎲 **Random Partner**\nThe anonymous Chat Bot!\n\nPress 'Search' to find a partner.",
        'already_in_chat': "You are currently in a chat. Use the buttons below.",
        'already_searching': "You are currently in the waiting queue. Use the buttons below.",
        'search_btn': "Search 🔎",
        'next_btn': "Next 🎲",
        'stop_btn': "Stop ⏹️",
        'block_btn': "Block User 🚫",
        'search_already_in_chat': "You are already in a chat! Press 'Stop' or 'Next' first.",
        'search_already_searching': "You are already searching. Please wait...",
        'search_wait': "🔎 Searching for a partner... Please wait.",
        'partner_found': "✅ Partner found! The chat has started. (You are anonymous).",
        'end_msg_user': "🔚 You have ended the chat.",
        'end_msg_partner': "⚠️ Your partner has left the chat.",
        'end_search_cancel': "Search cancelled.",
        'end_not_in_chat': "You are not currently in a chat or searching.",
        'link_blocked': "⛔️ You cannot send links (URLs) in anonymous chat.",
        'username_blocked': "⛔️ You cannot send user identifiers (usernames) in anonymous chat.",
        'settings_text': "🌐 **Language Settings**\n\nSelect your preferred language for the bot's interface and for matching partners:",
        'settings_saved': "✅ Language updated to {lang_name}. Press /start to see the changes.",
        'admin_denied': "🚫 Access denied. This command is for the administrator only.",
        'globally_banned': "🚫 Your access to this bot has been suspended permanently.",
        'use_buttons_msg': "Use the buttons below to control the chat:",
        'initial_selection_msg': "🌐 **Welcome to the Anonymous Chat Bot!**\n\nPlease select your preferred language to continue the setup:", 
        'cancel_op_btn': "❌ Cancel", 
        'join_channel_msg': r"👋 **Welcome to Random Partner 🎲\!**" + "\n\n"
                            r"To use this bot, you are required to join our official channel\." + "\n\n"
                            r"Please join the channel using the button below, then press '✅ I have joined'\.",
        'join_channel_btn': "Join Channel",
        'joined_btn': "I have joined",
        'joined_success': r"🎉 **Thank you for joining\!**" + "\n\n"
                          r"You can now use the bot\. Press /start or use the buttons below\.",
        'block_confirm_text': "🚫 **CONFIRM BLOCK AND REPORT**\n\nAre you sure you want to block the current partner and send a report to the Telegram Team?\n\n*(This action will end the chat immediately.)*",
        'block_cancelled': "🚫 Block/Report operation cancelled. You can continue chatting.",
        'block_success': "🛑 Thank you! The user has been blocked and the chat has ended.\n\nYour report has been successfully sent for review.\n\nPress Next 🎲 to find a new partner.",
        'next_not_in_chat': "🔎 Searching for a partner... Please wait.",
        'next_msg_user': "🔎 Searching for a new partner...",
        'next_already_searching': "You are already searching. Please wait...",
        'block_not_in_chat': "You are not currently in a chat to block anyone.",
        'block_while_searching': "You cannot block anyone while searching. Use 'Stop ⏹️' first.",
        'unreachable_partner': "Your partner seems to have blocked the bot or left Telegram. The chat has ended.",
        'not_in_chat_msg': "You are not in a chat. Press 'Search' to find a partner.",
        'partner_prefix': "Random partner🎲 : ",
        # --- NEW KEYS FOR LANGUAGE MATCHING ---
        'search_options_msg': "🌐 Match Settings: What language do you prefer your partner to speak?",
        'match_my_lang_btn': "Same Language 🇬🇧",
        'match_other_lang_btn': "Other Language (Practice) 🌍",
        'select_target_lang_msg': "🌐 Select the language you are looking for in a partner:",
        'search_wait_specific': "🔎 Searching for a partner who speaks {target_lang_name}... Please wait.",
    },
    'ar': {
        'language_name': "العربية 🇸🇦",
        'welcome': "مرحباً بك في 🎲 **شريك عشوائي**\nبوت الدردشة المجهول!\n\nاضغط 'بحث' للعثور على شريك.",
        'already_in_chat': "أنت حالياً في محادثة. استخدم الأزرار أدناه.",
        'already_searching': "أنت حالياً في قائمة الانتظار. استخدم الأزرار أدناه.",
        'search_btn': "بحث 🔎",
        'next_btn': "التالي 🎲",
        'stop_btn': "إيقاف ⏹️",
        'block_btn': "حظر مستخدم 🚫",
        'search_already_in_chat': "أنت بالفعل في محادثة! اضغط 'إيقاف' أو 'التالي' أولاً.",
        'search_already_searching': "أنت بالفعل تبحث. يرجى الانتظار...",
        'search_wait': "🔎 البحث عن شريك... يرجى الانتظار.",
        'partner_found': "✅ تم العثور على شريك! بدأت المحادثة. (أنت مجهول).",
        'end_msg_user': "🔚 لقد أنهيت المحادثة.",
        'end_msg_partner': "⚠️ لقد غادر شريكك المحادثة.",
        'end_search_cancel': "تم إلغاء البحث.",
        'end_not_in_chat': "أنت لست في محادثة حالياً ولا تبحث.",
        'link_blocked': "⛔️ لا يمكنك إرسال روابط (URLs) في الدردشة المجهولة.",
        'username_blocked': "⛔️ لا يمكنك إرسال معرفات مستخدمين (usernames) في الدردشة المجهولة.",
        'settings_text': "🌐 **إعدادات اللغة**\n\nاختر لغتك المفضلة لواجهة البوت وللمطابقة مع الشركاء:",
        'settings_saved': "✅ تم تحديث اللغة إلى {lang_name}. اضغط /start لرؤية التغييرات.",
        'admin_denied': "🚫 الوصول مرفوض. هذا الأمر مخصص للمدير فقط.",
        'globally_banned': "🚫 تم إيقاف وصولك إلى هذا البوت بشكل دائم.",
        'use_buttons_msg': "استخدم الأزرار أدناه للتحكم في الدردشة:",
        'initial_selection_msg': "🌐 **مرحباً بك في بوت الدردشة العشوائية!**\n\nالرجاء اختيار لغتك المفضلة للمتابعة:", 
        'cancel_op_btn': "❌ إلغاء", 
        'join_channel_msg': r"👋 **مرحباً بك في شريك عشوائي 🎲\!**" + "\n\n"
                            r"لاستخدام هذا البوت، يجب عليك الانضمام إلى قناتنا الرسمية\." + "\n\n"
                            r"يرجى الانضمام للقناة عبر الزر أدناه، ثم اضغط '✅ لقد انضممت'\.",
        'join_channel_btn': "انضم للقناة",
        'joined_btn': "لقد انضممت",
        'joined_success': r"🎉 **شكراً لانضمامك\!**" + "\n\n"
                          r"يمكنك الآن استخدام البوت\. اضغط /start أو استخدم الأزرار أدناه\.",
        'block_confirm_text': "🚫 **تأكيد الحظر والإبلاغ**\n\nهل أنت متأكد أنك تريد حظر الشريك الحالي وإرسال تقرير إلى فريق تليجرام التقني؟\n\n*(سيؤدي هذا الإجراء إلى إنهاء المحادثة فوراً.)*",
        'block_cancelled': "🚫 تم إلغاء عملية الحظر/الإبلاغ. يمكنك متابعة الدردشة.",
        'block_success': "🛑 شكراً لك! تم حظر المستخدم وتم إنهاء المحادثة.\n\nتم إرسال تقريرك للمراجعة بنجاح.\n\nاضغط التالي 🎲 للعثور على شريك جديد.",
        'next_not_in_chat': "🔎 البحث عن شريك... يرجى الانتظار.",
        'next_msg_user': "🔎 البحث عن شريك جديد...",
        'next_already_searching': "أنت بالفعل تبحث. يرجى الانتظار...",
        'block_not_in_chat': "أنت لست حالياً في محادثة لحظر أي شخص.",
        'block_while_searching': "لا يمكنك الحظر أثناء البحث. استخدم 'إيقاف ⏹️' أولاً.",
        'unreachable_partner': "يبدو أن شريكك قام بحظر البوت أو غادر تيليجرام. انتهت المحادثة.",
        'not_in_chat_msg': "أنت لست في محادثة. اضغط 'بحث' للعثور على شريك.",
        'partner_prefix': "صديق/ة🎲 : ",
        # --- NEW KEYS FOR LANGUAGE MATCHING ---
        'search_options_msg': "🌐 إعدادات المطابقة: كيف تفضل لغة شريكك؟",
        'match_my_lang_btn': "نفس لغتي 🇸🇦",
        'match_other_lang_btn': "لغة أخرى (للتمرين) 🌍",
        'select_target_lang_msg': "🌐 اختر اللغة التي تبحث عن شريك يتحدث بها:",
        'search_wait_specific': "🔎 البحث عن شريك يتحدث {target_lang_name}... يرجى الانتظار.",
    },
    'es': {
        'language_name': "Españول 🇪🇸",
        'welcome': "¡Bienvenido a 🎲 **Compañero Aleatorio**\nEl Bot de Chat Anónimo!\n\nPresiona 'Buscar' para encontrar un compañero.",
        'already_in_chat': "Actualmente estás en un chat. Usa los botones de abajo.",
        'already_searching': "Actualmente estás en la cola de espera. Usa los botones de abajo.",
        'search_btn': "Buscar 🔎",
        'next_btn': "Siguiente 🎲",
        'stop_btn': "Parar ⏹️",
        'block_btn': "Bloquear Usuario 🚫",
        'search_already_in_chat': "¡Ya estás en un chat! Presiona 'Parar' o 'Siguiente' primero.",
        'search_already_searching': "Ya estás buscando. Por favor espera...",
        'search_wait': "🔎 Buscando un compañero... Por favor espera.",
        'partner_found': "✅ ¡Compañero encontrado! El chat ha comenzado. (Eres anónimo).",
        'end_msg_user': "🔚 Has finalizado el chat.",
        'end_msg_partner': "⚠️ Tu compañero ha abandonado el chat.",
        'end_search_cancel': "Búsqueda cancelada.",
        'end_not_in_chat': "Actualmente no estás en un chat ni buscando.",
        'link_blocked': "⛔️ No puedes enviar enlaces (URLs) en el chat anónimo.",
        'username_blocked': "⛔️ No puedes enviar identificadores de usuario (usernames) en el chat anónimo.",
        'settings_text': "🌐 **Configuración de Idioma**\n\nSelecciona tu idioma preferido para la interfaz del bot y para emparejarte con compañeros:",
        'settings_saved': "✅ Idioma actualizado a {lang_name}. Presiona /start para ver los cambios.",
        'admin_denied': "🚫 Acceso denegado. Este comando es solo para el administrador.",
        'globally_banned': "🚫 Tu acceso a este bot ha sido suspendido permanentemente.",
        'use_buttons_msg': "Usa los botones de abajo para controlar el chat:",
        'initial_selection_msg': "🌐 **¡Bienvenido al Bot de Chat Anónimo!**\n\nPor favor, selecciona tu idioma preferido para continuar con la configuración:", 
        'cancel_op_btn': "❌ Anular", 
        'join_channel_msg': r"👋 **¡Bienvenido a Compañero Aleatorio 🎲\!**" + "\n\n"
                            r"Para usar este bot, se requiere que te unas a nuestro canal oficial\." + "\n\n"
                            r"Por favor, únete al canal usando el botón de abajo, luego presiona '✅ Me he unido'\.",
        'join_channel_btn': "Unirse al Canal",
        'joined_btn': "Me he unido",
        'joined_success': r"🎉 **¡Gracias por unirte\!**" + "\n\n"
                          r"Ahora puedes usar el bot\. Presiona /start o usa los botones de abajo\.",
        'block_confirm_text': "🚫 **CONFIRMAR BLOQUEO E INFORME**\n\n¿Estás seguro de que quieres bloquear al compañero actual y enviar un informe al Equipo de Telegram?\n\n*(Esta acción finalizará el chat inmediatamente.)*",
        'block_cancelled': "🚫 Operación de Bloqueo/Informe cancelada. Puedes seguir chateando.",
        'block_success': "🛑 ¡Gracias! El usuario ha sido bloqueado y el chat ha finalizado.\n\nTu informe ha sido enviado para revisión exitosamente.\n\nPresiona Siguiente 🎲 para encontrar un nuevo compañero.",
        'next_not_in_chat': "🔎 Buscando un compañero... Por favor espera.",
        'next_msg_user': "🔎 Buscando un nuevo compañero...",
        'next_already_searching': "Ya estás buscando. Por favor espera...",
        'block_not_in_chat': "No estás actualmente en un chat para bloquear a nadie.",
        'block_while_searching': "No puedes bloquear a nadie mientras buscas. Usa 'Parar ⏹️' primero.",
        'unreachable_partner': "Parece que tu compañero ha bloqueado al bot o dejó Telegram. El chat ha finalizado.",
        'not_in_chat_msg': "No estás en un chat. Presiona 'Buscar' para encontrar un compañero.",
        'partner_prefix': "tu amigo/a 🎲 : ",
        # --- NEW KEYS FOR LANGUAGE MATCHING ---
        'search_options_msg': "🌐 Configuración de Emparejamiento: ¿Cómo prefieres el idioma de tu compañero؟",
        'match_my_lang_btn': "Mismo idioma 🇪🇸",
        'match_other_lang_btn': "Otro idioma (para practicar) 🌍",
        'select_target_lang_msg': "🌐 Selecciona el idioma que buscas en un compañero:",
        'search_wait_specific': "🔎 Buscando un compañero que hable {target_lang_name}... Por favor espera.",
    }
}
DEFAULT_LANG = 'en'
SUPPORTED_LANGUAGES = ['en', 'ar', 'es']

# --- (2) Utility Functions (Helpers) ---

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
            _('search_btn', lang_code), 
            _('next_btn', lang_code)
        ], 
        [
            _('block_btn', lang_code), 
            _('stop_btn', lang_code)
        ] 
    ]
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)

# --- URL and Username Pattern Definition ---
URL_PATTERN = re.compile(
    r'(https?://|www\.|t\.me/|t\.co/|telegram\.me/|telegram\.dog/)'
    r'[\w\.-]+(\.[\w\.-]+)*([\w\-\._~:/\?#\[\]@!$&\'()*+,;=])*',
    re.IGNORECASE
)

# --- Define Confirmation Keyboard ---
async def get_confirmation_keyboard(reported_id, lang_code):
    """لوحة تأكيد الحظر بناءً على اللغة."""
    confirm_text = _('block_confirm_text', lang_code)
    cancel_text = _('cancel_op_btn', lang_code) 
    
    keyboard = [
        [InlineKeyboardButton("✅ " + _('block_btn', lang_code), callback_data=f"confirm_block_{reported_id}_{lang_code}")],
        [InlineKeyboardButton(cancel_text, callback_data=f"cancel_block_{lang_code}")]
    ]
    return InlineKeyboardMarkup(keyboard), confirm_text

# --- (3) Database Helper Functions ---

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
            
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS all_users (
                    user_id BIGINT PRIMARY KEY,
                    language VARCHAR(5) DEFAULT 'en' 
                );
            ''')
            
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS active_chats (
                    user_id BIGINT PRIMARY KEY,
                    partner_id BIGINT NOT NULL UNIQUE
                );
            ''')
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS waiting_queue (
                    user_id BIGINT PRIMARY KEY,
                    timestamp TIMESTZ DEFAULT (NOW() AT TIME ZONE 'UTC')
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

async def post_database_init(application: Application) -> None:
    """يتم استدعاؤها بعد تهيئة التطبيق."""
    if not await init_database():
        logger.critical("Failed to initialize database. Shutting down.")
        await application.stop()

async def check_if_user_exists(user_id):
    """يتحقق مما إذا كان المستخدم موجوداً في جدول all_users."""
    if not db_pool: return False
    async with db_pool.acquire() as connection:
        return await connection.fetchval("SELECT 1 FROM all_users WHERE user_id = $1", user_id) is not None

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

# --- (4) Command and Callback Handlers (Defined at the top for NameError fix) ---

async def send_join_channel_message(update_or_query: Union[Update, Update.callback_query], context: ContextTypes.DEFAULT_TYPE, lang_code: str):
    """ترسل رسالة الاشتراك الإجباري."""
    
    join_text = _('join_channel_msg', lang_code)
    join_btn_text = _('join_channel_btn', lang_code)
    joined_btn_text = _('joined_btn', lang_code)
    
    keyboard = [
        [
            InlineKeyboardButton(join_btn_text, url=CHANNEL_INVITE_LINK),
            InlineKeyboardButton("✅ " + joined_btn_text, callback_data=f"check_join_{lang_code}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if isinstance(update_or_query, Update): 
        sender = update_or_query.message.reply_text
        await sender(
            join_text,
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            protect_content=True
        )
    else:  # CallbackQuery
        await update_or_query.edit_message_text(
            join_text,
            reply_markup=reply_markup,
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            protect_content=True
        )

async def handle_join_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعالج ضغطة زر '✅ I have joined' للتحقق من الاشتراك."""
    query = update.callback_query
    user_id = query.from_user.id
    lang_code = query.data.split('_')[2] if len(query.data.split('_')) > 2 else DEFAULT_LANG
    
    await query.answer()
    
    if await is_user_subscribed(user_id, context):
        await query.edit_message_text(
            _('joined_success', lang_code),
            reply_markup=None, 
            parse_mode=constants.ParseMode.MARKDOWN_V2,
            protect_content=True
        )
        await query.message.reply_text(_('use_buttons_msg', lang_code), reply_markup=await get_keyboard(lang_code), protect_content=True)
    else:
        joined_btn_text = _('joined_btn', lang_code)
        await query.answer("⚠️ " + joined_btn_text, show_alert=True)

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

async def handle_target_lang_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    await query.answer()
    
    # 1. حالة "نفس لغتي" (تم اختيارها مباشرة في show_search_options) أو اختيار محدد
    if data.startswith("match_lang_"):
        _, user_lang_code, target_lang_code = data.split('_')
        
        # نرسل إلى دالة المطابقة لبدء الانتظار
        await start_matching_process(update, context, target_lang_code)
        return
        
    # 2. حالة "اختيار لغة محددة"
    if data.startswith("select_target_lang_"):
        _, _, user_lang_code = data.split('_')
        
        # إنشاء أزرار للغات المتاحة كأهداف للمطابقة
        language_buttons = []
        for code in SUPPORTED_LANGUAGES:
            # نتجنب إظهار اللغة الأصلية للمستخدم في قائمة "اللغات الأخرى"
            if code != user_lang_code:
                name = LANGUAGES[code]['language_name']
                # نستخدم 'match_lang_' مع كود اللغة المطلوبة كهدف
                language_buttons.append([InlineKeyboardButton(name, callback_data=f"match_lang_{user_lang_code}_{code}")])
                
        # نضيف زر إلغاء
        cancel_text = _('cancel_op_btn', user_lang_code)
        language_buttons.append([InlineKeyboardButton(cancel_text, callback_data="cancel_operation")])
        
        await query.edit_message_text(
            _('select_target_lang_msg', user_lang_code),
            reply_markup=InlineKeyboardMarkup(language_buttons),
            parse_mode=constants.ParseMode.MARKDOWN,
            protect_content=True
        )
        return
    
    # حالة إلغاء العملية
    if data == "cancel_operation":
        user_lang_code = await get_user_language(user_id)
        await query.edit_message_text("❌ تم إلغاء عملية البحث.", reply_markup=None)
        await query.message.reply_text(_('use_buttons_msg', user_lang_code), reply_markup=await get_keyboard(user_lang_code), protect_content=True)

# --- NEW FUNCTION: Core Matching Logic ---
async def start_matching_process(update: Update, context: ContextTypes.DEFAULT_TYPE, target_lang_code: str):
    user_id = update.callback_query.from_user.id
    user_lang_code = await get_user_language(user_id) 
    
    keyboard = await get_keyboard(user_lang_code)
    
    target_lang_name = LANGUAGES[target_lang_code]['language_name']
    wait_msg = _('search_wait_specific', user_lang_code).format(target_lang_name=target_lang_name)

    async with db_pool.acquire() as connection:
        async with connection.transaction():
            
            # 1. محاولة إيجاد شريك يفضل اللغة المطلوبة
            partner_id = await connection.fetchval(
                f"""
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
                """, user_id, target_lang_code
            )
            
            if partner_id:
                # 2. مطابقة ناجحة
                await connection.execute("INSERT INTO active_chats (user_id, partner_id) VALUES ($1, $2), ($2, $1)", user_id, partner_id)
                logger.info(f"Match found! {user_id} <-> {partner_id}. Lang: {user_lang_code} <-> {target_lang_code}")
                
                partner_lang = await get_user_language(partner_id)
                await context.bot.send_message(chat_id=user_id, text=_('partner_found', user_lang_code), reply_markup=keyboard, protect_content=True)
                await context.bot.send_message(chat_id=partner_id, text=_('partner_found', partner_lang), reply_markup=await get_keyboard(partner_lang), protect_content=True)
                
                await update.callback_query.edit_message_text(f"✅ تم العثور على شريك يتحدث {target_lang_name}!", reply_markup=None)
            else:
                # 3. عدم وجود شريك، يتم وضعه في قائمة الانتظار بالتفضيل الجديد
                await connection.execute(
                    "INSERT INTO waiting_queue (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", 
                    user_id
                )
                await update.callback_query.edit_message_text(wait_msg, reply_markup=None)
                logger.info(f"User {user_id} added to DB queue. Target Lang: {target_lang_code}")


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
                         f"**Reporter User ID (Blocker):** `{user_id}`\n"
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
                WHERE target_language = $2 -- يجب ان يتطابق target_language مع لغة المرسل
                RETURNING user_id
                """, user_id, current_user_lang
            )
            
            if partner_id_new:
                await connection.execute("INSERT INTO active_chats (user_id, partner_id) VALUES ($1, $2), ($2, $1)", user_id, partner_id_new)
                logger.info(f"Match found! {user_id} <-> {partner_id_new}. Lang: {current_user_lang}")
                
                partner_lang = await get_user_language(partner_id_new)
                await context.bot.send_message(chat_id=user_id, text=_('partner_found', lang_code), reply_markup=keyboard, protect_content=True)
                await context.bot.send_message(chat_id=partner_id_new, text=_('partner_found', partner_lang), reply_markup=await get_keyboard(partner_lang), protect_content=True)
                
                await update.callback_query.edit_message_text(f"✅ تم العثور على شريك يتحدث {target_lang_name}!", reply_markup=None)
            else:
                await connection.execute("INSERT INTO waiting_queue (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                await update.message.reply_text(_('search_wait', lang_code), protect_content=True)
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
                         f"**Reporter User ID (Blocker):** `{user_id}`\n"
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
                await update.message.reply_text(_('search_wait', lang_code), protect_content=True)
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
                         f"**Reporter User ID (Blocker):** `{user_id}`\n"
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
                await update.message.reply_text(_('search_wait', lang_code), protect_content=True)
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
                         f"**Reporter User ID (Blocker):** `{user_id}`\n"
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
                await update.message.reply_text(_('search_wait', lang_code), protect_content=True)
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
                         f"**Reporter User ID (Blocker):** `{user_id}`\n"
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
                await update.message.reply_text(_('search_wait', lang_code), protect_content=True)
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
                         f"**Reporter User ID (Blocker):** `{user_id}`\n"
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
                await update.message.reply_text(_('search_wait', lang_code), protect_content=True)
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
                         f"**Reporter User ID (Blocker):** `{user_id}`\n"
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
                await update.message.reply_text(_('search_wait', lang_code), protect_content=True)
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
                         f"**Reporter User ID (Blocker):** `{user_id}`\n"
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
                WHERE target_language = $2 -- يجب ان يتطابق target_language مع لغة المرسل
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
                await update.message.reply_text(_('search_wait', lang_code), protect_content=True)
                logger.info(f"User {user_id} added/remains in DB queue (via /next). Lang: {current_user_lang}")

# --- (9) Main Run Function ---

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

    # --- [مستجيبات الأدمن] ---
    
    # فلتر مشترك للأدمن وفلتر الكابشن
    admin_filter = filters.User(user_id=ADMIN_ID)
    caption_filter = filters.CaptionRegex(r'^/broadcast')

    # 1. مستجيب لأمر البث النصي
    application.add_handler(CommandHandler("broadcast", broadcast_command, filters=admin_filter), group=1)
    
    # 2. مستجيب لبث الصور
    application.add_handler(MessageHandler(
        admin_filter & filters.PHOTO & caption_filter,
        broadcast_command
    ), group=1)
    
    # 3. مستجيب لبث الفيديو
    application.add_handler(MessageHandler(
        admin_filter & filters.VIDEO & caption_filter,
        broadcast_command
    ), group=1)

    # 4. مستجيب لبث المستندات
    application.add_handler(MessageHandler(
        admin_filter & filters.ATTACHMENT & caption_filter, 
        broadcast_command
    ), group=1)

    # 5. بقية أوامر الأدمن
    application.add_handler(CommandHandler("sendid", sendid_command, filters=admin_filter), group=1) 
    application.add_handler(CommandHandler("banuser", banuser_command, filters=admin_filter), group=1)
    # -----------------------------------
    
    # مستجيبات الأزرار (CallbackQuery)
    application.add_handler(CallbackQueryHandler(handle_join_check, pattern=r"^check_join_"), group=2)
    application.add_handler(CallbackQueryHandler(handle_block_confirmation, pattern=r"^confirm_block_|^cancel_block_"), group=2)
    application.add_handler(CallbackQueryHandler(handle_language_selection, pattern=r"^set_lang_|initial_set_lang_"), group=2) 
    application.add_handler(CallbackQueryHandler(handle_target_lang_selection, pattern=r"^match_lang_|^select_target_lang_|cancel_operation"), group=4)
    
    # مستجيبات الأوامر (CommandHandler)
    application.add_handler(CommandHandler("start", start_command), group=3) 
    application.add_handler(CommandHandler("search", show_search_options), group=3) # يعرض خيارات البحث المتقدم
    application.add_handler(CommandHandler("end", end_command), group=3)
    application.add_handler(CommandHandler("next", next_command), group=3)
    application.add_handler(CommandHandler("settings", settings_command), group=3)
    
    # مستجيبات أزرار لوحة المفاتيح (MessageHandler)
    search_texts = [lang['search_btn'] for lang in LANGUAGES.values()]
    stop_texts = [lang['stop_btn'] for lang in LANGUAGES.values()]
    next_texts = [lang['next_btn'] for lang in LANGUAGES.values()]
    block_texts = [lang['block_btn'] for lang in LANGUAGES.values()]

    # توجيه زر البحث إلى دالة عرض الخيارات
    application.add_handler(MessageHandler(filters.Text(search_texts), show_search_options), group=4) 
    application.add_handler(MessageHandler(filters.Text(stop_texts), end_command), group=4)   
    application.add_handler(MessageHandler(filters.Text(next_texts), next_command), group=4)   
    application.add_handler(MessageHandler(filters.Text(block_texts), block_user_command), group=4) 
    
    all_button_texts = search_texts + stop_texts + next_texts + block_texts
    
    # المستجيب العام لأي رسالة ليست أمر وليست زر (يشمل كل أنواع الرسائل لغرض الأرشفة الشاملة)
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.Text(all_button_texts),
        relay_and_log_message
    ), group=5)

    logger.info("Bot setup complete. Starting polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
