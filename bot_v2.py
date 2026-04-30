import asyncio
import logging
import os
import re
import sys
from html import escape
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

import bot_db
import linked

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
VENERA_LINK = os.getenv("GULCHACHAK_LINK", "https://t.me/Venera25Naz")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "")

# [ДАННЫЕ ВЕНЕРЫ] — уточнить реферальную ссылку у Венеры
REG_LINK = "https://faberlic.com/register?sponsornumber=739945401&lang=ru&r=1000034210371"

MEDIA_DIR = Path(__file__).parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

SPEND_SAVING = {
    "low":  (2000,  460,   5520),
    "mid":  (3500,  800,   9600),
    "high": (6000,  1380,  16560),
}

SOURCE_LABELS = {
    "rsy":    "📡 РСЯ (Яндекс)",
    "yandex": "📡 Яндекс.Директ",
    "direct": "🌐 Прямой вход",
}


def label_source(src):
    return SOURCE_LABELS.get((src or "").lower(), f"🌐 {src or 'прямой вход'}")


class Form(StatesGroup):
    quiz_spend = State()
    waiting_for_name = State()
    waiting_for_phone = State()


# ─── ХЕЛПЕРЫ ────────────────────────────────────────────────────────────────

async def typing(chat_id, sec=1.5):
    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(sec)
    except Exception:
        pass


async def send_photo_if_exists(chat_id, filename, caption=None):
    p = MEDIA_DIR / filename
    if not p.exists():
        return False
    try:
        await bot.send_photo(chat_id, types.FSInputFile(p), caption=caption)
        return True
    except Exception as e:
        log.warning(f"photo {filename}: {e}")
        return False


async def send_video_note_if_exists(chat_id, filename):
    p = MEDIA_DIR / filename
    if not p.exists():
        return False
    try:
        await bot.send_video_note(chat_id, types.FSInputFile(p))
        return True
    except Exception as e:
        log.warning(f"video_note {filename}: {e}")
        return False


def reg_kb():
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛍 Хочу скидку — регистрируюсь", url=REG_LINK))
    b.row(types.InlineKeyboardButton(text="💬 Есть вопросы перед регистрацией", callback_data="faq"))
    b.row(types.InlineKeyboardButton(text="🙋 Помоги мне зарегистрироваться", callback_data="need_help"))
    return b.as_markup()


def after_reg_kb():
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Зарегистрировался(ась)!", callback_data="reg_done"))
    b.row(types.InlineKeyboardButton(text="😕 Не получилось, нужна помощь", callback_data="reg_failed"))
    b.row(types.InlineKeyboardButton(text="🔁 Попробую ещё раз", url=REG_LINK))
    return b.as_markup()


# ─── ШАГ 1: /start ──────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args = message.text.split(maxsplit=1)
    source = args[1].strip() if len(args) > 1 else "direct"
    await state.update_data(source=source)

    bot_db.upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        source,
    )
    bot_db.update_stage(message.from_user.id, "started")
    bot_db.log_event(message.from_user.id, "start", source)

    # [ФОТО ВЕНЕРЫ] — нужен файл venera.jpg в папке media/
    # Пока показываем текстовое приветствие
    if not await send_video_note_if_exists(message.chat.id, "venera_intro.mp4"):
        sent = await send_photo_if_exists(
            message.chat.id, "venera.jpg",
            caption=(
                "Привет! Я <b>Венера</b> 💎\n\n"
                "<i>[🎥 Здесь будет короткое видео — "
                "15 секунд, кто я и как помогаю экономить на косметике и химии]</i>"
            )
        )
        if not sent:
            await message.answer(
                "Привет! Я <b>Венера</b>, консультант Faberlic 💎\n\n"
                "<i>[🎥 Здесь будет видео-знакомство от Венеры]</i>"
            )

    await typing(message.chat.id, 2.0)

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="Да, хочу узнать →", callback_data="hook_yes"))
    b.row(types.InlineKeyboardButton(text="Звучит как реклама 🤨", callback_data="hook_doubt"))

    await message.answer(
        "Ты регулярно тратишь деньги на стиральный порошок, шампунь, "
        "крем и другую косметику?\n\n"
        "А что если я скажу, что <b>те же товары</b> можно покупать "
        "<b>на 20–26% дешевле</b> — легально, без подводных камней?\n\n"
        "Давай посчитаем на твоих цифрах 👇",
        reply_markup=b.as_markup()
    )


