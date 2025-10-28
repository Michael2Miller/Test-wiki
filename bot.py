import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- الإعدادات ---
TELEGRAM_TOKEN = os.environ.get('BOT_TOKEN')

# --- (هام) إدارة الحالة ---
# سنستخدم متغيرات عامة بسيطة لتخزين البيانات (لغرض التعلم)
# ملاحظة: هذه البيانات ستُفقد عند إعادة تشغيل البوت على Railway
# الطريقة الاحترافية تتطلب قاعدة بيانات (مثل Redis أو Postgres)

# 1. قاموس لتخزين المحادثات النشطة
# الصيغة: { user_id_A: user_id_B, user_id_B: user_id_A }
active_chats = {}

# 2. قائمة لتخزين المستخدمين الذين ينتظرون شريكاً
waiting_queue = []

# --- دوال البوت ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يرسل رسالة ترحيبية عند إرسال أمر /start"""
    user_id = update.message.from_user.id
    
    # التحقق إذا كان المستخدم في محادثة أو انتظار
    if user_id in active_chats:
        await update.message.reply_text("أنت حالياً في محادثة. أرسل /end لإنهائها.")
    elif user_id in waiting_queue:
        await update.message.reply_text("أنت حالياً في قائمة الانتظار. أرسل /end لإلغاء البحث.")
    else:
        await update.message.reply_text(
            "أهلاً بك في بوت المحادثة العشوائية! 🕵️‍♂️\n\n"
            "اضغط /search للبحث عن شريك وبدء محادثة مجهولة.\n"
            "اضغط /end لإنهاء المحادثة أو إلغاء البحث في أي وقت."
        )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبدأ البحث عن شريك محادثة"""
    user_id = update.message.from_user.id

    if user_id in active_chats:
        await update.message.reply_text("أنت بالفعل في محادثة! أرسل /end أولاً لإنهائها.")
        return
        
    if user_id in waiting_queue:
        await update.message.reply_text("أنت تبحث بالفعل. نرجو الانتظار...")
        return

    # التحقق من قائمة الانتظار
    if not waiting_queue:
        # لا أحد ينتظر، أضف هذا المستخدم إلى القائمة
        waiting_queue.append(user_id)
        await update.message.reply_text("🔎 يتم البحث عن شريك... نرجو الانتظار.")
        print(f"User {user_id} added to queue. Queue: {waiting_queue}")
    else:
        # وجدنا شريكاً!
        partner_id = waiting_queue.pop(0) # أخذ أول شخص من القائمة
        
        # تسجيل المحادثة النشطة لكلا الطرفين
        active_chats[user_id] = partner_id
        active_chats[partner_id] = user_id

        print(f"Match found! {user_id} <-> {partner_id}. Active chats: {active_chats}")

        # إرسال رسائل لكلا المستخدمين
        await context.bot.send_message(chat_id=user_id, text="✅ تم العثور على شريك! المحادثة بدأت. (هويتك مجهولة).")
        await context.bot.send_message(chat_id=partner_id, text="✅ تم العثور على شريك! المحادثة بدأت. (هويتك مجهولة).")

async def end_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ينهي المحادثة النشطة أو يلغي البحث"""
    user_id = update.message.from_user.id

    if user_id in active_chats:
        # المستخدم في محادثة نشطة
        partner_id = active_chats.pop(user_id) # حذف المستخدم
        if partner_id in active_chats: # التأكد من حذف الشريك أيضاً
            active_chats.pop(partner_id)
        
        print(f"Chat ended by {user_id}. Partner was {partner_id}.")

        # إبلاغ الطرفين
        await context.bot.send_message(chat_id=user_id, text="🔚 لقد أنهيت المحادثة.")
        await context.bot.send_message(chat_id=partner_id, text="⚠️ غادر شريكك المحادثة.")

    elif user_id in waiting_queue:
        # المستخدم كان ينتظر
        waiting_queue.remove(user_id)
        print(f"User {user_id} cancelled search.")
        await update.message.reply_text("تم إلغاء البحث.")
    else:
        # المستخدم ليس في محادثة أو انتظار
        await update.message.reply_text("أنت لست في محادثة أو عملية بحث حالياً.")


async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يمرر الرسائل النصية بين الشريكين"""
    user_id = update.message.from_user.id

    if user_id not in active_chats:
        await update.message.reply_text("أنت لست في محادثة. اضغط /search للبدء.")
        return

    # العثور على الشريك وتمرير الرسالة
    partner_id = active_chats[user_id]
    
    # (مهم) تمرير الرسالة
    # نحن نمرر أنواع مختلفة من الرسائل (نص، صور، ملصقات)
    
    if update.message.text:
        await context.bot.send_message(chat_id=partner_id, text=update.message.text)
    elif update.message.sticker:
        await context.bot.send_sticker(chat_id=partner_id, sticker=update.message.sticker.file_id)
    elif update.message.photo:
        # إرسال آخر صورة (أعلى جودة)
        await context.bot.send_photo(chat_id=partner_id, photo=update.message.photo[-1].file_id)
    elif update.message.voice:
        await context.bot.send_voice(chat_id=partner_id, voice=update.message.voice.file_id)
    else:
        await context.bot.send_message(chat_id=user_id, text="عذراً، لا يمكن إرسال هذا النوع من الرسائل (مثل الفيديو أو الملفات).")


# --- دالة التشغيل الرئيسية ---

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    if not TELEGRAM_TOKEN:
        print("لا يمكن تشغيل البوت بدون توكن.")
        return

    print("البوت العشوائي قيد التشغيل...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # إضافة المعالجات (Handlers)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("end", end_command))
    
    # هذا المعالج يرد على أي رسالة (نص، صورة، ملصق..) *ليست* أمراً
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, relay_message))

    # تشغيل البوت
    application.run_polling()

if __name__ == "__main__":
    main()
