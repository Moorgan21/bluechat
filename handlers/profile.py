"""
هندلرهای بخش پروفایل: نمایش، ویرایش نام/بیو/جنسیت/سن/عکس.
از conversation state ساده (context.user_data) برای گرفتن ورودی متنی
بعد از فشردن دکمه‌ی ویرایش استفاده می‌شه.

عکس پروفایل قبل از ذخیره‌شدن با moderation.check_image_safety (Gemini
Vision) بررسی می‌شه؛ فقط عکس‌های تاییدشده ذخیره می‌شن.
"""

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from db import Gender, async_session, clear_photo_file_id, get_or_create_user, grant_referral_bonus
from keyboards import cancel_keyboard, city_keyboard, gender_selection_keyboard, profile_inline_keyboard, province_keyboard
from moderation import check_image_safety

logger = logging.getLogger(__name__)

AWAITING_FIELD_KEY = "awaiting_profile_field"  # "name" | "bio" | "age" | "photo"

GENDER_LABELS = {
    Gender.male: "👨 مرد",
    Gender.female: "👩 زن",
    Gender.unset: "تنظیم‌نشده",
}

CATEGORY_LABELS_FA = {
    "sexual": "محتوای جنسی/برهنه",
    "violence": "خشونت گرافیکی",
    "csam": "محتوای مرتبط با سوءاستفاده از کودکان",
    "hate": "نمادهای نفرت‌پراکنی",
    "other": "نامشخص",
}


def is_profile_complete(user) -> bool:
    """حداقل‌های لازم برای اینکه کاربر بتونه وارد چت بشه: نام نمایشی،
    جنسیت، و سن. عکس پروفایل اختیاریه (چون بررسی moderation ممکنه طول
    بکشه و نباید مانع ورود کاربر به چت بشه)."""
    return bool(user.display_name) and user.gender != Gender.unset and user.age is not None


def _format_profile_text(user) -> str:
    location_line = ""
    if user.province or user.city:
        parts = [p for p in (user.province, user.city) if p]
        location_line = f"📍 موقعیت: {' — '.join(parts)}\n"
    return (
        f"👤 پروفایل شما\n\n"
        f"نام نمایشی: {user.display_name or 'تنظیم‌نشده'}\n"
        f"بیوگرافی: {user.bio or '—'}\n"
        f"جنسیت: {GENDER_LABELS.get(user.gender, 'تنظیم‌نشده')}\n"
        f"سن: {user.age or '—'}\n"
        f"{location_line}"
        f"💰 سکه: {user.coins}\n"
        f"تعداد گفتگوها: {user.total_chats}\n"
        f"🔗 پروفایل عمومی: /user_{user.referral_code}"
    )


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_user = update.effective_user
    async with async_session() as session:
        user = await get_or_create_user(
            session, telegram_user.id, telegram_user.username, telegram_user.first_name
        )
        text = _format_profile_text(user)
        photo_file_id = user.photo_file_id

    if photo_file_id:
        if update.callback_query:
            await update.callback_query.answer()
            chat_id = update.callback_query.message.chat_id
        else:
            chat_id = update.message.chat_id
        try:
            await context.bot.send_photo(
                chat_id, photo_file_id, caption=text, reply_markup=profile_inline_keyboard()
            )
            return
        except Exception:
            # file_id نامعتبر شده — از DB پاک می‌کنیم و به کاربر خبر می‌دیم
            await clear_photo_file_id(telegram_user.id)
            text += "\n\n⚠️ عکس پروفایلت نامعتبر شده. از «🖼 عکس پروفایل» دوباره آپلود کن."

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=profile_inline_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=profile_inline_keyboard())