# ─── СОМНЕНИЕ ────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "hook_doubt")
async def hook_doubt(cb: types.CallbackQuery):
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await typing(cb.message.chat.id, 1.5)

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="Окей, давай посчитаем →", callback_data="hook_yes"))
    b.row(types.InlineKeyboardButton(text="Спасибо, не интересно", callback_data="not_now"))

    await bot.send_message(
        cb.message.chat.id,
        "Понимаю — в интернете много сомнительных предложений 🙂\n\n"
        "<b>Faberlic</b> существует с 1997 года, это российский производитель. "
        "Никаких вступительных взносов, никаких обязательных закупок.\n\n"
        "Ты просто регистрируешься один раз как постоянный покупатель "
        "и получаешь постоянную скидку на весь каталог.\n\n"
        "Давай просто проверим на твоих цифрах — сколько ты можешь сэкономить 👇",
        reply_markup=b.as_markup()
    )


# ─── ШАГ 2: КВИЗ ─────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "hook_yes")
async def quiz_spend(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    bot_db.update_stage(cb.from_user.id, "quiz_started")
    try:
        await cb.message.delete()
    except Exception:
        pass
    await typing(cb.message.chat.id, 1.2)

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="меньше 2 000 ₽", callback_data="spend_low"))
    b.row(types.InlineKeyboardButton(text="от 2 000 до 5 000 ₽", callback_data="spend_mid"))
    b.row(types.InlineKeyboardButton(text="больше 5 000 ₽", callback_data="spend_high"))

    await bot.send_message(
        cb.message.chat.id,
        "Сколько в среднем уходит в месяц на <b>бытовую химию и уходовую косметику</b>?\n\n"
        "<i>Порошок, гель для душа, шампунь, крем, зубная паста, средство для посуды — всё вместе</i>",
        reply_markup=b.as_markup()
    )
    await state.set_state(Form.quiz_spend)


# ─── ШАГ 3: РЕЗУЛЬТАТ + РЕГИСТРАЦИЯ ─────────────────────────────────────────

