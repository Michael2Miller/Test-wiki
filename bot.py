import os
import wikipediaapi
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- الإعدادات ---

# 1. إعداد ويكيبيديا (يفضل تحديد لغة و User-Agent)
# سنستخدم ويكيبيديا العربية كمثال
wiki_wiki = wikipediaapi.Wikipedia(
    language='ar',  # للبحث في ويكيبيديا العربية
    user_agent='MyWikipediaBot/1.0 (example@example.com)' # مطلوب حسب شروط ويكيبيديا
)

# 2. الحصول على توكن تليجرام (هام جداً للأمان)
# سنقرأه من متغيرات البيئة (لنضعه لاحقاً في Railway)
TELEGRAM_TOKEN = os.environ.get('BOT_TOKEN')
if not TELEGRAM_TOKEN:
    print("خطأ: لم يتم العثور على متغير BOT_TOKEN")
    # يمكنك وضع التوكن هنا مباشرة *فقط أثناء التجربة المحلية*
    # TELEGRAM_TOKEN = "التوكن_السري_الخاص_بك" 

# --- دوال البوت ---

# دالة التعامل مع أمر /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يرسل رسالة ترحيبية عند إرسال أمر /start"""
    await update.message.reply_text(
        "أهلاً بك! 👋\n"
        "أرسل لي أي مصطلح (مثل 'الأهرامات') وسأبحث عنه في ويكيبيديا وأرسل لك الملخص."
    )

# دالة البحث في ويكيبيديا (الدالة الأهم)
async def search_wikipedia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبحث عن النص المستلم في ويكيبيديا ويرجع الملخص"""
    search_term = update.message.text
    chat_id = update.message.chat_id
    
    await context.bot.send_chat_action(chat_id=chat_id, action="TYPING") # لإظهار "يكتب الآن..."

    print(f"يتم البحث عن: {search_term}")

    # البحث عن الصفحة
    page = wiki_wiki.page(search_term)

    if not page.exists():
        await update.message.reply_text(f"عذراً، لم أجد أي نتائج لكلمة '{search_term}' في ويكيبيديا العربية.")
        return

    # إعداد الرد
    # page.summary يحتوي على الملخص
    # لنأخذ أول 500 حرف لضمان عدم طول الرسالة
    summary = page.summary[0:500] 
    
    response_text = f"📖 **{page.title}**\n\n"
    response_text += f"{summary}...\n\n"
    response_text += f"[اقرأ المزيد على ويكيبيديا]({page.fullurl})"
    
    # إرسال الرد
    await update.message.reply_text(
        response_text,
        parse_mode='Markdown', # للسماح بالروابط والنص العريض
        disable_web_page_preview=True # لمنع ظهور معاينة الرابط الكبيرة
    )

# --- دالة التشغيل الرئيسية ---

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    if not TELEGRAM_TOKEN:
        print("لا يمكن تشغيل البوت بدون توكن.")
        return

    print("البوت قيد التشغيل...")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # إضافة المعالجات (Handlers)
    application.add_handler(CommandHandler("start", start_command))
    
    # هذا المعالج يرد على أي رسالة نصية *ليست* أمراً (مثل /start)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_wikipedia))

    # تشغيل البوت (Polling)
    application.run_polling()

if __name__ == "__main__":
    main()
