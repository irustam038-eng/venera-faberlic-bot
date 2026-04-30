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
CHANNEL_ID = os.getenv("CHANNEL_ID", "")          # например @gulchachak_club или -1001234567890
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "")      # публичная ссылка на канал
MEDIA_DIR = Path(__file__).parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)

# file_id PDF-гайда — кешируется после первой отправки
_guide_file_id: str | None = None

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Экономия по тратам (в месяц, ~23% от суммы)
SPEND_SAVING = {
    "low":    (2000,  460,   5520),   # трат/мес, экономия/мес, экономия/год
    "mid":    (3500,  800,   9600),
    "high":   (6000,  1380,  16560),
}

SOURCE_LABELS = {
    "rsy":    "📡 РСЯ (Яндекс)",
    "yandex": "📡 Яндекс.Директ",
    "direct": "🌐 Прямой вход",
}


def label_source(src):
    return SOURCE_LABELS.get((src or "").lower(), f"🌐 {src or 'прямой вход'}")


# --- FSM ---
class Form(StatesGroup):
    quiz_spend = State()
    waiting_for_name = State()
    waiting_for_phone = State()


# --- ХЕЛПЕРЫ ---
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


async def send_voice_if_exists(chat_id, filename):
    p = MEDIA_DIR / filename
    if not p.exists():
        return False
    try:
        await bot.send_voice(chat_id, types.FSInputFile(p))
        return True
    except Exception as e:
        log.warning(f"voice {filename}: {e}")
        return False


async def is_subscribed(user_id: int) -> bool:
    if not CHANNEL_ID:
        return True  # канал не настроен — пропускаем проверку
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status not in ("left", "kicked")
    except Exception as e:
        log.warning(f"check subscription {user_id}: {e}")
        return False


async def send_guide(chat_id: int):
    global _guide_file_id
    pdf_path = MEDIA_DIR / "guide.pdf"

    if _guide_file_id:
        await bot.send_document(chat_id, _guide_file_id)
        return

    if pdf_path.exists():
        msg = await bot.send_document(chat_id, types.FSInputFile(pdf_path))
        _guide_file_id = msg.document.file_id  # кешируем на будущее
    else:
        # Гайда ещё нет — шлём текстовую заглушку
        await bot.send_message(
            chat_id,
            "📖 <b>Гайд «5 средств Faberlic, которые заменят 15 банок из магазина»</b>\n\n"
            "Гульчачак пришлёт его тебе лично в ближайшее время 🌸"
        )


# ───────────────────────────────────────────
#  ШАГ 1: /start — крючок
# ───────────────────────────────────────────
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

    # Фото или видео-кружок Гульчачак
    if not await send_video_note_if_exists(message.chat.id, "gulchachak_intro.mp4"):
        await send_photo_if_exists(
            message.chat.id, "gulchachak.jpg",
            caption="Привет, я <b>Гульчачак</b> 🌸"
        )

    await typing(message.chat.id, 2.0)

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="Да, хочу знать как →", callback_data="hook_yes"))
    b.row(types.InlineKeyboardButton(text="Не верю, это развод", callback_data="hook_doubt"))

    await message.answer(
        "Ты покупаешь порошок, гель для душа, косметику на Wildberries или в магазине?\n\n"
        "А знаешь, что <b>те же самые вещи</b> можно брать на <b>20–26% дешевле</b> — "
        "напрямую от производителя, без подписок и обязательных закупок?\n\n"
        "Давай посчитаем сколько ты переплачиваешь прямо сейчас 👇",
        reply_markup=b.as_markup()
    )


# ───────────────────────────────────────────
#  Обработка сомнения
# ───────────────────────────────────────────
@dp.callback_query(F.data == "hook_doubt")
async def hook_doubt(cb: types.CallbackQuery, state: FSMContext):
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
        "Понимаю скептицизм 🙂\n\n"
        "Faberlic — это российская компания, работает с 1997 года. "
        "Производит бытовую химию, косметику, парфюм. "
        "Никаких схем — просто покупаешь напрямую как зарегистрированный покупатель "
        "и получаешь скидку постоянного клиента.\n\n"
        "Давай просто посчитаем на твоих цифрах — убедишься сама 👇",
        reply_markup=b.as_markup()
    )