@dp.callback_query(Form.quiz_spend, F.data.startswith("spend_"))
async def quiz_result(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    tier = cb.data.replace("spend_", "")
    spend, save_month, save_year = SPEND_SAVING[tier]
    await state.update_data(spend_tier=tier, save_month=save_month, save_year=save_year)
    bot_db.log_event(cb.from_user.id, "quiz_spend", tier)
    await state.set_state(None)

    try:
        await cb.message.delete()
    except Exception:
        pass

    await typing(cb.message.chat.id, 2.0)
    await bot.send_message(
        cb.message.chat.id,
        f"<b>Вот что ты переплачиваешь прямо сейчас:</b>\n\n"
        f"📉 <b>~{save_month:,} ₽ каждый месяц</b>\n"
        f"📉 <b>~{save_year:,} ₽ каждый год</b>\n\n"
        f"Это реальные деньги — просто потому что покупаешь не напрямую."
    )

    await typing(cb.message.chat.id, 2.0)
    await bot.send_message(
        cb.message.chat.id,
        "❌ <i>«Придётся делать обязательные заказы каждый месяц»</i>\n"
        "✅ Нет. Покупаешь только когда нужно и только то, что нужно.\n\n"
        "❌ <i>«Регистрация стоит денег»</i>\n"
        "✅ Нет. Полностью бесплатно.\n\n"
        "❌ <i>«Это МЛМ — надо продавать и приглашать»</i>\n"
        "✅ Нет. Просто карта постоянного покупателя со скидкой 20–26% — "
        "как в магазине, только честнее."
    )

    await typing(cb.message.chat.id, 2.0)
    bot_db.update_stage(cb.from_user.id, "showed_link")
    bot_db.mark_link_shown(cb.from_user.id)

    await bot.send_message(
        cb.message.chat.id,
        f"Регистрация займёт <b>буквально 2 минуты</b> 💎\n\n"
        f"Нажми кнопку — откроется сайт Faberlic. "
        f"Введи имя, email и телефон. Скидка активируется сразу после подтверждения email.",
        reply_markup=reg_kb()
    )


# ─── FAQ ─────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "faq")
async def faq(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "faq")

    await typing(cb.message.chat.id, 1.5)
    await bot.send_message(
        cb.message.chat.id,
        "<b>Отвечаю на частые вопросы 💎</b>\n\n"

        "🔹 <b>Регистрация платная?</b>\n"
        "Нет, полностью бесплатно.\n\n"

        "🔹 <b>Нужно покупать каждый месяц?</b>\n"
        "Нет. Заказываешь когда удобно — скидка никуда не денется.\n\n"

        "🔹 <b>Будут звонить и уговаривать?</b>\n"
        "Нет. Только если сам обратишься к Венере за помощью.\n\n"

        "🔹 <b>Нужно кого-то приглашать?</b>\n"
        "Нет. Просто покупаешь для себя дешевле — и всё.\n\n"

        "🔹 <b>Как работает скидка?</b>\n"
        "После регистрации скидка 20–26% применяется ко всему каталогу автоматически.\n\n"

        "🔹 <b>Как зарегистрироваться?</b>\n"
        "1️⃣ Нажми «Хочу скидку — регистрируюсь»\n"
        "2️⃣ Введи имя, фамилию, email и телефон\n"
        "3️⃣ Придумай пароль\n"
        "4️⃣ Подтверди email (письмо придёт моментально)\n"
        "5️⃣ Готово — личный кабинет и скидка активны 🎉\n\n"
        "<i>Если что-то не получается — Венера поможет лично.</i>",
        reply_markup=reg_kb()
    )


# ─── НУЖНА ПОМОЩЬ → СБОР КОНТАКТА ───────────────────────────────────────────

@dp.callback_query(F.data.in_({"need_help", "reg_failed"}))
async def need_help(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    bot_db.update_stage(cb.from_user.id, "asked_name")
    bot_db.log_event(cb.from_user.id, "need_help")
    try:
        await cb.message.delete()
    except Exception:
        pass

    await typing(cb.message.chat.id, 1.0)
    await bot.send_message(
        cb.message.chat.id,
        "Без проблем, Венера поможет разобраться 💎\n\n"
        "Как тебя зовут?"
    )
    await state.set_state(Form.waiting_for_name)


@dp.message(Form.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()[:80]
    if len(re.findall(r"[а-яёa-z]", name.lower())) < 2:
        await message.answer("Напиши своё имя 🙂")
        return

    await state.update_data(user_name=name)
    bot_db.update_stage(message.from_user.id, "asked_phone", name=name)
    bot_db.log_event(message.from_user.id, "name_given", name)

    kb = ReplyKeyboardBuilder()
    kb.row(types.KeyboardButton(text="📱 Поделиться номером", request_contact=True))

    await typing(message.chat.id, 1.0)
    await message.answer(
        f"Рада познакомиться, {escape(name)} 💎\n\n"
        f"Оставь номер телефона — Венера свяжется "
        f"в течение пары часов и поможет разобраться со всеми вопросами.",
        reply_markup=kb.as_markup(resize_keyboard=True, one_time_keyboard=True),
    )
    await state.set_state(Form.waiting_for_phone)


@dp.message(Form.waiting_for_phone, F.contact)
@dp.message(Form.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number if message.contact else (message.text or "")
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        await message.answer(
            "Не похоже на номер 🤔\n"
            "Напиши в формате +7XXXXXXXXXX или нажми кнопку 📱"
        )
        return
    phone = "+" + digits

    data = await state.get_data()
    name = data.get("user_name", "—")
    source = data.get("source", "direct")
    tier = data.get("spend_tier", "—")
    save_year = data.get("save_year", 0)
    user = message.from_user

    bot_db.update_stage(user.id, "completed", phone=phone, completed_at=bot_db.now())
    bot_db.log_event(user.id, "phone_given", phone)
    linked.record_bot_completion(user.id, source, name, phone, "🆘 Нужна помощь")

    profile = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">профиль</a>'
    spend_label = {"low": "до 2 000 ₽", "mid": "2 000–5 000 ₽", "high": "более 5 000 ₽"}.get(tier, tier)

    report = (
        f"🆘 <b>НУЖНА ПОМОЩЬ С РЕГИСТРАЦИЕЙ</b>\n"
        f"━━━━━━━━━━━━\n"
        f"👤 Имя: {escape(name)}\n"
        f"📱 Тел: <code>{escape(phone)}</code>\n"
        f"🛒 Трат в мес: {spend_label}\n"
        f"💰 Экономия: ~{save_year:,} ₽/год\n"
        f"🚩 Источник: {label_source(source)}\n"
        f"🔗 Профиль: {profile}\n"
        f"━━━━━━━━━━━━"
    )
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, report)
        except Exception as e:
            log.error(f"admin {admin}: {e}")

    await message.answer(
        f"Готово, {escape(name)}! 💎",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await typing(message.chat.id, 1.5)
    await message.answer(
        "Венера получила твои данные и напишет в течение <b>1–2 часов</b>.\n\n"
        "Она поможет зарегистрироваться за 5 минут прямо в переписке 💎"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="💬 Написать Венере сейчас", url=VENERA_LINK))
    await message.answer("Или напиши ей прямо сейчас 👇", reply_markup=b.as_markup())
    await state.clear()


# ─── РЕГИСТРАЦИЯ ВЫПОЛНЕНА ────────────────────────────────────────────────────

@dp.callback_query(F.data == "reg_done")
async def reg_done(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.update_stage(cb.from_user.id, "registered")
    bot_db.log_event(cb.from_user.id, "reg_done")
    try:
        await cb.message.delete()
    except Exception:
        pass

    await typing(cb.message.chat.id, 1.5)
    data = bot_db.get_user(cb.from_user.id)
    save_year = 0
    if data:
        tier = data.get("spend_tier")
        if tier and tier in SPEND_SAVING:
            save_year = SPEND_SAVING[tier][2]

    await bot.send_message(
        cb.message.chat.id,
        f"🎉 <b>Отлично, добро пожаловать!</b>\n\n"
        f"Теперь ты экономишь <b>~{save_year:,} ₽ в год</b> на тех же покупках 💎\n\n"
        f"Заходи в каталог — скидка уже активна. "
        f"Если возникнут вопросы по первому заказу, Венера всегда поможет:",
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="💬 Написать Венере", url=VENERA_LINK))
    if CHANNEL_LINK:
        b.row(types.InlineKeyboardButton(text="📣 Подписаться на канал", url=CHANNEL_LINK))
    await bot.send_message(
        cb.message.chat.id,
        "В канале — советы по выбору товаров и акции 💎" if CHANNEL_LINK else "На связи! 💎",
        reply_markup=b.as_markup()
    )


# ─── НЕ СЕЙЧАС ───────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "not_now")
async def not_now(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer("окей, без давления 💎")
    bot_db.update_stage(cb.from_user.id, "postponed")
    bot_db.log_event(cb.from_user.id, "not_now")
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await bot.send_message(
        cb.message.chat.id,
        "Хорошо, без давления 💎 Когда захочешь — просто напиши /start.\n"
        f"Или сразу к Венере: {VENERA_LINK}"
    )


# ─── /help ───────────────────────────────────────────────────────────────────

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛍 Зарегистрироваться", url=REG_LINK))
    b.row(types.InlineKeyboardButton(text="💬 Написать Венере", url=VENERA_LINK))
    await message.answer(
        "💎 <b>Чем могу помочь</b>\n\n"
        "/start — начать сначала\n"
        "/help — это сообщение\n\n"
        "Или выбери 👇",
        reply_markup=b.as_markup()
    )


# ─── FALLBACK ────────────────────────────────────────────────────────────────

@dp.message(F.text)
async def fallback(message: types.Message, state: FSMContext):
    bot_db.log_event(message.from_user.id, "freeform", (message.text or "")[:200])
    current = await state.get_state()
    if current:
        await message.answer("Нажми кнопку выше или /start чтобы начать сначала 💎")
        return
    await typing(message.chat.id, 1.0)
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛍 Зарегистрироваться", url=REG_LINK))
    b.row(types.InlineKeyboardButton(text="💬 Написать Венере", url=VENERA_LINK))
    await message.answer(
        "Привет! Я помогаю сэкономить на покупках через Faberlic 💎\n\n"
        "Жми /start — посчитаем твою выгоду за минуту.\n"
        "Или выбери 👇",
        reply_markup=b.as_markup()
    )


# ─── АДМИНКА ─────────────────────────────────────────────────────────────────

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    s = bot_db.funnel_stats()
    lines = ["<b>📊 ВОРОНКА</b>\n"]
    order = ["started", "quiz_started", "showed_link", "registered", "asked_name", "asked_phone", "completed", "postponed"]
    by = {r["funnel_stage"]: r["n"] for r in s["stages"]}
    total = by.get("started", 1) or 1
    for st in order:
        n = by.get(st, 0)
        pct = n * 100 // total
        bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"  {bar} {st:13s} <b>{n}</b> ({pct}%)")
    lines.append("\n<b>🚩 ИСТОЧНИКИ</b>")
    for r in s["sources"][:10]:
        lines.append(f"  {label_source(r['source'])}: {r['total']} → ✅ {r['completed']}")
    await message.answer("\n".join(lines))


@dp.message(Command("reset"))
async def cmd_reset(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    bot_db.update_stage(message.from_user.id, "started")
    await message.answer("✅ стейт сброшен, жми /start")


# ─── НАПОМИНАНИЯ (ДОЖИМ) ──────────────────────────────────────────────────────

async def reminder_loop():
    await asyncio.sleep(90)
    while True:
        try:
            for u in bot_db.pending_reminder1(minutes=10):
                name = u.get("first_name") or "привет"
                tier = u.get("spend_tier")
                save_year = SPEND_SAVING.get(tier, (0, 0, 0))[2] if tier else 0
                try:
                    await bot.send_message(
                        u["tg_id"],
                        f"{escape(name)}, ты успел(а) зарегистрироваться? 💎\n\n"
                        f"Напоминаю — экономия <b>~{save_year:,} ₽ в год</b> "
                        f"ждёт тебя, регистрация займёт 2 минуты 👇",
                        reply_markup=after_reg_kb()
                    )
                    bot_db.mark_reminder1_sent(u["tg_id"])
                    bot_db.log_event(u["tg_id"], "reminder1_sent")
                except TelegramForbiddenError:
                    bot_db.log_event(u["tg_id"], "blocked_bot")
                    bot_db.mark_reminder1_sent(u["tg_id"])
                except Exception as e:
                    log.warning(f"reminder1 {u['tg_id']}: {e}")
                await asyncio.sleep(2)

            for u in bot_db.pending_reminder2(minutes=40):
                name = u.get("first_name") or "привет"
                tier = u.get("spend_tier")
                save_year = SPEND_SAVING.get(tier, (0, 0, 0))[2] if tier else 0
                try:
                    b = InlineKeyboardBuilder()
                    b.row(types.InlineKeyboardButton(text="🛍 Зарегистрироваться", url=REG_LINK))
                    b.row(types.InlineKeyboardButton(text="💬 Как зарегистрироваться?", callback_data="faq"))
                    b.row(types.InlineKeyboardButton(text="🙋 Помогите, не получается", callback_data="need_help"))
                    await bot.send_message(
                        u["tg_id"],
                        f"{escape(name)}, ещё раз напомню 💎\n\n"
                        f"Каждый месяц ты переплачиваешь <b>~{save_year // 12:,} ₽</b> "
                        f"на те же самые покупки.\n\n"
                        f"Регистрация бесплатная и займёт 2 минуты. "
                        f"Если что-то не получается — нажми кнопку, "
                        f"Венера поможет лично 💎",
                        reply_markup=b.as_markup()
                    )
                    bot_db.mark_reminder2_sent(u["tg_id"])
                    bot_db.log_event(u["tg_id"], "reminder2_sent")
                except TelegramForbiddenError:
                    bot_db.log_event(u["tg_id"], "blocked_bot")
                    bot_db.mark_reminder2_sent(u["tg_id"])
                except Exception as e:
                    log.warning(f"reminder2 {u['tg_id']}: {e}")
                await asyncio.sleep(2)

        except Exception as e:
            log.exception(f"reminder loop: {e}")
        await asyncio.sleep(120)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    bot_db.init()
    linked.init()
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("🤖 BOT Венера запущен")
    asyncio.create_task(reminder_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())