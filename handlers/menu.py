"""
هندلرهای راهنما و بازگشت به منوی اصلی.
"""

from telegram import Update
from telegram.ext import ContextTypes

from keyboards import main_reply_keyboard

HELP_TEXT = (
    "🤔 راهنمای ربات چت ناشناس بلو چت\n\n"
    "💬 وصل کن به یه ناشناس! — انتخاب جنسیت مطلوب و جفت‌شدن با یک کاربر آنلاین\n"
    "💬 جستجوی کاربران — جفت‌شدن هدفمند با فیلتر جنسیت/سن\n"
    "📍 افراد نزدیک — پیدا کردن کاربران نزدیک به موقعیت مکانی‌ت\n"
    "💰 سکه — مشاهده و کسب سکه از طریق دعوت دوستان\n"
    "👤 پروفایل — ویرایش نام نمایشی، بیوگرافی، جنسیت، سن و تنظیماتِ واکنش\n"
    "🔗 معرفی به دوستان — لینک دعوت با پاداش سکه\n"
    "🥷 لینک ناشناس من — لینک اختصاصی برای دریافت پیامِ ناشناسِ نوتیفی\n\n"
    "پروفایلِ عمومی:\n"
    "با فرستادنِ /user_شناسه (شناسه‌ی هر کاربر) می‌تونی پروفایلش رو ببینی، بدونِ "
    "اینکه توی چتِ فعال باهاش باشی. زیرِ پروفایل می‌تونی گزارش بدی، درخواستِ چت "
    "بفرستی، بلاکش کنی، یا (اگه فعال کرده باشه) بهش واکنش بفرستی.\n\n"
    "دستورات:\n"
    "/start — شروع یا بازگشت به منو\n"
    "/next — عوض‌کردن همراه فعلی\n"
    "/stop — پایان گفتگو\n"
    "/silent — فعال/غیرفعال‌کردنِ حالتِ سایلنت (جلوگیری از دریافتِ درخواستِ چت)\n\n"
    "⚠️ هویت شما در این ربات کاملاً ناشناس می‌مونه. لطفاً اطلاعات شخصی حساس "
    "(شماره تلفن، آدرس، و غیره) رو با کاربران ناشناس به اشتراک نذارید."
)


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(HELP_TEXT)
    else:
        await update.message.reply_text(HELP_TEXT, reply_markup=main_reply_keyboard())


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("به منوی اصلی برگشتی 👇", reply_markup=main_reply_keyboard())
    try:
        await query.delete_message()
    except Exception:
        pass
