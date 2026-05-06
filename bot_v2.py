# -*- coding: utf-8 -*-
import asyncio
import csv
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
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
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from dotenv import load_dotenv

import bot_db
import linked

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

TOKEN      = os.getenv("BOT_TOKEN")
ADMIN_IDS  = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

MEDIA_DIR  = Path(__file__).parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)

LINKS_FILE = Path(__file__).parent / "links.json"
TEXTS_FILE = Path(__file__).parent / "texts.json"

# Ссылки по образцу VK-бота (links.json)
DEFAULT_LINKS = {
    "reg":             "https://clck.ru/3TSfhb",
    "catalog":         "https://clck.ru/3TQgSf",
    "catalog_avon":    "https://clck.ru/3TQgU4",
    "catalog_care":    "https://faberlic.com/ru/ru/category/13.02.01.00.00?sponsornumber=722761514&page=0",
    "catalog_home":    "https://faberlic.com/ru/ru/category/05.01.02.00.00?sponsornumber=722761514&page=0",
    "catalog_health":  "https://faberlic.com/ru/ru/category/06.13.03.00.00?sponsornumber=722761514&page=0",
    "catalog_eastern": "https://clck.ru/3TQgWy",
    "catalog_perfume": "https://faberlic.com/ru/ru/category/02.01.01.00.00?sponsornumber=722761514&page=0",
    "catalog_makeup":  "https://faberlic.com/ru/ru/category/12.01.00.00.00?sponsornumber=722761514&page=0",
    "catalog_sets":    "https://clck.ru/3TQgaw",
    # Контакты Венеры
    "venera_tg":   "https://t.me/Venera25Naz",
    "vk_group":    "https://vk.ru/club235304738",
    "vk_personal": "https://vk.ru/id443815960",
    "whatsapp":    "http://wa.me/79274621686",
    "instagram":   "https://www.instagram.com/gazetdinoas",
    "maxchat":     "https://max.ru/join/0XdCIgBT5PEmHxkZDqzgx-UvkcSQ77ZG3H21IVwn9c8",
}

DEFAULT_TEXTS = {
    "gift_promo": (
        "Акция для новых покупателей\n"
        "с 4 по 24 мая 2026 года\n\n"
        "Набор в ПОДАРОК за 1 руб.!\n\n"
        "ШАГ 1: Зарегистрируйся на faberlic.com — получи скидку 20%\n"
        "ШАГ 2: Сделай заказ от 1500 руб. (цены каталога)\n"
        "ШАГ 3: Получи в подарок набор:\n"
        "- Beauty Collagen (арт. 15955)\n"
        "- Крем для век Elasty Eye Filler (арт. 1383)\n"
        "- Ночной крем Skin-Plumping Cream (арт. 1382) ИЛИ\n"
        "  Дневной крем-флюид Firming Fluid Cream (арт. 1381)\n\n"
        "Цена набора в каталоге: 1997 руб.\n"
        "Ты платишь: всего 1 руб.\n\n"
        "Регистрация бесплатная, занимает 2 минуты"
    ),
    "welcome_text": (
        "Здесь я делюсь лайфхаками Faberlic, которые экономят время и деньги\n\n"
        "Ты здесь, потому что хочешь знать секреты чистоты?\n"
        "Или ищешь легендарную кислородную косметику?\n"
        "Может хочешь легко похудеть?\n\n"
        "Выбирай, что тебе прислать прямо сейчас"
    ),
}


# ─── JSON HELPERS ─────────────────────────────────────────────────────────────