# ───────────────────────────────────────────
#  ШАГ 2: Квиз — сколько тратишь
# ───────────────────────────────────────────
@dp.callback_query(F.data == "hook_yes")
async def quiz_spend(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    bot_db.update_stage(cb.from_user.id, "quiz_started")
    bot_db.log_event(cb.from_user.id, "hook_yes")

    try:
        await cb.message.delete()
    except Exception:
        pass

    await typing(cb.message.chat.id, 1.2)

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="до 2 000 ₽", callback_data="spend_low"))
    b.row(types.InlineKeyboardButton(text="2 000–5 000 ₽", callback_data="spend_mid"))
    b.row(types.InlineKeyboardButton(text="больше 5 000 ₽", callback_data="spend_high"))

    await bot.send_message(
        cb.message.chat.id,
        "Сколько примерно тратишь в месяц на <b>бытовую химию и косметику</b>?\n\n"
        "<i>(порошок, средство для посуды, гель для душа, шампунь, крем — всё вместе)</i>",
        reply_markup=b.as_markup()
    )
    await state.set_state(Form.quiz_spend)


# ───────────────────────────────────────────
#  ШАГ 3: Персональный результат
# ───────────────────────────────────────────
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
        f"Живыми деньгами — на те же самые покупки."
    )

    await typing(cb.message.chat.id, 2.0)

    # Force-subscribe: предлагаем канал + гайд
    if CHANNEL_ID and CHANNEL_LINK:
        b = InlineKeyboardBuilder()
        b.row(types.InlineKeyboardButton(text="🔗 Подписаться на канал", url=CHANNEL_LINK))
        b.row(types.InlineKeyboardButton(text="✅ Я подписалась!", callback_data="check_sub"))
        await bot.send_message(
            cb.message.chat.id,
            f"🎁 Чтобы забрать пошаговую инструкцию как оформить дисконт 20% "
            f"и получить мой авторский гайд <b>«5 средств Faberlic, которые заменят "
            f"15 банок из магазина»</b> — подпишись на мой закрытый канал для мам 👇",
            reply_markup=b.as_markup()
        )
    else:
        # Канал не настроен — идём сразу к сбору контакта
        b = InlineKeyboardBuilder()
        b.row(types.InlineKeyboardButton(text="✅ Хочу так же", callback_data="lead"))
        b.row(types.InlineKeyboardButton(text="📸 Покажи отзывы", callback_data="proof"))
        b.row(types.InlineKeyboardButton(text="⏸ Не сейчас", callback_data="not_now"))
        await bot.send_message(
            cb.message.chat.id,
            "❌ <i>«А вдруг заставят покупать каждый месяц на большие суммы?»</i>\n"
            "✅ Нет. Никаких обязаловок. Покупаешь только то, что нужно — когда хочешь.\n\n"
            "Гульчачак <b>бесплатно</b> оформит тебе личный кабинет и поможет взять "
            "максимум бонусов с первого заказа 🌸",
            reply_markup=b.as_markup()
        )