async def profile_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]

    if action == "edit_name":
        context.user_data[AWAITING_FIELD_KEY] = "name"
        await query.message.reply_text(
            "نام نمایشی جدیدت رو بفرست (حداکثر ۲۴ کاراکتر):",
            reply_markup=cancel_keyboard(),
        )
    elif action == "edit_bio":
        context.user_data[AWAITING_FIELD_KEY] = "bio"
        await query.message.reply_text(
            "بیوگرافی جدیدت رو بفرست (حداکثر ۱۵۰ کاراکتر):",
            reply_markup=cancel_keyboard(),
        )
    elif action == "edit_age":
        context.user_data[AWAITING_FIELD_KEY] = "age"
        await query.message.reply_text(
            "سنت رو به‌صورت عدد بفرست:",
            reply_markup=cancel_keyboard(),
        )
    elif action == "edit_gender":
        await query.message.reply_text("جنسیتت رو انتخاب کن:", reply_markup=gender_selection_keyboard())
    elif action == "edit_photo":
        context.user_data[AWAITING_FIELD_KEY] = "photo"
        await query.message.reply_text(
            "یه عکس به‌عنوان عکس پروفایل بفرست.\n"
            "⏳ عکس قبل از ثبت به‌صورت خودکار بررسی می‌شه و اگه نامناسب تشخیص داده "
            "بشه، ذخیره نخواهد شد.",
            reply_markup=cancel_keyboard(),
        )
    elif action == "edit_province":
        context.user_data[AWAITING_FIELD_KEY] = "province"
        await query.message.reply_text("استانت رو انتخاب کن:", reply_markup=province_keyboard())
    elif action == "edit_city":
        from db import User
        async with async_session() as session:
            user = await session.get(User, query.from_user.id)
        if user and user.province:
            context.user_data[AWAITING_FIELD_KEY] = "city"
            context.user_data["city_province"] = user.province
            await query.message.reply_text(
                f"شهرت رو از استان {user.province} انتخاب کن:",
                reply_markup=city_keyboard(user.province),
            )
        else:
            await query.message.reply_text(
                "ابتدا استانت رو از «🗺 استان» انتخاب کن تا بتونی شهر رو هم انتخاب کنی."
            )


async def gender_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    gender_value = query.data.split(":", 1)[1]  # "male" | "female"
    telegram_user = query.from_user
    context.user_data.pop(AWAITING_FIELD_KEY, None)

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)
        user.gender = Gender.male if gender_value == "male" else Gender.female
        await session.commit()
        text = _format_profile_text(user)
        photo_file_id = user.photo_file_id

    caption = f"✅ به‌روزرسانی شد.\n\n{text}"
    if photo_file_id:
        try:
            await query.message.reply_photo(
                photo_file_id, caption=caption, reply_markup=profile_inline_keyboard()
            )
        except Exception:
            await clear_photo_file_id(telegram_user.id)
            await query.message.reply_text(
                caption + "\n\n⚠️ عکس پروفایلت نامعتبر شده. از «🖼 عکس پروفایل» دوباره آپلود کن.",
                reply_markup=profile_inline_keyboard(),
            )
    else:
        await query.message.reply_text(caption, reply_markup=profile_inline_keyboard())


async def _send_profile_updated(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    profile_text: str,
    photo_file_id: str | None,
    header: str = "✅ به‌روزرسانی شد.",
) -> None:
    """بعد از هر تغییر پروفایل، اگه عکس وجود داشت عکس+کپشن می‌فرسته،
    وگرنه متن. در صورت خرابی file_id، از DB پاک و fallback به متن می‌ده."""
    caption = f"{header}\n\n{profile_text}"
    user_id = update.effective_user.id
    msg = update.effective_message
    if photo_file_id:
        try:
            await msg.reply_photo(photo_file_id, caption=caption, reply_markup=profile_inline_keyboard())
            return
        except Exception:
            await clear_photo_file_id(user_id)
            caption += "\n\n⚠️ عکس پروفایلت نامعتبر شده. از «🖼 عکس پروفایل» دوباره آپلود کن."
    await msg.reply_text(caption, reply_markup=profile_inline_keyboard())


