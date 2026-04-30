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
GULCHACHAK_LINK = os.getenv("GULCHACHAK_LINK", "https://t.me/Gulchachak_faberlic")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "")
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
    b.row(types.InlineKeyboardButton(text="✅ Зарегистрироваться", url=REG_LINK))
    b.row(types.InlineKeyboardButton(text="❓ Есть вопросы", callback_data="faq"))
    b.row(types.InlineKeyboardButton(text="🙋 Хочу чтобы помогли", callback_data="need_help"))
    return b.as_markup()


def after_reg_kb():
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Да, зарегистрировалась!", callback_data="reg_done"))
    b.row(types.InlineKeyboardButton(text="😕 Не получилось", callback_data="reg_failed"))
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

    # Видео-кружок или фото Гульчачак
    if not await send_video_note_if_exists(message.chat.id, "gulchachak_intro.mp4"):
        sent = await send_photo_if_exists(
            message.chat.id, "gulchachak.jpg",
            caption=(
                "Привет, я <b>Гульчачак</b> 🌸\n\n"
                "<i>[🎥 Здесь будет короткое видео-знакомство — "
                "15 секунд, кто я и чем помогу тебе сэкономить]</i>"
            )
        )
        if not sent:
            await message.answer(
                "Привет, я помощник <b>Гульчачак</b> 🌸\n\n"
                "<i>[🎥 Здесь будет видео-знакомство от Гульчачак]</i>"
            )

    await typing(message.chat.id, 2.0)

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="Да, интересно →", callback_data="hook_yes"))
    b.row(types.InlineKeyboardButton(text="Не верю, это развод 🤔", callback_data="hook_doubt"))

    await message.answer(
        "Ты покупаешь порошок, гель для душа и косметику на Wildberries?\n\n"
        "А знаешь, что <b>те же самые вещи</b> можно брать на <b>20–26% дешевле</b> — "
        "напрямую от производителя, без обязательных закупок и подписок?\n\n"
        "Давай посчитаем — сколько ты сейчас переплачиваешь 👇",
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
    b.row(types.InlineKeyboardButton(text="Ладно, давай посчитаем →", callback_data="hook_yes"))
    b.row(types.InlineKeyboardButton(text="Всё равно не интересно", callback_data="not_now"))

    await bot.send_message(
        cb.message.chat.id,
        "Понимаю скептицизм — в интернете много всякого 🙂\n\n"
        "<b>Faberlic</b> — российская компания, работает с 1997 года. "
        "Производит бытовую химию, косметику, парфюм. "
        "Никаких схем — просто регистрируешься как постоянный покупатель "
        "и получаешь скидку навсегда.\n\n"
        "Давай просто посчитаем на твоих цифрах 👇",
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
    b.row(types.InlineKeyboardButton(text="до 2 000 ₽", callback_data="spend_low"))
    b.row(types.InlineKeyboardButton(text="2 000 – 5 000 ₽", callback_data="spend_mid"))
    b.row(types.InlineKeyboardButton(text="больше 5 000 ₽", callback_data="spend_high"))

    await bot.send_message(
        cb.message.chat.id,
        "Сколько примерно тратишь в месяц на <b>бытовую химию и косметику</b>?\n\n"
        "<i>Порошок, гель для душа, шампунь, крем, средство для посуды — всё вместе</i>",
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
        f"<b>Твоя экономия с Faberlic:</b>\n\n"
        f"💰 <b>~{save_month:,} ₽ в месяц</b>\n"
        f"🎯 <b>~{save_year:,} ₽ в год</b>\n\n"
        f"Живыми деньгами — на те же самые покупки, что ты и так берёшь."
    )

    await typing(cb.message.chat.id, 2.0)
    await bot.send_message(
        cb.message.chat.id,
        "❌ <i>«А вдруг заставят покупать каждый месяц на большие суммы?»</i>\n"
        "✅ Нет. Покупаешь только то, что нужно — когда хочешь. "
        "Никаких обязательных заказов.\n\n"
        "❌ <i>«Регистрация платная?»</i>\n"
        "✅ Нет. Абсолютно бесплатно.\n\n"
        "❌ <i>«Это МЛМ? Надо кого-то приглашать?»</i>\n"
        "✅ Нет. Просто программа лояльности — как карта постоянного покупателя, "
        "только со скидкой 20–26% вместо 5%."
    )

    await typing(cb.message.chat.id, 2.0)
    bot_db.update_stage(cb.from_user.id, "showed_link")
    bot_db.mark_link_shown(cb.from_user.id)

    await bot.send_message(
        cb.message.chat.id,
        f"Регистрация занимает <b>2 минуты</b> 🌸\n\n"
        f"Нажми кнопку ниже — откроется сайт Faberlic. "
        f"Заполни имя, email и телефон. Готово — скидка активна сразу.",
        reply_markup=reg_kb()
    )


# ─── FAQ — ЧАСТЫЕ ВОПРОСЫ ────────────────────────────────────────────────────

@dp.callback_query(F.data == "faq")
async def faq(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "faq")

    await typing(cb.message.chat.id, 1.5)
    await bot.send_message(
        cb.message.chat.id,
        "<b>Отвечаю на самые частые вопросы 🌸</b>\n\n"

        "🔹 <b>Сколько стоит регистрация?</b>\n"
        "Ничего. Бесплатно.\n\n"

        "🔹 <b>Нужно ли делать заказы каждый месяц?</b>\n"
        "Нет. Заказываешь когда хочешь и что хочешь. "
        "Хоть раз в полгода — скидка остаётся.\n\n"

        "🔹 <b>Будут ли мне звонить или спамить?</b>\n"
        "Нет. Только если сама обратишься за помощью к Гульчачак.\n\n"

        "🔹 <b>Это МЛМ? Надо кого-то приглашать?</b>\n"
        "Нет. Ты просто покупаешь для себя дешевле. "
        "Никого приглашать не нужно.\n\n"

        "🔹 <b>Как получить скидку 20–26%?</b>\n"
        "Автоматически после регистрации. "
        "Скидка применяется ко всему каталогу сразу.\n\n"

        "🔹 <b>Как зарегистрироваться?</b>\n"
        "1️⃣ Нажми кнопку «Зарегистрироваться»\n"
        "2️⃣ Введи имя, фамилию, email и телефон\n"
        "3️⃣ Придумай пароль\n"
        "4️⃣ Подтверди email (письмо придёт сразу)\n"
        "5️⃣ Всё — личный кабинет готов, скидка активна 🎉\n\n"
        "<i>Если всё равно что-то непонятно — Гульчачак поможет лично.</i>",
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
        "Без проблем, Гульчачак поможет 🌸\n\n"
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
        f"Приятно познакомиться, {escape(name)} 🌸\n\n"
        f"Оставь номер телефона — Гульчачак напишет "
        f"в течение пары часов и поможет разобраться.",
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
        f"Готово, {escape(name)}! 🌸",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await typing(message.chat.id, 1.5)
    await message.answer(
        "Гульчачак получила твой контакт и напишет в течение <b>1–2 часов</b>.\n\n"
        "Она поможет зарегистрироваться за 5 минут — "
        "прямо в переписке, без звонков 🌸"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="👩‍💻 Написать ей сейчас", url=GULCHACHAK_LINK))
    await message.answer("Или сама напиши ей прямо сейчас 👇", reply_markup=b.as_markup())
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
        f"🎉 <b>Поздравляю!</b>\n\n"
        f"Теперь ты экономишь <b>~{save_year:,} ₽ в год</b> на тех же покупках 🌸\n\n"
        f"Можешь сразу зайти в каталог и сделать первый заказ со скидкой.\n\n"
        f"Если вдруг возникнут вопросы — Гульчачак всегда на связи:",
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="👩‍💻 Написать Гульчачак", url=GULCHACHAK_LINK))
    if CHANNEL_LINK:
        b.row(types.InlineKeyboardButton(text="📣 Подписаться на канал", url=CHANNEL_LINK))
    await bot.send_message(
        cb.message.chat.id,
        "Ещё в её канале — лайфхаки по экономии и обзоры новинок 🌸" if CHANNEL_LINK else "На связи! 🌸",
        reply_markup=b.as_markup()
    )