# ───────────────────────────────────────────
#  Проверка подписки на канал
# ───────────────────────────────────────────
@dp.callback_query(F.data == "check_sub")
async def check_sub(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    subscribed = await is_subscribed(cb.from_user.id)

    if not subscribed:
        await cb.answer(
            "Вижу, что ты ещё не подписалась 😔\n"
            "Нажми первую кнопку, подпишись и возвращайся!",
            show_alert=True
        )
        return

    bot_db.log_event(cb.from_user.id, "subscribed_channel")
    try:
        await cb.message.delete()
    except Exception:
        pass

    await typing(cb.message.chat.id, 1.0)
    await bot.send_message(
        cb.message.chat.id,
        "🎉 Отлично, ты в канале! Держи гайд 👇"
    )
    await send_guide(cb.message.chat.id)

    await typing(cb.message.chat.id, 1.5)
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Хочу оформить дисконт", callback_data="lead"))
    b.row(types.InlineKeyboardButton(text="📸 Покажи отзывы", callback_data="proof"))
    b.row(types.InlineKeyboardButton(text="⏸ Не сейчас", callback_data="not_now"))
    await bot.send_message(
        cb.message.chat.id,
        "❌ <i>«А вдруг заставят покупать каждый месяц?»</i>\n"
        "✅ Нет. Покупаешь только то, что нужно — когда хочешь.\n\n"
        "Гульчачак <b>бесплатно</b> оформит твой кабинет и поможет взять "
        "максимум бонусов с первого заказа 🌸",
        reply_markup=b.as_markup()
    )


# ───────────────────────────────────────────
#  Соц-доказательство
# ───────────────────────────────────────────
@dp.callback_query(F.data == "proof")
async def proof(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "view_proof")

    sent = False
    for fname, caption in [
        ("review_1.jpg", "💬 Отзыв покупательницы"),
        ("review_2.jpg", "💬 Ещё один отзыв"),
        ("payout.jpg",   "💰 Скрин экономии за месяц"),
    ]:
        if await send_photo_if_exists(cb.message.chat.id, fname, caption):
            sent = True
            await asyncio.sleep(1)

    if not sent:
        await typing(cb.message.chat.id, 1.0)
        await bot.send_message(
            cb.message.chat.id,
            "📸 Отзывы и скрины скоро появятся тут.\n"
            "Гульчачак пришлёт их лично — как только оставишь контакт 👇"
        )

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Хочу попробовать", callback_data="lead"))
    b.row(types.InlineKeyboardButton(text="⏸ Не сейчас", callback_data="not_now"))
    await bot.send_message(cb.message.chat.id, "Попробуем? 👇", reply_markup=b.as_markup())


# ───────────────────────────────────────────
#  ШАГ 4: Сбор контакта
# ───────────────────────────────────────────
@dp.callback_query(F.data == "lead")
async def lead_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()

    # Если квиз не прошли (старая кнопка после рестарта)
    if not data.get("spend_tier"):
        await bot.send_message(cb.message.chat.id, "Давай сначала 🌸 жми /start")
        return

    bot_db.update_stage(cb.from_user.id, "asked_name")
    bot_db.log_event(cb.from_user.id, "lead_clicked")

    try:
        await cb.message.delete()
    except Exception:
        pass

    await typing(cb.message.chat.id, 1.0)
    await bot.send_message(
        cb.message.chat.id,
        "Отлично! Осталось два шага 🌸\n\nКак тебя зовут?"
    )
    await state.set_state(Form.waiting_for_name)


@dp.message(Form.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()[:80]
    if len(re.findall(r"[а-яёa-z]", name.lower())) < 2:
        await message.answer("Напиши имя, пожалуйста 🙂")
        return

    await state.update_data(user_name=name)
    bot_db.update_stage(message.from_user.id, "asked_phone", name=name)
    bot_db.log_event(message.from_user.id, "name_given", name)

    kb = ReplyKeyboardBuilder()
    kb.row(types.KeyboardButton(text="📱 Поделиться номером", request_contact=True))

    await typing(message.chat.id, 1.0)
    await message.answer(
        f"Приятно познакомиться, {escape(name)} 🌸\n\n"
        f"Последний шаг — оставь номер телефона.\n"
        f"Гульчачак напишет в течение пары часов и поможет оформить кабинет.",
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
    save_month = data.get("save_month", 0)
    save_year = data.get("save_year", 0)
    user = message.from_user

    bot_db.update_stage(user.id, "completed", phone=phone, completed_at=bot_db.now())
    bot_db.log_event(user.id, "phone_given", phone)
    linked.record_bot_completion(user.id, source, name, phone, "🎁 Скидка")

    profile = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">профиль</a>'

    spend_label = {"low": "до 2 000 ₽", "mid": "2 000–5 000 ₽", "high": "более 5 000 ₽"}.get(tier, tier)

    report = (
        f"🔥 <b>НОВАЯ ЗАЯВКА</b>\n"
        f"━━━━━━━━━━━━\n"
        f"👤 Имя: {escape(name)}\n"
        f"📱 Тел: <code>{escape(phone)}</code>\n"
        f"🛒 Трат в мес: {spend_label}\n"
        f"💰 Экономия: ~{save_month:,} ₽/мес  (~{save_year:,} ₽/год)\n"
        f"🚩 Источник: {label_source(source)}\n"
        f"🔗 Профиль: {profile}\n"
        f"━━━━━━━━━━━━"
    )
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, report)
        except Exception as e:
            log.error(f"admin {admin}: {e}")

    # ───── Финальный экран ─────
    await message.answer(
        f"🎉 <b>Готово, {escape(name)}!</b>",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await typing(message.chat.id, 2.0)
    await message.answer(
        f"Твоя потенциальная экономия — <b>~{save_year:,} ₽ в год</b> 🌸\n\n"
        "<b>Что дальше:</b>\n"
        "1️⃣ Гульчачак напишет тебе в течение <b>1–2 часов</b>\n"
        "2️⃣ Бесплатно оформит личный кабинет\n"
        "3️⃣ Подскажет как взять максимум бонусов с первого заказа\n\n"
        "<i>Никаких звонков. Если не подойдёт — просто скажешь, без обид.</i>"
    )
    await typing(message.chat.id, 1.5)
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="👩‍💻 Написать Гульчачак сейчас", url=GULCHACHAK_LINK))
    await message.answer(
        "Если не хочешь ждать — можешь написать ей первой 👇",
        reply_markup=b.as_markup()
    )
    await state.clear()


# ───────────────────────────────────────────
#  Не сейчас
# ───────────────────────────────────────────
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


# ───────────────────────────────────────────
#  /help
# ───────────────────────────────────────────
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🌸 <b>Как пользоваться ботом</b>\n\n"
        "/start — начать сначала\n"
        "/help — это сообщение\n\n"
        f"Вопросы? Пиши напрямую: {GULCHACHAK_LINK}"
    )