async def handle_profile_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه کاربر منتظر واردکردن یه فیلد متنی پروفایل بود، ورودیش رو پردازش می‌کنه.
    خروجی True یعنی این پیام مصرف شد و نباید توسط relay_message هم پردازش بشه."""
    awaiting = context.user_data.get(AWAITING_FIELD_KEY)
    if not awaiting or awaiting == "photo":
        return False  # ورودیِ عکس در handle_profile_photo_input پردازش می‌شه

    text = (update.message.text or "").strip()
    telegram_user = update.effective_user

    if awaiting in ("province", "city"):
        # استان و شهر با دکمه انتخاب می‌شن، نه تایپ
        if awaiting == "city":
            province = context.user_data.get("city_province", "")
            if province:
                await update.message.reply_text(
                    "شهرت رو با دکمه‌های بالا انتخاب کن:", reply_markup=city_keyboard(province)
                )
                return True
        await update.message.reply_text("استانت رو با دکمه‌های بالا انتخاب کن:", reply_markup=province_keyboard())
        return True

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)

        if awaiting == "name":
            if not text or len(text) > 24:
                await update.message.reply_text("نام نمایشی باید بین ۱ تا ۲۴ کاراکتر باشه. دوباره بفرست:")
                return True
            user.display_name = text
        elif awaiting == "bio":
            if len(text) > 150:
                await update.message.reply_text("بیوگرافی نباید بیشتر از ۱۵۰ کاراکتر باشه. دوباره بفرست:")
                return True
            user.bio = text
        elif awaiting == "age":
            if not text.isdigit() or not (10 <= int(text) <= 99):
                await update.message.reply_text("لطفاً یه سن معتبر (بین ۱۰ تا ۹۹) به‌صورت عدد بفرست:")
                return True
            user.age = int(text)
        elif awaiting == "city":
            if not text or len(text) > 50:
                await update.message.reply_text("نام شهر باید بین ۱ تا ۵۰ کاراکتر باشه. دوباره بفرست:")
                return True
            user.city = text

        await session.commit()
        result_text = _format_profile_text(user)
        photo_file_id = user.photo_file_id

    context.user_data.pop(AWAITING_FIELD_KEY, None)
    await _send_profile_updated(update, context, result_text, photo_file_id)
    return True


async def handle_profile_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه کاربر منتظر آپلود عکس پروفایل بود، عکس دریافتی رو با Gemini Vision
    بررسی و در صورت تایید ذخیره می‌کنه. خروجی True یعنی این پیام مصرف شد."""
    awaiting = context.user_data.get(AWAITING_FIELD_KEY)
    if awaiting != "photo":
        return False

    if not update.message.photo:
        await update.message.reply_text("لطفاً یه عکس (نه فایل یا چیز دیگه) بفرست، یا /cancel برای انصراف:")
        return True

    telegram_user = update.effective_user
    largest_photo = update.message.photo[-1]

    await update.message.reply_text("⏳ در حال بررسی عکس...")

    try:
        tg_file = await context.bot.get_file(largest_photo.file_id)
        image_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception:
        logger.exception("خطا در دانلود عکس از تلگرام برای بررسی moderation.")
        await update.message.reply_text("⚠️ مشکلی در دریافت عکس پیش اومد. دوباره امتحان کن.")
        return True

    result = await check_image_safety(image_bytes, mime_type="image/jpeg")

    if not result.safe:
        category_fa = CATEGORY_LABELS_FA.get(result.category, "نامشخص")
        await update.message.reply_text(
            "🚫 این عکس به‌عنوان عکس پروفایل قابل قبول نیست.\n"
            f"دلیل: {category_fa}"
            + (f" — {result.reason}" if result.reason else "")
            + "\n\nلطفاً یه عکس مناسب دیگه بفرست، یا /cancel برای انصراف:"
        )
        return True  # همچنان منتظر عکس جدید می‌مونیم

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)
        user.photo_file_id = largest_photo.file_id
        user.photo_approved_at = datetime.utcnow()
        await session.commit()
        result_text = _format_profile_text(user)

    context.user_data.pop(AWAITING_FIELD_KEY, None)
    # عکس جدید همینجاست — مستقیم ارسال می‌کنیم بدون نیاز به fallback
    try:
        await update.message.reply_photo(
            largest_photo.file_id,
            caption=f"✅ عکس پروفایل ثبت شد.\n\n{result_text}",
            reply_markup=profile_inline_keyboard(),
        )
    except Exception:
        await update.message.reply_text(
            f"✅ عکس پروفایل ثبت شد.\n\n{result_text}", reply_markup=profile_inline_keyboard()
        )
    return True


async def cancel_profile_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.pop(AWAITING_FIELD_KEY, None):
        await update.message.reply_text("ویرایش لغو شد.")
    else:
        await update.message.reply_text("چیزی برای لغو کردن نیست.")


# ---------------------------------------------------------------------------
# جریان تکمیل اجباری پروفایل (Onboarding) — قبل از اولین ورود به چت
# ---------------------------------------------------------------------------
AWAITING_ONBOARDING_KEY = "awaiting_onboarding_field"  # "name" | "age"