# ─── НЕ СЕЙЧАС ───────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "not_now")
async def not_now(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer("ок, без давления 🌸")
    bot_db.update_stage(cb.from_user.id, "postponed")
    bot_db.log_event(cb.from_user.id, "not_now")
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await bot.send_message(
        cb.message.chat.id,
        "Без проблем 🌸 Когда будешь готова — просто напиши /start.\n"
        f"Или сразу к Гульчачак: {GULCHACHAK_LINK}"
    )


# ─── /help ───────────────────────────────────────────────────────────────────

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Зарегистрироваться", url=REG_LINK))
    b.row(types.InlineKeyboardButton(text="👩‍💻 Написать Гульчачак", url=GULCHACHAK_LINK))
    await message.answer(
        "🌸 <b>Чем могу помочь</b>\n\n"
        "/start — начать сначала\n"
        "/help — это сообщение\n\n"
        "Или выбери кнопку 👇",
        reply_markup=b.as_markup()
    )


# ─── FALLBACK ────────────────────────────────────────────────────────────────

@dp.message(F.text)
async def fallback(message: types.Message, state: FSMContext):
    bot_db.log_event(message.from_user.id, "freeform", (message.text or "")[:200])
    current = await state.get_state()
    if current:
        await message.answer("Нажми кнопку выше или /start чтобы начать сначала 🌸")
        return
    await typing(message.chat.id, 1.0)
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Зарегистрироваться", url=REG_LINK))
    b.row(types.InlineKeyboardButton(text="👩‍💻 Написать Гульчачак", url=GULCHACHAK_LINK))
    await message.answer(
        "Я бот-помощник Гульчачак 🌸\n\n"
        "Жми /start — посчитаем сколько ты сможешь экономить.\n"
        "Или сразу выбери 👇",
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
            # Напоминание 1 — через 10 минут после показа ссылки
            for u in bot_db.pending_reminder1(minutes=10):
                name = u.get("first_name") or "привет"
                tier = u.get("spend_tier")
                save_year = SPEND_SAVING.get(tier, (0, 0, 0))[2] if tier else 0
                try:
                    await bot.send_message(
                        u["tg_id"],
                        f"{escape(name)}, ты успела зарегистрироваться? 🌸\n\n"
                        f"Напоминаю — твоя экономия <b>~{save_year:,} ₽ в год</b> "
                        f"ждёт тебя, регистрация занимает 2 минуты 👇",
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

            # Напоминание 2 (дожим) — через 40 минут после reminder1
            for u in bot_db.pending_reminder2(minutes=40):
                name = u.get("first_name") or "привет"
                tier = u.get("spend_tier")
                save_year = SPEND_SAVING.get(tier, (0, 0, 0))[2] if tier else 0
                try:
                    b = InlineKeyboardBuilder()
                    b.row(types.InlineKeyboardButton(text="✅ Зарегистрироваться", url=REG_LINK))
                    b.row(types.InlineKeyboardButton(text="❓ Как зарегистрироваться?", callback_data="faq"))
                    b.row(types.InlineKeyboardButton(text="🙋 Помогите, не получается", callback_data="need_help"))
                    await bot.send_message(
                        u["tg_id"],
                        f"{escape(name)}, последний раз напомню 🌸\n\n"
                        f"Ты оставляешь <b>~{save_year:,} ₽ в год</b> на столе — "
                        f"просто потому что ещё не зарегистрировалась.\n\n"
                        f"Это бесплатно и занимает 2 минуты. "
                        f"Если что-то не получается — нажми кнопку ниже, "
                        f"Гульчачак поможет лично 🌸",
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
    log.info("🤖 BOT запущен")
    asyncio.create_task(reminder_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())