def load_links() -> dict:
    if not LINKS_FILE.exists():
        save_links(DEFAULT_LINKS)
        return DEFAULT_LINKS.copy()
    with open(LINKS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    updated = False
    for k, v in DEFAULT_LINKS.items():
        if k not in data:
            data[k] = v
            updated = True
    if updated:
        save_links(data)
    return data


def save_links(data: dict):
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_texts() -> dict:
    if not TEXTS_FILE.exists():
        save_texts(DEFAULT_TEXTS)
        return DEFAULT_TEXTS.copy()
    with open(TEXTS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    for k, v in DEFAULT_TEXTS.items():
        data.setdefault(k, v)
    return data


def save_texts(data: dict):
    with open(TEXTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


SOURCE_LABELS = {
    "rsy":    "📡 РСЯ (Яндекс)",
    "yandex": "📡 Яндекс.Директ",
    "direct": "🌐 Прямой вход",
}


def label_source(src):
    return SOURCE_LABELS.get((src or "").lower(), f"🌐 {src or 'прямой вход'}")


# ─── FSM STATES ───────────────────────────────────────────────────────────────

class Form(StatesGroup):
    waiting_for_fio   = State()
    waiting_for_dob   = State()
    waiting_for_city  = State()
    waiting_for_phone = State()
    waiting_for_email = State()


class Admin(StatesGroup):
    awaiting_link_key          = State()
    awaiting_text_key          = State()
    awaiting_broadcast         = State()
    awaiting_broadcast_confirm = State()
    awaiting_photo_upload      = State()


# ─── BOT & DISPATCHER ─────────────────────────────────────────────────────────

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher(storage=MemoryStorage())


# ─── ХЕЛПЕРЫ ──────────────────────────────────────────────────────────────────

async def typing(chat_id, sec=1.5):
    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(sec)
    except Exception:
        pass


async def send_photo_if_exists(chat_id: int, filename: str, caption: str = None) -> bool:
    p = MEDIA_DIR / filename
    if not p.exists():
        return False
    try:
        await bot.send_photo(chat_id, types.FSInputFile(p), caption=caption, parse_mode=ParseMode.HTML)
        return True
    except Exception as e:
        log.warning(f"photo {filename}: {e}")
        return False


async def send_video_if_exists(chat_id: int, filename: str) -> bool:
    p = MEDIA_DIR / filename
    if not p.exists():
        return False
    try:
        await bot.send_video(chat_id, types.FSInputFile(p))
        return True
    except Exception as e:
        log.warning(f"video {filename}: {e}")
        return False


def after_reg_kb():
    links = load_links()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Зарегистрировался(ась)!", callback_data="reg_done"))
    b.row(types.InlineKeyboardButton(text="😕 Не получилось, нужна помощь", callback_data="reg_failed"))
    b.row(types.InlineKeyboardButton(text="🔁 Попробую ещё раз", url=links["reg"]))
    return b.as_markup()


def socials_kb(links: dict):
    b = InlineKeyboardBuilder()
    b.row(
        types.InlineKeyboardButton(text="👥 VK группа",   url=links["vk_group"]),
        types.InlineKeyboardButton(text="👤 VK страница", url=links["vk_personal"]),
    )
    b.row(
        types.InlineKeyboardButton(text="💬 WhatsApp", url=links["whatsapp"]),
        types.InlineKeyboardButton(text="📸 Instagram", url=links["instagram"]),
    )
    b.row(types.InlineKeyboardButton(text="💬 Max чат", url=links["maxchat"]))
    b.row(types.InlineKeyboardButton(text="💎 Написать Венере", url=links["venera_tg"]))
    return b.as_markup()


def main_menu_kb(is_admin: bool = False) -> types.InlineKeyboardMarkup:
    """6 кнопок главного меню как в VK-боте."""
    b = InlineKeyboardBuilder()
    b.row(
        types.InlineKeyboardButton(text="🧹 Гайд по чистоте", callback_data="clean_main"),
        types.InlineKeyboardButton(text="💄 Уход за собой",    callback_data="care_main"),
    )
    b.row(
        types.InlineKeyboardButton(text="💊 Здоровье",    callback_data="health_main"),
        types.InlineKeyboardButton(text="🎁 Подарок -20%", callback_data="gift_btn"),
    )
    b.row(
        types.InlineKeyboardButton(text="📖 Каталог",     callback_data="catalog_btn"),
        types.InlineKeyboardButton(text="💬 Задать вопрос", callback_data="ask_btn"),
    )
    if is_admin:
        b.row(types.InlineKeyboardButton(text="⚙️ Настройки бота", callback_data="adm_main"))
    return b.as_markup()


# ─── /start ───────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    args   = message.text.split(maxsplit=1)
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

    texts    = load_texts()
    welcome  = texts.get("welcome_text", DEFAULT_TEXTS["welcome_text"])
    is_admin = message.from_user.id in ADMIN_IDS

    full_text = (
        f"Привет! Я твой гид по чистоте и уходу от Faberlic\n\n"
        f"{welcome}"
    )

    video_sent = await send_video_if_exists(message.chat.id, "venera_intro.mp4")
    if video_sent:
        await message.answer(full_text, reply_markup=main_menu_kb(is_admin))
    else:
        photo_sent = await send_photo_if_exists(message.chat.id, "venera.jpg", caption=full_text)
        if photo_sent:
            await message.answer("Выбирай 👇", reply_markup=main_menu_kb(is_admin))
        else:
            await message.answer(full_text, reply_markup=main_menu_kb(is_admin))


# ─── ГАЙД ПО ЧИСТОТЕ ──────────────────────────────────────────────────────────

@dp.callback_query(F.data == "clean_main")
async def cb_clean_main(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "clean_main")
    links = load_links()
    text = (
        "🧹 <b>Косметика для дома Faberlic</b>\n\n"
        "Средства для стирки, уборки кухни, ванной.\n"
        "Экологичные составы, концентраты.\n\n"
        f"Посмотреть каталог:\n{links.get('catalog_home', links['catalog'])}"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Хочу зарегистрироваться",   url=links["reg"]))
    b.row(types.InlineKeyboardButton(text="← Главное меню", callback_data="back_main"))
    photo_sent = await send_photo_if_exists(cb.message.chat.id, "clean_main.jpg", caption=text)
    if not photo_sent:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())
        return
    await bot.send_message(cb.message.chat.id, "Выбирай 👇", reply_markup=b.as_markup())


# ─── УХОД ЗА СОБОЙ ────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "care_main")
async def cb_care_main(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "care_main")
    links = load_links()
    text = (
        "💄 <b>Уход за собой Faberlic</b>\n\n"
        "Уход за лицом, телом, волосами — всё в одном месте.\n"
        "Кремы, сыворотки, маски, гели для душа.\n\n"
        f"Посмотреть каталог:\n{links.get('catalog_care', links['catalog'])}"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Хочу зарегистрироваться", url=links["reg"]))
    b.row(types.InlineKeyboardButton(text="← Главное меню", callback_data="back_main"))
    photo_sent = await send_photo_if_exists(cb.message.chat.id, "care_main.jpg", caption=text)
    if not photo_sent:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())
        return
    await bot.send_message(cb.message.chat.id, "Выбирай 👇", reply_markup=b.as_markup())


# ─── ЗДОРОВЬЕ ─────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "health_main")
async def cb_health_main(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "health_main")
    links = load_links()
    text = (
        "💊 <b>Здоровье и стройность Faberlic</b>\n\n"
        "Wellness-коктейли, БАДы, программы стройности.\n\n"
        f"◾ Здоровье и стройность:\n{links.get('catalog_health', links['catalog'])}\n\n"
        f"◾ Восточный секрет (японская медицина, добавки):\n{links.get('catalog_eastern', links['catalog'])}"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Хочу зарегистрироваться", url=links["reg"]))
    b.row(types.InlineKeyboardButton(text="← Главное меню", callback_data="back_main"))
    photo_sent = await send_photo_if_exists(cb.message.chat.id, "health_main.jpg", caption=text)
    if not photo_sent:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())
        return
    await bot.send_message(cb.message.chat.id, "Выбирай 👇", reply_markup=b.as_markup())


# ─── ПОДАРОК -20% ─────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "gift_btn")
async def cb_gift_btn(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "gift_btn")
    links  = load_links()
    texts  = load_texts()
    text   = texts.get("gift_promo", DEFAULT_TEXTS["gift_promo"])

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛍 Зарегистрироваться",    url=links["reg"]))
    b.row(types.InlineKeyboardButton(text="💎 Написать Венере",        url=links["venera_tg"]))
    b.row(types.InlineKeyboardButton(text="← Главное меню", callback_data="back_main"))

    photo_sent = await send_photo_if_exists(cb.message.chat.id, "gift_promo.jpg", caption=text)
    if not photo_sent:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())
        return
    await bot.send_message(cb.message.chat.id, "Выбирай 👇", reply_markup=b.as_markup())


# ─── КАТАЛОГ ──────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "catalog_btn")
async def cb_catalog_btn(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "catalog_btn")
    links = load_links()
    text = (
        "📖 <b>Выбери раздел каталога:</b>\n\n"
        f"◾ УХОД ЗА СОБОЙ (косметика, кремы, уход):\n{links.get('catalog_care', links['catalog'])}\n\n"
        f"◾ КОСМЕТИКА ДЛЯ ДОМА:\n{links.get('catalog_home', links['catalog'])}\n\n"
        f"◾ ЗДОРОВЬЕ И СТРОЙНОСТЬ:\n{links.get('catalog_health', links['catalog'])}\n\n"
        f"◾ ПАРФЮМЕРИЯ И АРОМАТЫ:\n{links.get('catalog_perfume', links['catalog'])}\n\n"
        f"◾ ВСЁ ДЛЯ МАКИЯЖА:\n{links.get('catalog_makeup', links['catalog'])}\n\n"
        f"◾ НОВИНКИ:\n{links.get('catalog_sets', links['catalog'])}\n\n"
        f"📖 КАТАЛОГ FABERLIC:\n{links['catalog']}\n\n"
        f"📖 КАТАЛОГ AVON:\n{links.get('catalog_avon', links['catalog'])}"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Зарегистрироваться (-20%)", url=links["reg"]))
    b.row(types.InlineKeyboardButton(text="← Главное меню", callback_data="back_main"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


# ─── ЗАДАТЬ ВОПРОС ────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "ask_btn")
async def cb_ask_btn(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "ask_btn")
    links = load_links()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="💎 Написать Венере", url=links["venera_tg"]))
    b.row(types.InlineKeyboardButton(text="← Главное меню",     callback_data="back_main"))
    try:
        await cb.message.edit_text(
            "Напишу лично — отвечу на все вопросы!\n\n"
            "Работаю с Faberlic уже несколько лет и знаю продукцию как свои пять пальцев 💎",
            reply_markup=b.as_markup(),
        )
    except Exception:
        await bot.send_message(
            cb.message.chat.id,
            "Напишу лично — отвечу на все вопросы!\n\n"
            "Работаю с Faberlic уже несколько лет и знаю продукцию как свои пять пальцев 💎",
            reply_markup=b.as_markup(),
        )


# ─── ВЕРНУТЬСЯ В ГЛАВНОЕ МЕНЮ ─────────────────────────────────────────────────

@dp.callback_query(F.data == "back_main")
async def cb_back_main(cb: types.CallbackQuery):
    await cb.answer()
    is_admin = cb.from_user.id in ADMIN_IDS
    texts    = load_texts()
    welcome  = texts.get("welcome_text", DEFAULT_TEXTS["welcome_text"])
    full_text = (
        f"Я твой гид по чистоте и уходу от Faberlic\n\n"
        f"{welcome}"
    )
    try:
        await cb.message.edit_text(full_text, reply_markup=main_menu_kb(is_admin))
    except Exception:
        await bot.send_message(cb.message.chat.id, full_text, reply_markup=main_menu_kb(is_admin))


# ─── GO_REG ───────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "go_reg")
async def go_reg(cb: types.CallbackQuery):
    await cb.answer()
    links = load_links()
    bot_db.update_stage(cb.from_user.id, "showed_link")
    bot_db.mark_link_shown(cb.from_user.id)
    bot_db.log_event(cb.from_user.id, "go_reg")

    text = (
        "Давай начнём с малого — оформим тебе личный кабинет 💎\n\n"
        "Ты сразу увидишь свои скидки и сможешь выбрать подарок.\n\n"
        "Можешь зарегистрироваться самостоятельно по ссылке 👇"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛍 Зарегистрироваться самостоятельно", url=links["reg"]))
    b.row(types.InlineKeyboardButton(text="❓ Есть вопросы",                      callback_data="faq"))
    b.row(types.InlineKeyboardButton(text="📋 Оставить данные — помогу лично",    callback_data="need_help"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


# ─── FAQ ──────────────────────────────────────────────────────────────────────

def faq_menu_kb():
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="💰 Регистрация стоит денег?",          callback_data="faq_cost"))
    b.row(types.InlineKeyboardButton(text="📦 Нужно делать обязательные заказы?", callback_data="faq_orders"))
    b.row(types.InlineKeyboardButton(text="📞 Будут названивать?",                callback_data="faq_calls"))
    b.row(types.InlineKeyboardButton(text="👥 Нужно кого-то приглашать?",         callback_data="faq_invite"))
    b.row(types.InlineKeyboardButton(text="🎯 Как работает скидка?",              callback_data="faq_discount"))
    b.row(types.InlineKeyboardButton(text="📱 Как зарегистрироваться?",           callback_data="faq_howto"))
    b.row(types.InlineKeyboardButton(text="← Назад",                             callback_data="go_reg"))
    return b.as_markup()


def faq_answer_kb():
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛍 Зарегистрироваться", callback_data="go_reg"))
    b.row(types.InlineKeyboardButton(text="← Все вопросы",        callback_data="faq"))
    return b.as_markup()


FAQ_ANSWERS = {
    "faq_cost":     "Регистрация полностью бесплатна. Никаких взносов, стартовых пакетов и платных подписок.",
    "faq_orders":   "Нет никаких обязательных заказов. Покупаешь только когда нужно — скидка никуда не денется.",
    "faq_calls":    "Нет. Никто не будет тебе звонить и уговаривать. Только если сам обратишься к Венере за помощью.",
    "faq_invite":   "Нет. Просто покупаешь для себя дешевле — и всё. Никого приглашать не нужно.",
    "faq_discount": (
        "После регистрации скидка 20–26% применяется ко всему каталогу автоматически. "
        "Просто добавляй товары в корзину и смотри на цену со скидкой."
    ),
    "faq_howto": (
        "1️⃣ Нажми «Зарегистрироваться»\n"
        "2️⃣ Введи имя, фамилию, email и телефон\n"
        "3️⃣ Придумай пароль\n"
        "4️⃣ Подтверди email (письмо придёт моментально)\n"
        "5️⃣ Готово — скидка активна 🎉"
    ),
}


@dp.callback_query(F.data == "faq")
async def faq_menu(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "faq")
    text = "Выбери вопрос который тебя интересует 💎"
    try:
        await cb.message.edit_text(text, reply_markup=faq_menu_kb())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=faq_menu_kb())


@dp.callback_query(F.data.startswith("faq_"))
async def faq_answer(cb: types.CallbackQuery):
    await cb.answer()
    answer = FAQ_ANSWERS.get(cb.data)
    if not answer:
        return
    bot_db.log_event(cb.from_user.id, cb.data)
    try:
        await cb.message.edit_text(answer, reply_markup=faq_answer_kb())
    except Exception:
        await bot.send_message(cb.message.chat.id, answer, reply_markup=faq_answer_kb())


# ─── СБОР ДАННЫХ (FSM) — need_help / reg_failed ───────────────────────────────

@dp.callback_query(F.data.in_({"need_help", "reg_failed"}))
async def need_help(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    bot_db.update_stage(cb.from_user.id, "asked_fio")
    bot_db.log_event(cb.from_user.id, "need_help")
    try:
        await cb.message.delete()
    except Exception:
        pass

    await typing(cb.message.chat.id, 1.0)
    await bot.send_message(
        cb.message.chat.id,
        "Без проблем, Венера поможет разобраться 💎\n\n"
        "Напиши своё <b>ФИО</b> (Фамилия Имя Отчество):",
    )
    await state.set_state(Form.waiting_for_fio)


@dp.message(Form.waiting_for_fio)
async def process_fio(message: types.Message, state: FSMContext):
    fio   = (message.text or "").strip()[:120]
    words = [w for w in fio.split() if re.search(r"[а-яёa-z]", w, re.I)]
    if len(words) < 2:
        await message.answer("Напиши <b>Фамилию и Имя</b> через пробел 🙂")
        return
    await state.update_data(fio=fio)
    bot_db.update_stage(message.from_user.id, "asked_dob")
    await typing(message.chat.id, 0.8)
    await message.answer("Дата рождения в формате <b>ДД.ММ.ГГГГ</b>:")
    await state.set_state(Form.waiting_for_dob)


@dp.message(Form.waiting_for_dob)
async def process_dob(message: types.Message, state: FSMContext):
    dob = (message.text or "").strip()
    if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", dob):
        await message.answer("Напиши дату в формате <b>ДД.ММ.ГГГГ</b>, например: 15.03.1990")
        return
    await state.update_data(dob=dob)
    bot_db.update_stage(message.from_user.id, "asked_city")
    await typing(message.chat.id, 0.8)
    await message.answer("Твой <b>город</b>:")
    await state.set_state(Form.waiting_for_city)


@dp.message(Form.waiting_for_city)
async def process_city(message: types.Message, state: FSMContext):
    city = (message.text or "").strip()[:80]
    if len(re.findall(r"[а-яёa-z]", city, re.I)) < 2:
        await message.answer("Напиши название города 🙂")
        return
    await state.update_data(city=city)
    bot_db.update_stage(message.from_user.id, "asked_phone")

    kb = ReplyKeyboardBuilder()
    kb.row(types.KeyboardButton(text="📱 Поделиться номером", request_contact=True))
    await typing(message.chat.id, 0.8)
    await message.answer(
        "Номер <b>телефона</b>:",
        reply_markup=kb.as_markup(resize_keyboard=True, one_time_keyboard=True),
    )
    await state.set_state(Form.waiting_for_phone)


@dp.message(Form.waiting_for_phone, F.contact)
@dp.message(Form.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone_raw = message.contact.phone_number if message.contact else (message.text or "")
    digits    = re.sub(r"\D", "", phone_raw)
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
    await state.update_data(phone=phone)
    bot_db.update_stage(message.from_user.id, "asked_email")
    await message.answer(
        "Адрес <b>электронной почты</b> (email):",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await state.set_state(Form.waiting_for_email)


@dp.message(Form.waiting_for_email)
async def process_email(message: types.Message, state: FSMContext):
    email = (message.text or "").strip()[:120]
    if "@" not in email or "." not in email.split("@")[-1]:
        await message.answer("Не похоже на email. Напиши адрес вида <b>name@mail.ru</b>")
        return

    data   = await state.get_data()
    fio    = data.get("fio", "—")
    dob    = data.get("dob", "—")
    city   = data.get("city", "—")
    phone  = data.get("phone", "—")
    source = data.get("source", "direct")
    user   = message.from_user

    bot_db.update_stage(user.id, "completed", phone=phone, completed_at=bot_db.now())
    bot_db.log_event(user.id, "email_given", email)
    linked.record_bot_completion(user.id, source, fio, phone, "📋 Анкета")

    profile = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">профиль</a>'

    report = (
        f"📋 <b>ЗАЯВКА НА РЕГИСТРАЦИЮ</b>\n"
        f"━━━━━━━━━━━━\n"
        f"👤 ФИО: {escape(fio)}\n"
        f"🎂 Дата рождения: {escape(dob)}\n"
        f"🏙 Город: {escape(city)}\n"
        f"📱 Телефон: <code>{escape(phone)}</code>\n"
        f"📧 Email: <code>{escape(email)}</code>\n"
        f"🚩 Источник: {label_source(source)}\n"
        f"🔗 Профиль: {profile}\n"
        f"━━━━━━━━━━━━"
    )
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, report)
        except Exception as e:
            log.error(f"admin {admin}: {e}")

    links = load_links()
    await typing(message.chat.id, 1.0)
    await message.answer(
        "Готово! 💎 Венера получила твои данные и свяжется в течение 1–2 часов.\n"
        "Она поможет завершить регистрацию и выбрать подарок новичка 🎁"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="💬 Написать Венере сейчас", url=links["venera_tg"]))
    await message.answer("Или напиши ей прямо сейчас 👇", reply_markup=b.as_markup())
    await state.clear()


# ─── РЕГИСТРАЦИЯ ВЫПОЛНЕНА ────────────────────────────────────────────────────

@dp.callback_query(F.data == "reg_done")
async def reg_done(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.update_stage(cb.from_user.id, "registered")
    bot_db.log_event(cb.from_user.id, "reg_done")
    links = load_links()
    try:
        await cb.message.delete()
    except Exception:
        pass

    await typing(cb.message.chat.id, 1.5)
    await bot.send_message(
        cb.message.chat.id,
        "🎉 <b>Отлично, добро пожаловать в Faberlic!</b>\n\n"
        "Скидка уже активна — заходи в каталог и выбирай товары. "
        "По любым вопросам Венера всегда поможет 💎",
    )
    await bot.send_message(
        cb.message.chat.id,
        "📲 <b>Подписывайся на Венеру в соцсетях</b>\n\n"
        "Там — советы по уходу, новинки каталога, акции и лайфхаки по экономии:",
        reply_markup=socials_kb(links),
    )


# ─── НЕ СЕЙЧАС ───────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "not_now")
async def not_now(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer("окей, без давления 💎")
    bot_db.update_stage(cb.from_user.id, "postponed")
    bot_db.log_event(cb.from_user.id, "not_now")
    await state.clear()
    links = load_links()
    try:
        await cb.message.delete()
    except Exception:
        pass
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="💬 Написать Венере", url=links["venera_tg"]))
    await bot.send_message(
        cb.message.chat.id,
        "Хорошо, без давления 💎 Когда захочешь — просто напиши /start.",
        reply_markup=b.as_markup(),
    )


# ─── /help ────────────────────────────────────────────────────────────────────

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    links    = load_links()
    is_admin = message.from_user.id in ADMIN_IDS
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛍 Зарегистрироваться", url=links["reg"]))
    b.row(types.InlineKeyboardButton(text="💬 Написать Венере",    url=links["venera_tg"]))
    b.row(types.InlineKeyboardButton(text="❓ FAQ",                callback_data="faq"))
    await message.answer(
        "💎 <b>Чем могу помочь</b>\n\n"
        "/start — начать сначала\n"
        "/help — это сообщение\n\n"
        "Или выбери 👇",
        reply_markup=b.as_markup(),
    )


# ─── FALLBACK ─────────────────────────────────────────────────────────────────

@dp.message(F.text & ~F.text.startswith("/"))
async def fallback(message: types.Message, state: FSMContext):
    bot_db.log_event(message.from_user.id, "freeform", (message.text or "")[:200])
    current = await state.get_state()
    if current:
        await message.answer("Нажми кнопку выше или /start чтобы начать сначала 💎")
        return
    links    = load_links()
    is_admin = message.from_user.id in ADMIN_IDS
    await typing(message.chat.id, 1.0)
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛍 Зарегистрироваться", url=links["reg"]))
    b.row(types.InlineKeyboardButton(text="💬 Написать Венере",    url=links["venera_tg"]))
    await message.answer(
        "Привет! Я помогаю открыть доступ к скидкам и подаркам Faberlic 💎\n\n"
        "Жми /start — расскажу всё с начала.\n"
        "Или выбери 👇",
        reply_markup=b.as_markup(),
    )


# ─── ADMIN PANEL ──────────────────────────────────────────────────────────────

def admin_main_kb():
    b = InlineKeyboardBuilder()
    b.row(
        types.InlineKeyboardButton(text="👥 Мои клиенты",  callback_data="adm_leads"),
        types.InlineKeyboardButton(text="📊 Статистика",    callback_data="adm_stats"),
    )
    b.row(
        types.InlineKeyboardButton(text="🖼 Фото в боте",   callback_data="adm_media"),
        types.InlineKeyboardButton(text="🔗 Ссылки",        callback_data="adm_links"),
    )
    b.row(
        types.InlineKeyboardButton(text="✏️ Тексты",        callback_data="adm_texts"),
        types.InlineKeyboardButton(text="📢 Рассылка",      callback_data="adm_broadcast"),
    )
    b.row(types.InlineKeyboardButton(text="❓ Помощь",      callback_data="adm_help"))
    return b.as_markup()


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await message.answer("👑 <b>Панель управления</b>\n\nВыбери раздел:", reply_markup=admin_main_kb())


@dp.callback_query(F.data == "adm_main")
async def adm_main(cb: types.CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    await state.clear()
    try:
        await cb.message.edit_text("👑 <b>Панель управления</b>\n\nВыбери раздел:", reply_markup=admin_main_kb())
    except Exception:
        await bot.send_message(cb.message.chat.id, "👑 <b>Панель управления</b>\n\nВыбери раздел:", reply_markup=admin_main_kb())


@dp.callback_query(F.data == "adm_stats")
async def adm_stats(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()

    s     = bot_db.funnel_stats()
    order = [
        "started", "showed_link", "asked_fio", "asked_dob", "asked_city",
        "asked_phone", "asked_email", "completed", "registered", "postponed",
    ]
    by    = {r["funnel_stage"]: r["n"] for r in s["stages"]}
    total = max(by.get("started", 1), 1)

    lines = ["📊 <b>ВОРОНКА</b>\n"]
    for st in order:
        n   = by.get(st, 0)
        pct = n * 100 // total
        bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"  {bar} {st:<14} <b>{n}</b> ({pct}%)")
    for r in s["stages"]:
        if r["funnel_stage"] not in order:
            lines.append(f"  {'░' * 10} {r['funnel_stage']:<14} <b>{r['n']}</b>")

    lines.append("\n🚩 <b>ИСТОЧНИКИ</b>")
    for r in s["sources"][:10]:
        lines.append(f"  {label_source(r['source'])}: {r['total']} → ✅ {r['completed']}")

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="← Назад", callback_data="adm_main"))
    try:
        await cb.message.edit_text("\n".join(lines), reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, "\n".join(lines), reply_markup=b.as_markup())


# ─── ТЕКСТЫ (ADMIN) ───────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm_texts")
async def adm_texts(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✏️ Текст приветствия",  callback_data="adm_edit_welcome"))
    b.row(types.InlineKeyboardButton(text="✏️ Текст акции/подарка", callback_data="adm_edit_gift"))
    b.row(types.InlineKeyboardButton(text="← Назад",               callback_data="adm_main"))
    try:
        await cb.message.edit_text("✏️ <b>Тексты в боте</b>\n\nВыбери что изменить:", reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, "✏️ <b>Тексты в боте</b>\n\nВыбери что изменить:", reply_markup=b.as_markup())


TEXT_KEY_NAMES = {
    "adm_edit_welcome": ("welcome_text", "текст приветствия"),
    "adm_edit_gift":    ("gift_promo",   "текст акции/подарка"),
}


@dp.callback_query(F.data.in_(set(TEXT_KEY_NAMES.keys())))
async def adm_edit_text(cb: types.CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    text_key, label = TEXT_KEY_NAMES[cb.data]
    texts   = load_texts()
    current = texts.get(text_key, "—")
    await state.set_state(Admin.awaiting_text_key)
    await state.update_data(text_key=text_key)
    await bot.send_message(
        cb.message.chat.id,
        f"Отправь новый <b>{label}</b>. Текущий:\n\n<code>{escape(current)}</code>",
    )


@dp.message(Admin.awaiting_text_key)
async def adm_receive_text(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    new_text = (message.text or "").strip()
    if not new_text:
        await message.answer("Текст не может быть пустым. Попробуй ещё раз.")
        return
    data     = await state.get_data()
    text_key = data.get("text_key")
    texts    = load_texts()
    texts[text_key] = new_text
    save_texts(texts)
    await state.clear()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="← К текстам", callback_data="adm_texts"))
    await message.answer(
        f"✅ Текст <b>{text_key}</b> обновлён.",
        reply_markup=b.as_markup(),
    )


# ─── ССЫЛКИ (ADMIN) ───────────────────────────────────────────────────────────

LINK_KEY_NAMES = {
    "adm_set_reg":      ("reg",             "реферальная ссылка (регистрация)"),
    "adm_set_catalog":  ("catalog",         "полный каталог"),
    "adm_set_care":     ("catalog_care",    "уход за собой"),
    "adm_set_home":     ("catalog_home",    "косметика для дома"),
    "adm_set_health":   ("catalog_health",  "здоровье и стройность"),
    "adm_set_perfume":  ("catalog_perfume", "парфюмерия"),
    "adm_set_makeup":   ("catalog_makeup",  "макияж"),
    "adm_set_sets":     ("catalog_sets",    "новинки"),
}


@dp.callback_query(F.data == "adm_links")
async def adm_links(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    links = load_links()

    def short(url):
        return url[:40] + "…" if len(url) > 40 else url

    text = (
        "🔗 <b>Текущие ссылки</b>\n\n"
        f"🔑 Регистрация: <code>{short(links.get('reg','—'))}</code>\n"
        f"📖 Полный каталог: <code>{short(links.get('catalog','—'))}</code>\n"
        f"💄 Уход за собой: <code>{short(links.get('catalog_care','—'))}</code>\n"
        f"🏠 Дом: <code>{short(links.get('catalog_home','—'))}</code>\n"
        f"💊 Здоровье: <code>{short(links.get('catalog_health','—'))}</code>\n"
        f"🌹 Парфюм: <code>{short(links.get('catalog_perfume','—'))}</code>\n"
        f"💋 Макияж: <code>{short(links.get('catalog_makeup','—'))}</code>\n"
        f"✨ Новинки: <code>{short(links.get('catalog_sets','—'))}</code>"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🔑 Регистрация",       callback_data="adm_set_reg"))
    b.row(types.InlineKeyboardButton(text="📖 Полный каталог",     callback_data="adm_set_catalog"))
    b.row(
        types.InlineKeyboardButton(text="💄 Уход за собой", callback_data="adm_set_care"),
        types.InlineKeyboardButton(text="🏠 Дом",            callback_data="adm_set_home"),
    )
    b.row(
        types.InlineKeyboardButton(text="💊 Здоровье",  callback_data="adm_set_health"),
        types.InlineKeyboardButton(text="🌹 Парфюм",    callback_data="adm_set_perfume"),
    )
    b.row(
        types.InlineKeyboardButton(text="💋 Макияж",    callback_data="adm_set_makeup"),
        types.InlineKeyboardButton(text="✨ Новинки",   callback_data="adm_set_sets"),
    )
    b.row(types.InlineKeyboardButton(text="← Назад", callback_data="adm_main"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


@dp.callback_query(F.data.in_(set(LINK_KEY_NAMES.keys())))
async def adm_set_link(cb: types.CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    link_key, label = LINK_KEY_NAMES[cb.data]
    links   = load_links()
    current = links.get(link_key, "—")
    await state.set_state(Admin.awaiting_link_key)
    await state.update_data(link_key=link_key)
    await bot.send_message(
        cb.message.chat.id,
        f"Отправь новую ссылку для <b>{label}</b>. Текущая:\n<code>{current}</code>",
    )


@dp.message(Admin.awaiting_link_key)
async def adm_receive_link(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    new_url = (message.text or "").strip()
    if not new_url.startswith("http"):
        await message.answer("Ссылка должна начинаться с http. Попробуй ещё раз.")
        return
    data     = await state.get_data()
    link_key = data.get("link_key")
    links    = load_links()
    links[link_key] = new_url
    save_links(links)
    await state.clear()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="← В управление ссылками", callback_data="adm_links"))
    await message.answer(
        f"✅ Ссылка <b>{link_key}</b> обновлена:\n<code>{new_url}</code>",
        reply_markup=b.as_markup(),
    )


# ─── РАССЫЛКА (ADMIN) ─────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(cb: types.CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    await state.set_state(Admin.awaiting_broadcast)
    await bot.send_message(
        cb.message.chat.id,
        "📢 <b>Рассылка</b>\n\n"
        "Отправь текст сообщения (можно с форматированием HTML). "
        "Будет отправлено всем пользователям бота.",
    )


@dp.message(Admin.awaiting_broadcast)
async def adm_broadcast_text(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    text    = message.text or message.caption or ""
    all_ids = bot_db.get_all_tg_ids()
    await state.update_data(broadcast_text=text)
    await state.set_state(Admin.awaiting_broadcast_confirm)
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Да, отправить", callback_data="adm_broadcast_go"))
    b.row(types.InlineKeyboardButton(text="❌ Отменить",      callback_data="adm_main"))
    await message.answer(
        f"Вот как будет выглядеть сообщение:\n\n---\n{text}\n---\n\n"
        f"Отправить <b>{len(all_ids)}</b> пользователям?",
        reply_markup=b.as_markup(),
    )


@dp.callback_query(F.data == "adm_broadcast_go")
async def adm_broadcast_go(cb: types.CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    await state.clear()

    all_ids = bot_db.get_all_tg_ids()
    ok = fail = 0
    for tg_id in all_ids:
        try:
            await bot.send_message(tg_id, text)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="← Главное меню", callback_data="adm_main"))
    await bot.send_message(
        cb.message.chat.id,
        f"✅ Рассылка завершена\n\nОтправлено: {ok}\nНе доставлено: {fail}",
        reply_markup=b.as_markup(),
    )


# ─── КЛИЕНТЫ (ADMIN) ──────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm_leads")
async def adm_leads(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    leads = bot_db.get_recent_leads(10)

    if not leads:
        text = "👥 <b>Мои клиенты</b>\n\nПока никто не написал боту."
    else:
        parts = []
        for lead in leads:
            parts.append(
                f"👤 {lead.get('name') or lead.get('first_name') or '—'}\n"
                f"📱 {lead.get('phone') or '—'}\n"
                f"📅 {(lead.get('last_seen') or '')[:16]}\n"
                f"🚩 {label_source(lead.get('source'))}"
            )
        text = "👥 <b>Последние клиенты</b>\n\n" + "\n\n".join(parts)

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="📥 Скачать всех в Excel", callback_data="adm_export"))
    b.row(types.InlineKeyboardButton(text="← Назад",                 callback_data="adm_main"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


@dp.callback_query(F.data == "adm_export")
async def adm_export(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    await bot.send_message(cb.message.chat.id, "📥 Формирую таблицу, секунду...")

    fields = ["tg_id", "username", "first_name", "name", "source",
              "funnel_stage", "first_seen", "last_seen", "phone", "completed_at"]
    with bot_db.conn() as c:
        rows = c.execute(
            "SELECT tg_id, username, first_name, name, source, "
            "funnel_stage, first_seen, last_seen, phone, completed_at "
            "FROM users ORDER BY first_seen DESC"
        ).fetchall()

    tmp = Path(tempfile.mktemp(suffix=".csv"))
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fields})

    await bot.send_document(
        cb.message.chat.id,
        types.FSInputFile(str(tmp), filename="clients_venera.csv"),
        caption=f"Клиенты — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
    )
    tmp.unlink(missing_ok=True)


# ─── МЕДИА (ADMIN) ────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "adm_media")
async def adm_media(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    MEDIA_DIR.mkdir(exist_ok=True)
    files   = list(MEDIA_DIR.iterdir())
    current = ", ".join(f.name for f in files) if files else "нет загруженных фото"
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="📸 Моё фото (приветствие /start)", callback_data="adm_photo_venera"))
    b.row(types.InlineKeyboardButton(text="🎬 Видео-приветствие",             callback_data="adm_video_venera"))
    b.row(types.InlineKeyboardButton(text="🗑 Удалить фото",                   callback_data="adm_delete_photo"))
    b.row(types.InlineKeyboardButton(text="← Назад",                          callback_data="adm_main"))
    await cb.message.edit_text(
        f"🖼 <b>Фото и видео в боте</b>\n\n"
        f"Сейчас загружено: <b>{current}</b>\n\n"
        "Когда пользователь пишет /start — бот показывает твоё фото или видео.\n"
        "Загрузи новое 👇",
        reply_markup=b.as_markup(),
    )


@dp.callback_query(F.data.in_({"adm_photo_venera", "adm_video_venera"}))
async def adm_photo_start(cb: types.CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    is_video = cb.data == "adm_video_venera"
    slot     = "venera_intro" if is_video else "venera"
    ext      = "mp4" if is_video else "jpg"
    await state.set_state(Admin.awaiting_photo_upload)
    await state.update_data(photo_slot=slot, photo_ext=ext, is_video=is_video)
    kind = "видео (MP4)" if is_video else "фото"
    await cb.message.edit_text(
        f"Отправь мне своё {kind} — просто прикрепи файл и нажми отправить.\n\n"
        "(Отмена — напиши /admin)"
    )


@dp.message(Admin.awaiting_photo_upload)
async def adm_receive_media(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data     = await state.get_data()
    slot     = data.get("photo_slot", "media")
    ext      = data.get("photo_ext", "jpg")
    is_video = data.get("is_video", False)
    MEDIA_DIR.mkdir(exist_ok=True)
    dest = MEDIA_DIR / f"{slot}.{ext}"

    if is_video and message.video:
        await bot.download(message.video, destination=str(dest))
    elif not is_video and message.photo:
        await bot.download(message.photo[-1], destination=str(dest))
    else:
        kind = "видео" if is_video else "фото"
        await message.answer(f"Нужно прислать именно {kind}, не текст.")
        return

    await state.clear()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="← Назад в настройки", callback_data="adm_main"))
    await message.answer("✅ Сохранено! Теперь бот покажет новый файл при /start", reply_markup=b.as_markup())


@dp.callback_query(F.data == "adm_delete_photo")
async def adm_delete_photo(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    MEDIA_DIR.mkdir(exist_ok=True)
    files = list(MEDIA_DIR.iterdir())
    if not files:
        await cb.message.edit_text(
            "Папка с медиа пуста — удалять нечего.",
            reply_markup=(InlineKeyboardBuilder().row(
                types.InlineKeyboardButton(text="← Назад", callback_data="adm_media")
            ).as_markup()),
        )
        return
    b = InlineKeyboardBuilder()
    for f in files:
        b.row(types.InlineKeyboardButton(text=f"🗑 {f.name}", callback_data=f"adm_delfile_{f.name}"))
    b.row(types.InlineKeyboardButton(text="← Назад", callback_data="adm_media"))
    await cb.message.edit_text(
        "⚠️ Выбери файл для удаления (удалённое не восстановить):",
        reply_markup=b.as_markup(),
    )


@dp.callback_query(F.data.startswith("adm_delfile_"))
async def adm_delfile(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    fname  = cb.data.removeprefix("adm_delfile_")
    target = MEDIA_DIR / fname
    if target.exists():
        target.unlink()
        await cb.message.edit_text(
            f"✅ Файл <b>{fname}</b> удалён.",
            reply_markup=(InlineKeyboardBuilder().row(
                types.InlineKeyboardButton(text="← Назад", callback_data="adm_media")
            ).as_markup()),
        )
    else:
        await cb.message.edit_text(
            "Файл не найден.",
            reply_markup=(InlineKeyboardBuilder().row(
                types.InlineKeyboardButton(text="← Назад", callback_data="adm_media")
            ).as_markup()),
        )


@dp.callback_query(F.data == "adm_help")
async def adm_help(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="← Назад", callback_data="adm_main"))
    await cb.message.edit_text(
        "❓ <b>Как пользоваться настройками</b>\n\n"
        "👥 <b>Мои клиенты</b> — список людей, которые написали боту. "
        "Можно скачать таблицу CSV.\n\n"
        "📊 <b>Статистика</b> — сколько людей зашло, сколько нажало зарегистрироваться.\n\n"
        "🖼 <b>Фото в боте</b> — поменяй своё фото или видео при /start.\n\n"
        "🔗 <b>Ссылки</b> — обнови реферальные ссылки на каталоги.\n\n"
        "✏️ <b>Тексты</b> — измени текст приветствия или условия акции.\n\n"
        "📢 <b>Рассылка</b> — отправь сообщение сразу всем, кто писал боту.\n\n"
        "Если что-то не работает — напиши Раилю 💬",
        reply_markup=b.as_markup(),
    )


# ─── /stats и /reset ──────────────────────────────────────────────────────────

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    s     = bot_db.funnel_stats()
    order = [
        "started", "showed_link", "asked_fio", "asked_dob", "asked_city",
        "asked_phone", "asked_email", "completed", "registered", "postponed",
    ]
    by    = {r["funnel_stage"]: r["n"] for r in s["stages"]}
    total = max(by.get("started", 1), 1)
    lines = ["<b>📊 ВОРОНКА</b>\n"]
    for st in order:
        n   = by.get(st, 0)
        pct = n * 100 // total
        bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"  {bar} {st:<13} <b>{n}</b> ({pct}%)")
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
                try:
                    await bot.send_message(
                        u["tg_id"],
                        "Привет! Ты ещё не зарегистрировался(ась)? 💎\n\n"
                        "Я помогу — займёт 2 минуты 👇",
                        reply_markup=after_reg_kb(),
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
                links = load_links()
                try:
                    b = InlineKeyboardBuilder()
                    b.row(types.InlineKeyboardButton(text="🛍 Зарегистрироваться",        url=links["reg"]))
                    b.row(types.InlineKeyboardButton(text="📋 Помогите — оставлю данные", callback_data="need_help"))
                    await bot.send_message(
                        u["tg_id"],
                        "Ещё раз напомню 💎\n\n"
                        "Регистрация бесплатная и займёт 2 минуты. "
                        "Если что-то не получается — нажми кнопку, Венера поможет лично 💎",
                        reply_markup=b.as_markup(),
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
    load_links()   # создаст links.json с дефолтами если не существует
    load_texts()   # создаст texts.json с дефолтами если не существует
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("BOT Венера запущен")
    asyncio.create_task(reminder_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())