async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """جریان تکمیل اجباری پروفایل رو شروع می‌کنه: نام → جنسیت → سن."""
    from telegram import ReplyKeyboardRemove

    context.user_data[AWAITING_ONBOARDING_KEY] = "name"
    await update.effective_message.reply_text(
        "👋 قبل از شروع چت، باید یه پروفایل کوتاه بسازی (فقط چند ثانیه طول می‌کشه).\n\n"
        "۱️⃣ یه نام نمایشی برای خودت انتخاب کن (حداکثر ۲۴ کاراکتر). این اسم "
        "واقعیت نیست و فقط داخل ربات دیده می‌شه:",
        reply_markup=ReplyKeyboardRemove(),
    )


async def handle_onboarding_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """مرحله‌ی نام و سن رو در جریان onboarding پردازش می‌کنه. جنسیت با دکمه‌ی
    inline گرفته می‌شه (در onboarding_gender_callback). خروجی True یعنی
    پیام مصرف شد."""
    step = context.user_data.get(AWAITING_ONBOARDING_KEY)
    if not step:
        return False

    text = (update.message.text or "").strip()
    telegram_user = update.effective_user

    if step == "name":
        if not text or len(text) > 24:
            await update.message.reply_text("نام باید بین ۱ تا ۲۴ کاراکتر باشه. دوباره بفرست:")
            return True
        async with async_session() as session:
            user = await get_or_create_user(session, telegram_user.id)
            user.display_name = text
            await session.commit()

        context.user_data[AWAITING_ONBOARDING_KEY] = "gender"
        await update.message.reply_text(
            "۲️⃣ جنسیتت رو انتخاب کن:", reply_markup=gender_selection_keyboard()
        )
        return True

    if step == "gender":
        await update.message.reply_text(
            "لطفاً جنسیتت رو با دکمه‌های بالا انتخاب کن (نه با تایپ‌کردن):",
            reply_markup=gender_selection_keyboard(),
        )
        return True

    if step == "age":
        if not text.isdigit() or not (10 <= int(text) <= 99):
            await update.message.reply_text("لطفاً یه سن معتبر (بین ۱۰ تا ۹۹) به‌صورت عدد بفرست:")
            return True
        async with async_session() as session:
            user = await get_or_create_user(session, telegram_user.id)
            user.age = int(text)
            await session.commit()

        context.user_data[AWAITING_ONBOARDING_KEY] = "province"
        await update.message.reply_text(
            "۴️⃣ استانت رو انتخاب کن:", reply_markup=province_keyboard()
        )
        return True

    if step == "province":
        await update.message.reply_text(
            "لطفاً استانت رو با دکمه‌های بالا انتخاب کن:",
            reply_markup=province_keyboard(),
        )
        return True

    if step == "city":
        # شهر باید از کیبورد انتخاب بشه — اگه تایپ کرد، keyboard رو دوباره نشون می‌دیم
        province = context.user_data.get("city_province", "")
        if province:
            await update.message.reply_text(
                "شهرت رو از لیست انتخاب کن:", reply_markup=city_keyboard(province)
            )
        else:
            await update.message.reply_text(
                "لطفاً استانت رو با دکمه‌های بالا انتخاب کن:", reply_markup=province_keyboard()
            )
        return True

    return False


async def onboarding_gender_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه کاربر در مرحله‌ی onboarding منتظر انتخاب جنسیته، این callback رو
    پردازش می‌کنه و به مرحله‌ی سن می‌ره. خروجی True یعنی مصرف شد."""
    if context.user_data.get(AWAITING_ONBOARDING_KEY) != "gender":
        return False

    query = update.callback_query
    await query.answer()
    gender_value = query.data.split(":", 1)[1]
    telegram_user = query.from_user

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)
        user.gender = Gender.male if gender_value == "male" else Gender.female
        await session.commit()

    context.user_data[AWAITING_ONBOARDING_KEY] = "age"
    await query.edit_message_text("✅ ثبت شد.")
    await query.message.reply_text("۳️⃣ سنت رو به‌صورت عدد بفرست:")
    return True


async def onboarding_province_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه کاربر در مرحله‌ی انتخاب استان onboarding هست، این callback رو
    پردازش می‌کنه و به مرحله‌ی شهر می‌ره. خروجی True یعنی مصرف شد."""
    if context.user_data.get(AWAITING_ONBOARDING_KEY) != "province":
        return False

    query = update.callback_query
    await query.answer()
    province = query.data.split(":", 1)[1]
    telegram_user = query.from_user

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)
        user.province = province
        await session.commit()

    context.user_data[AWAITING_ONBOARDING_KEY] = "city"
    context.user_data["city_province"] = province
    await query.edit_message_text(f"✅ استان {province} ثبت شد.")
    await query.message.reply_text(
        f"۵️⃣ شهرت رو از استان {province} انتخاب کن:",
        reply_markup=city_keyboard(province),
    )
    return True