# ───────────────────────────────────────────
#  Fallback
# ───────────────────────────────────────────
@dp.message(F.text)
async def fallback(message: types.Message, state: FSMContext):
    bot_db.log_event(message.from_user.id, "freeform", (message.text or "")[:200])
    current = await state.get_state()
    if current:
        await message.answer("Нажми кнопку выше или /start чтобы начать сначала 🌸")
        return
    await typing(message.chat.id, 1.0)
    await message.answer(
        "Я бот-помощник Гульчачак 🌸\n\n"
        "Жми /start — посчитаем сколько ты сможешь экономить.\n"
        f"Или сразу пиши ей: {GULCHACHAK_LINK}"
    )


# ───────────────────────────────────────────
#  Админка
# ───────────────────────────────────────────
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    s = bot_db.funnel_stats()
    lines = ["<b>📊 ВОРОНКА</b>\n"]
    order = ["started", "quiz_started", "asked_name", "asked_phone", "completed", "postponed"]
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


# ───────────────────────────────────────────
#  Reminder loop
# ───────────────────────────────────────────
async def reminder_loop():
    await asyncio.sleep(60)
    while True:
        try:
            stuck = bot_db.pending_reminders(stuck_minutes=30)
            for u in stuck:
                name = u.get("name") or u.get("first_name") or "ты"
                try:
                    await bot.send_message(
                        u["tg_id"],
                        f"{escape(name)}, отвлеклась? 🌸\n"
                        f"Мы почти посчитали твою экономию — вернись: /start 👈"
                    )
                    bot_db.log_event(u["tg_id"], "reminder_sent")
                except TelegramForbiddenError:
                    bot_db.log_event(u["tg_id"], "blocked_bot")
                except Exception as e:
                    log.warning(f"reminder fail {u['tg_id']}: {e}")
                bot_db.mark_reminder_sent(u["tg_id"])
                await asyncio.sleep(2)
        except Exception as e:
            log.exception(f"reminder loop: {e}")
        await asyncio.sleep(300)


async def main():
    bot_db.init()
    linked.init()
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("🤖 BOT запущен")
    asyncio.create_task(reminder_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