async def edit_province_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه کاربر در حال ویرایش استانِ پروفایل هست، استان رو ذخیره می‌کنه
    و بلافاصله کیبورد شهرهای همون استان رو نشون می‌ده."""
    if context.user_data.get(AWAITING_FIELD_KEY) != "province":
        return False

    query = update.callback_query
    await query.answer()
    province = query.data.split(":", 1)[1]
    telegram_user = query.from_user

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)
        user.province = province
        user.city = None  # استان عوض شده، شهر قبلی نامعتبره
        await session.commit()

    context.user_data[AWAITING_FIELD_KEY] = "city"
    context.user_data["city_province"] = province
    try:
        await query.edit_message_text(f"✅ استان {province} ثبت شد.")
    except Exception:
        pass
    await query.message.reply_text(
        f"حالا شهرت رو از استان {province} انتخاب کن:",
        reply_markup=city_keyboard(province),
    )
    return True


async def handle_city_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """هندلر انتخاب شهر از کیبورد — هم onboarding هم ویرایش پروفایل."""
    query = update.callback_query
    await query.answer()
    city = query.data.split(":", 1)[1]
    telegram_user = query.from_user

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)
        user.city = city
        await session.commit()
        result_text = _format_profile_text(user)
        photo_file_id = user.photo_file_id

    context.user_data.pop("city_province", None)

    if context.user_data.get(AWAITING_ONBOARDING_KEY) == "city":
        context.user_data.pop(AWAITING_ONBOARDING_KEY, None)
        try:
            await query.edit_message_text(f"✅ شهر {city} ثبت شد.")
        except Exception:
            pass
        await _finish_onboarding(update, context)
        return True

    if context.user_data.get(AWAITING_FIELD_KEY) == "city":
        context.user_data.pop(AWAITING_FIELD_KEY, None)
        try:
            await query.edit_message_text(f"✅ شهر {city} ثبت شد.")
        except Exception:
            pass
        await _send_profile_updated(update, context, result_text, photo_file_id, header=f"✅ شهر {city} ثبت شد.")
        return True

    return False


async def handle_city_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌های صفحه‌بندی کیبورد شهر."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)  # citypg:province:page
    if len(parts) != 3:
        return
    _, province, page_str = parts
    try:
        page = int(page_str)
    except ValueError:
        return
    try:
        await query.edit_message_reply_markup(reply_markup=city_keyboard(province, page))
    except Exception:
        pass


async def _finish_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from keyboards import main_reply_keyboard
    from telegram.error import TelegramError

    telegram_user = update.effective_user
    async with async_session() as session:
        user = await get_or_create_user(session, telegram_user.id)
        invited_by = user.invited_by

    if invited_by:
        try:
            new_balance = await grant_referral_bonus(invited_by, telegram_user.id)
            if new_balance is not None:
                await context.bot.send_message(
                    invited_by,
                    f"🎉 کاربری که دعوت کردی پروفایلش رو کامل کرد!\n"
                    f"۵ سکه به حسابت اضافه شد. موجودی جدید: {new_balance} 💰",
                )
        except Exception:
            pass

    pending_code = context.user_data.pop("pending_direct_link_code", None)

    if pending_code:
        # این حالت از یه لینک ناشناسِ مستقیم اومده (کاربر می‌خواد برای
        # یه فرد مشخص پیام ناشناس بفرسته)، پس بعد از تکمیل پروفایل
        # مستقیم وارد state نوشتنِ پیام می‌شه.
        await update.effective_message.reply_text("🎉 پروفایلت کامل شد!")
        from main import _handle_direct_link

        await _handle_direct_link(update, context, pending_code)
        return

    # حالت عادی: پروفایل کامل شد، فقط منو رو نشون بده و بذار خودِ کاربر
    # با زدنِ دکمه‌ی «وصل کن به یه ناشناس!» تصمیم بگیره کی وارد چت بشه.
    await update.effective_message.reply_text(
        "🎉 پروفایلت کامل شد! هر وقت خواستی، از منوی پایین «وصل کن به یه ناشناس!» رو بزن.",
        reply_markup=main_reply_keyboard(),
    )
