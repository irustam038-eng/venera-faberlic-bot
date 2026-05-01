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

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

MEDIA_DIR = Path(__file__).parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)

LINKS_FILE = Path(__file__).parent / "links.json"

DEFAULT_LINKS = {
    "reg": "https://faberlic.com/register?sponsornumber=742652198&lang=ru&r=1000034210371",
    "catalog_beauty": "https://faberlic.com/ru/ru/catalogs/1094?sponsornumber=742652198",
    "catalog_health": "https://faberlic.com/ru/ru/catalogs/1102?sponsornumber=742652198",
    "catalog_makeup": "https://faberlic.com/ru/ru/catalogs/1102?sponsornumber=742652198",
    # Эко-дом — категории
    "cat_laundry":  "https://faberlic.com/ru/ru/category/05.01.03.00.00?sponsornumber=742652198&page=0",
    "cat_shoes":    "https://faberlic.com/ru/ru/category/05.01.10.00.00?sponsornumber=742652198&page=0",
    "cat_dishes":   "https://faberlic.com/ru/ru/category/05.01.01.00.00?sponsornumber=742652198&page=0",
    "cat_bathroom": "https://faberlic.com/ru/ru/category/05.01.04.00.00?sponsornumber=742652198&page=0",
    "cat_kitchen":  "https://faberlic.com/ru/ru/category/05.01.02.00.00?sponsornumber=742652198&page=0",
    "cat_surfaces": "https://faberlic.com/ru/ru?sponsornumber=742652198",
    "cat_aroma":    "https://faberlic.com/ru/ru/category/05.01.06.00.00?sponsornumber=742652198&page=0",
    # Одежда и аксессуары
    "cat_tights":    "https://faberlic.com/ru/ru/category/07.02.02.01.01?sponsornumber=742652198&page=0",
    "cat_underwear": "https://faberlic.com/ru/ru/category/07.02.01.03.00?sponsornumber=742652198&page=0",
    # Другие категории
    "cat_kids": "https://faberlic.com/ru/ru/category/22.03.01.00.00?sponsornumber=742652198&page=0",
    "cat_men":  "https://faberlic.com/ru/ru/category/13.07.02.01.00?sponsornumber=742652198&page=0",
    # Контакты
    "venera_tg":  "https://t.me/Venera25Naz",
    "vk_group":   "https://vk.ru/club235304738",
    "vk_personal": "https://vk.ru/id443815960",
    "whatsapp":   "http://wa.me/79274621686",
    "instagram":  "https://www.instagram.com/gazetdinoas",
    "maxchat":    "https://max.ru/join/0XdCIgBT5PEmHxkZDqzgx-UvkcSQ77ZG3H21IVwn9c8",
}


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


SOURCE_LABELS = {
    "rsy":    "📡 РСЯ (Яндекс)",
    "yandex": "📡 Яндекс.Директ",
    "direct": "🌐 Прямой вход",
}


def label_source(src):
    return SOURCE_LABELS.get((src or "").lower(), f"🌐 {src or 'прямой вход'}")


# ─── FSM STATES ──────────────────────────────────────────────────────────────

class Form(StatesGroup):
    waiting_for_fio   = State()
    waiting_for_dob   = State()
    waiting_for_city  = State()
    waiting_for_phone = State()
    waiting_for_email = State()


class Admin(StatesGroup):
    awaiting_link_key          = State()
    awaiting_broadcast         = State()
    awaiting_broadcast_confirm = State()
    awaiting_photo_upload      = State()


# ─── BOT & DISPATCHER ────────────────────────────────────────────────────────

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher(storage=MemoryStorage())


# ─── ХЕЛПЕРЫ ─────────────────────────────────────────────────────────────────

async def typing(chat_id, sec=1.5):
    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(sec)
    except Exception:
        pass


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


async def send_photo_if_exists(chat_id: int, filename: str, caption: str = None) -> bool:
    p = MEDIA_DIR / filename
    if not p.exists():
        return False
    try:
        await bot.send_photo(chat_id, types.FSInputFile(p), caption=caption)
        return True
    except Exception as e:
        log.warning(f"photo {filename}: {e}")
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
        types.InlineKeyboardButton(text="👥 VK группа", url=links["vk_group"]),
        types.InlineKeyboardButton(text="👤 VK страница", url=links["vk_personal"]),
    )
    b.row(
        types.InlineKeyboardButton(text="💬 WhatsApp", url=links["whatsapp"]),
        types.InlineKeyboardButton(text="📸 Instagram", url=links["instagram"]),
    )
    b.row(types.InlineKeyboardButton(text="💬 Max чат", url=links["maxchat"]))
    b.row(types.InlineKeyboardButton(text="💎 Написать Венере", url=links["venera_tg"]))
    return b.as_markup()


# ─── /start ──────────────────────────────────────────────────────────────────

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

    welcome = (
        "Привет! Я твой гид в мире умного шопинга с Faberlic 🌸\n\n"
        "Здесь ты узнаешь, как пользоваться премиальным продуктом — "
        "от кислородной косметики до эко-средств для дома — и при этом:\n\n"
        "1️⃣ Экономить семейный бюджет\n"
        "2️⃣ Получать подарки\n"
        "3️⃣ Создать источник дополнительного дохода, просто делясь своими отзывами\n\n"
        "С чего начнём?"
    )

    video_sent = await send_video_if_exists(message.chat.id, "venera_intro.mp4")
    if video_sent:
        await message.answer(welcome)
    else:
        photo_sent = await send_photo_if_exists(message.chat.id, "venera.jpg", caption=welcome)
        if not photo_sent:
            await message.answer(welcome)

    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="⭐ Посмотреть хиты каталога", callback_data="hits_catalog"))
    b.row(types.InlineKeyboardButton(text="🎁 Забрать подарок новичка",  callback_data="gift_newbie"))
    if message.from_user.id in ADMIN_IDS:
        b.row(types.InlineKeyboardButton(text="⚙️ Настройки бота", callback_data="adm_main"))
    await message.answer("С чего начнём? 👇", reply_markup=b.as_markup())


# ─── ХИТЫ КАТАЛОГА ───────────────────────────────────────────────────────────

@dp.callback_query(F.data == "hits_catalog")
async def hits_catalog(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "hits_catalog")

    text = (
        "Почему Faberlic выбирают миллионы? 🧪\n\n"
        "Мы — компания-производитель с собственной лабораторией.\n\n"
        "🌿 <b>Эко-дом:</b> разлагается в природе, безопасен для детей и животных\n"
        "🫧 <b>Кислородный комплекс:</b> доставляет кислород в глубокие слои кожи (эксклюзивный патент)\n"
        "🌹 <b>Парфюм:</b> создаётся во Франции великими парфюмерами (Пьер Бурдон, Бертран Дюшофур)"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🏠 Эко-дом",             callback_data="cat_health"))
    b.row(types.InlineKeyboardButton(text="💄 Красота и уход",       callback_data="cat_beauty"))
    b.row(types.InlineKeyboardButton(text="💅 Декоративная косметика", callback_data="cat_makeup"))
    b.row(
        types.InlineKeyboardButton(text="👗 Колготки и бельё", callback_data="cat_clothes"),
        types.InlineKeyboardButton(text="👶 Детское",           callback_data="cat_kids_btn"),
    )
    b.row(types.InlineKeyboardButton(text="👨 Мужчинам",       callback_data="cat_men_btn"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


# ─── ПОДАРОК НОВИЧКА ─────────────────────────────────────────────────────────

@dp.callback_query(F.data == "gift_newbie")
async def gift_newbie(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "gift_newbie")

    text = (
        "🎁 <b>Подарок новичка</b>\n\n"
        "При первой регистрации каждый новый участник получает приветственный подарок от Faberlic.\n\n"
        "Это набор пробников или товар из актуального каталога — "
        "выбираешь сама при оформлении первого заказа.\n\n"
        "Чтобы получить подарок — нужно зарегистрироваться. "
        "Это бесплатно и займёт 2 минуты 💎"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛍 Зарегистрироваться и получить подарок", callback_data="go_reg"))
    b.row(types.InlineKeyboardButton(text="⭐ Сначала посмотреть продукты",           callback_data="hits_catalog"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


# ─── КАТЕГОРИИ КАТАЛОГА ───────────────────────────────────────────────────────

def _catalog_kb(cat_url_key: str) -> types.InlineKeyboardMarkup:
    links = load_links()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛒 Открыть каталог",                          url=links[cat_url_key]))
    b.row(types.InlineKeyboardButton(text="Хочу только скидку — регистрируюсь",          callback_data="go_reg"))
    b.row(types.InlineKeyboardButton(text="Узнать, как на этом ещё и заработать",        callback_data="biz_block"))
    b.row(types.InlineKeyboardButton(text="← Назад",                                     callback_data="hits_catalog"))
    return b.as_markup()


@dp.callback_query(F.data == "cat_health")
async def cat_health(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "cat_health")
    text = (
        "🏠 <b>Эко-дом — чистота без химии</b>\n\n"
        "Линейка FaberHome — профессиональная уборка без агрессивной химии.\n"
        "Без хлора, фосфатов, безопасно для детей и животных. Выбери категорию 👇"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="👕 Уход за одеждой / стирка", callback_data="eco_laundry"))
    b.row(types.InlineKeyboardButton(text="👟 Уход за обувью",            callback_data="eco_shoes"))
    b.row(types.InlineKeyboardButton(text="🍽 Уход за посудой",           callback_data="eco_dishes"))
    b.row(types.InlineKeyboardButton(text="🚿 Ванная и туалет",           callback_data="eco_bathroom"))
    b.row(types.InlineKeyboardButton(text="🍳 Уход за кухней",            callback_data="eco_kitchen"))
    b.row(types.InlineKeyboardButton(text="✨ Уход за поверхностями",     callback_data="eco_surfaces"))
    b.row(types.InlineKeyboardButton(text="🌸 Ароматизация воздуха",      callback_data="eco_aroma"))
    b.row(types.InlineKeyboardButton(text="← Назад",                     callback_data="hits_catalog"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


def _eco_kb(link_key: str) -> types.InlineKeyboardMarkup:
    links = load_links()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛒 Открыть каталог", url=links[link_key]))
    b.row(types.InlineKeyboardButton(text="✅ Хочу — зарегистрироваться", callback_data="go_reg"))
    b.row(types.InlineKeyboardButton(text="← Назад",                     callback_data="cat_health"))
    return b.as_markup()


ECO_CARDS = {
    "eco_laundry": (
        "cat_laundry",
        "👕 <b>Уход за одеждой и стирка</b>\n\n"
        "• Стиральные порошки и гели — без фосфатов\n"
        "• Капсулы и таблетки для стирки\n"
        "• Кондиционеры и ополаскиватели\n"
        "• Пятновыводители\n\n"
        "🌿 Безопасно для чувствительной кожи и детских вещей.",
    ),
    "eco_shoes": (
        "cat_shoes",
        "👟 <b>Уход за обувью</b>\n\n"
        "• Кремы и спреи для кожи\n"
        "• Водоотталкивающая пропитка\n"
        "• Средства для замши и нубука\n"
        "• Дезодоранты для обуви\n\n"
        "✨ Профессиональный уход в домашних условиях.",
    ),
    "eco_dishes": (
        "cat_dishes",
        "🍽 <b>Уход за посудой</b>\n\n"
        "• Гель и средства для мытья посуды вручную\n"
        "• Таблетки и порошок для посудомоечных машин\n"
        "• Соль и ополаскиватель для ПММ\n\n"
        "💧 Быстро смывается, не оставляет разводов.",
    ),
    "eco_bathroom": (
        "cat_bathroom",
        "🚿 <b>Ванная и туалет</b>\n\n"
        "• Чистящий гель для ванны и душевой кабины\n"
        "• Средство против известкового налёта\n"
        "• Чистящий гель для унитаза\n"
        "• Таблетки для бачка\n\n"
        "⚡ Действует за 3 минуты, без хлора.",
    ),
    "eco_kitchen": (
        "cat_kitchen",
        "🍳 <b>Уход за кухней</b>\n\n"
        "• Антижир для плит и духовок\n"
        "• Средство для чистки микроволновок\n"
        "• Обезжириватель для поверхностей\n\n"
        "🔥 Щелочная формула растворяет жир в кашицу. Работает как профессиональный клининг.",
    ),
    "eco_surfaces": (
        "cat_surfaces",
        "✨ <b>Уход за поверхностями</b>\n\n"
        "• Универсальные чистящие средства\n"
        "• Спреи для стёкол и зеркал\n"
        "• Полироли для мебели\n"
        "• Влажные салфетки для уборки\n\n"
        "🌿 Без агрессивных компонентов.",
    ),
    "eco_aroma": (
        "cat_aroma",
        "🌸 <b>Ароматизация воздуха и тканей</b>\n\n"
        "• Освежители воздуха (спреи, диффузоры)\n"
        "• Ароматические саше\n"
        "• Средства для ароматизации белья при стирке\n\n"
        "💐 Французские ароматические композиции. Долго держатся.",
    ),
}


@dp.callback_query(F.data.in_(set(ECO_CARDS.keys())))
async def eco_category(cb: types.CallbackQuery):
    await cb.answer()
    link_key, text = ECO_CARDS[cb.data]
    bot_db.log_event(cb.from_user.id, cb.data)
    try:
        await cb.message.edit_text(text, reply_markup=_eco_kb(link_key))
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=_eco_kb(link_key))


@dp.callback_query(F.data == "cat_clothes")
async def cat_clothes(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "cat_clothes")
    links = load_links()
    text = "👗 <b>Колготки и нижнее бельё</b>\n\nВыбери категорию 👇"
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🧦 Колготки",       url=links["cat_tights"]))
    b.row(types.InlineKeyboardButton(text="👙 Нижнее бельё",   url=links["cat_underwear"]))
    b.row(types.InlineKeyboardButton(text="✅ Зарегистрироваться (-20%)", callback_data="go_reg"))
    b.row(types.InlineKeyboardButton(text="← Назад",           callback_data="hits_catalog"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


@dp.callback_query(F.data == "cat_kids_btn")
async def cat_kids_btn(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "cat_kids")
    links = load_links()
    text = (
        "👶 <b>Детские товары Faberlic</b>\n\n"
        "• Детская косметика — шампуни, кремы, гели для купания\n"
        "• Без парабенов, красителей и агрессивных ароматизаторов\n"
        "• Средства для ухода за кожей малышей\n\n"
        "🌿 Протестировано дерматологами, безопасно с рождения."
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛒 Открыть каталог",          url=links["cat_kids"]))
    b.row(types.InlineKeyboardButton(text="✅ Зарегистрироваться (-20%)", callback_data="go_reg"))
    b.row(types.InlineKeyboardButton(text="← Назад",                     callback_data="hits_catalog"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


@dp.callback_query(F.data == "cat_men_btn")
async def cat_men_btn(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "cat_men")
    links = load_links()
    text = (
        "👨 <b>Мужчинам от Faberlic</b>\n\n"
        "• Парфюм — стойкие ароматы от французских домов\n"
        "• Уход за лицом и телом\n"
        "• Гели для бритья и после бритья\n"
        "• Дезодоранты-антиперспиранты\n\n"
        "💎 Качество класса люкс по цене масс-маркета. Со скидкой ещё дешевле."
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🛒 Открыть каталог",          url=links["cat_men"]))
    b.row(types.InlineKeyboardButton(text="✅ Зарегистрироваться (-20%)", callback_data="go_reg"))
    b.row(types.InlineKeyboardButton(text="← Назад",                     callback_data="hits_catalog"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


@dp.callback_query(F.data == "cat_beauty")
async def cat_beauty(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "cat_beauty")
    text = (
        "💄 <b>Красота и уход — линейка OXYTOP</b>\n\n"
        "Фирменный кислородный комплекс Faberlic насыщает кожу кислородом "
        "и восстанавливает клеточный обмен.\n\n"
        "Что входит:\n"
        "• Кремы для лица (день/ночь/вокруг глаз)\n"
        "• Сыворотки и маски\n"
        "• Тональные средства с SPF\n"
        "• Средства для волос и тела\n\n"
        "🎯 Со скидкой 20–26% — реально дешевле, чем в аптеке или магазине."
    )
    try:
        await cb.message.edit_text(text, reply_markup=_catalog_kb("catalog_beauty"))
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=_catalog_kb("catalog_beauty"))


@dp.callback_query(F.data == "cat_makeup")
async def cat_makeup(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "cat_makeup")
    text = (
        "💅 <b>Декоративная косметика</b>\n\n"
        "Французские ароматы и профессиональная декоративная косметика по доступным ценам.\n\n"
        "Что входит:\n"
        "• Помады, блески, туши\n"
        "• Тени, хайлайтеры, контуринг\n"
        "• Базы и фиксаторы\n"
        "• Парфюм — от цветочных до восточных\n\n"
        "✨ Качество как у брендов — цена как у масс-маркета. Со скидкой ещё дешевле."
    )
    try:
        await cb.message.edit_text(text, reply_markup=_catalog_kb("catalog_makeup"))
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=_catalog_kb("catalog_makeup"))


# ─── БИЗНЕС-БЛОК ─────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "biz_block")
async def biz_block(cb: types.CallbackQuery):
    await cb.answer()
    links = load_links()
    bot_db.log_event(cb.from_user.id, "biz_block")
    try:
        await cb.message.delete()
    except Exception:
        pass

    await typing(cb.message.chat.id, 2.0)
    await bot.send_message(
        cb.message.chat.id,
        "💼 <b>Про бизнес простыми словами</b>\n\n"
        "Тебе не нужно открывать склад или бегать с сумками. "
        "Мы работаем по системе «Умных рекомендаций»:\n\n"
        "1️⃣ Пользуешься сам — покупаешь то, что и так берёшь в супермаркете\n"
        "2️⃣ Рекомендуешь друзьям — через соцсети или лично\n"
        "3️⃣ Получаешь вознаграждение на карту или счёт\n\n"
        "Это отличный вариант для мам в декрете, студентов или как подработка к основной работе. "
        "Мы обучаем всему бесплатно!",
    )

    await typing(cb.message.chat.id, 2.0)
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="📊 Посмотреть кейсы доходов",   callback_data="biz_cases"))
    b.row(types.InlineKeyboardButton(text="✅ Пройти регистрацию",          callback_data="go_reg"))
    b.row(types.InlineKeyboardButton(text="💬 Написать Венере о бизнесе",   url=links["venera_tg"]))
    await bot.send_message(
        cb.message.chat.id,
        "📈 <b>Реальные цифры</b>\n\n"
        "• 5–10 рекомендаций в месяц → 2 000–5 000 ₽\n"
        "• Активная структура → 15 000–40 000 ₽\n"
        "• Уровень наставника → 60 000–150 000+ ₽\n\n"
        "Без обязательных закупок. Работаешь в своём темпе, в телефоне.",
        reply_markup=b.as_markup(),
    )


@dp.callback_query(F.data == "biz_cases")
async def biz_cases(cb: types.CallbackQuery):
    await cb.answer()
    bot_db.log_event(cb.from_user.id, "biz_cases")
    text = (
        "📊 <b>Истории участников Faberlic</b>\n\n"
        "👩 <b>Марина, мама двоих детей, Казань</b>\n"
        "Начинала с покупок для семьи. Через 3 месяца рекомендаций — 12 000 ₽/мес доп. дохода. "
        "Сейчас обучает свою команду.\n\n"
        "👩 <b>Алина, студентка, Уфа</b>\n"
        "Зарегистрировалась ради скидки. Рассказала трём подругам в Instagram. "
        "Через месяц — первые 4 500 ₽ на карту.\n\n"
        "👩 <b>Светлана, учитель, Екатеринбург</b>\n"
        "Работает 2–3 часа в день в телефоне. Доход 35 000 ₽/мес. "
        "«Это не продажи — это просто рекомендации того, чем пользуюсь сама.»\n\n"
        "Хочешь так же? Начни с регистрации — это бесплатно 💎"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="✅ Пройти регистрацию", callback_data="go_reg"))
    b.row(types.InlineKeyboardButton(text="← Назад",              callback_data="biz_block"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


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


# ─── FAQ ─────────────────────────────────────────────────────────────────────

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


# ─── СБОР ДАННЫХ (FSM) — need_help / reg_failed ──────────────────────────────

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
    fio = (message.text or "").strip()[:120]
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
    digits = re.sub(r"\D", "", phone_raw)
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

    data = await state.get_data()
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


# ─── /help ───────────────────────────────────────────────────────────────────

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    links = load_links()
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


# ─── FALLBACK ────────────────────────────────────────────────────────────────

@dp.message(F.text & ~F.text.startswith("/"))
async def fallback(message: types.Message, state: FSMContext):
    bot_db.log_event(message.from_user.id, "freeform", (message.text or "")[:200])
    current = await state.get_state()
    if current:
        await message.answer("Нажми кнопку выше или /start чтобы начать сначала 💎")
        return
    links = load_links()
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


# ─── ADMIN PANEL ─────────────────────────────────────────────────────────────

def admin_main_kb():
    b = InlineKeyboardBuilder()
    b.row(
        types.InlineKeyboardButton(text="👥 Мои клиенты",   callback_data="adm_leads"),
        types.InlineKeyboardButton(text="📊 Статистика",     callback_data="adm_stats"),
    )
    b.row(
        types.InlineKeyboardButton(text="🖼 Фото в боте",    callback_data="adm_media"),
        types.InlineKeyboardButton(text="🔗 Ссылки",         callback_data="adm_links"),
    )
    b.row(
        types.InlineKeyboardButton(text="📢 Рассылка",       callback_data="adm_broadcast"),
        types.InlineKeyboardButton(text="❓ Помощь",         callback_data="adm_help"),
    )
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

    s = bot_db.funnel_stats()
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


@dp.callback_query(F.data == "adm_links")
async def adm_links(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    links = load_links()

    def short(url):
        return url[:40] + "…" if len(url) > 40 else url

    eco_keys = ["cat_laundry","cat_shoes","cat_dishes","cat_bathroom","cat_kitchen","cat_surfaces","cat_aroma"]
    eco_labels = ["Стирка","Обувь","Посуда","Ванная","Кухня","Поверхности","Ароматизация"]
    eco_lines = "\n".join(f"{l}: <code>{short(links.get(k,'—'))}</code>" for k,l in zip(eco_keys,eco_labels))

    text = (
        "🔗 <b>Текущие ссылки</b>\n\n"
        f"🔑 Реф. регистрация: <code>{short(links['reg'])}</code>\n\n"
        f"💄 Косметика: <code>{short(links['catalog_beauty'])}</code>\n"
        f"💅 Макияж: <code>{short(links['catalog_makeup'])}</code>\n\n"
        f"🏠 Эко-дом категории:\n{eco_lines}\n\n"
        f"👗 Колготки: <code>{short(links.get('cat_tights','—'))}</code>\n"
        f"👙 Бельё: <code>{short(links.get('cat_underwear','—'))}</code>\n"
        f"👶 Детское: <code>{short(links.get('cat_kids','—'))}</code>\n"
        f"👨 Мужское: <code>{short(links.get('cat_men','—'))}</code>"
    )
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🔑 Реф. ссылка (регистрация)",  callback_data="adm_set_reg"))
    b.row(types.InlineKeyboardButton(text="💄 Каталог косметики",           callback_data="adm_set_beauty"))
    b.row(types.InlineKeyboardButton(text="💅 Каталог макияжа",             callback_data="adm_set_makeup"))
    b.row(types.InlineKeyboardButton(text="🏠 Эко-дом: ссылки категорий",  callback_data="adm_links_eco"))
    b.row(types.InlineKeyboardButton(text="👗 Одежда / бельё / дети / муж", callback_data="adm_links_other"))
    b.row(types.InlineKeyboardButton(text="← Назад",                        callback_data="adm_main"))
    try:
        await cb.message.edit_text(text, reply_markup=b.as_markup())
    except Exception:
        await bot.send_message(cb.message.chat.id, text, reply_markup=b.as_markup())


LINK_KEY_NAMES = {
    "adm_set_beauty":    ("catalog_beauty",  "каталог косметики"),
    "adm_set_health":    ("catalog_health",  "каталог эко-дом"),
    "adm_set_makeup":    ("catalog_makeup",  "каталог макияжа"),
    "adm_set_reg":       ("reg",             "реферальная ссылка"),
    "adm_set_laundry":   ("cat_laundry",     "стирка и одежда"),
    "adm_set_shoes":     ("cat_shoes",       "уход за обувью"),
    "adm_set_dishes":    ("cat_dishes",      "уход за посудой"),
    "adm_set_bathroom":  ("cat_bathroom",    "ванная и туалет"),
    "adm_set_kitchen":   ("cat_kitchen",     "уход за кухней"),
    "adm_set_surfaces":  ("cat_surfaces",    "уход за поверхностями"),
    "adm_set_aroma":     ("cat_aroma",       "ароматизация воздуха"),
    "adm_set_tights":    ("cat_tights",      "колготки"),
    "adm_set_underwear": ("cat_underwear",   "нижнее бельё"),
    "adm_set_kids":      ("cat_kids",        "детские товары"),
    "adm_set_men":       ("cat_men",         "мужской каталог"),
}


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


@dp.callback_query(F.data == "adm_links_eco")
async def adm_links_eco(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="👕 Стирка",          callback_data="adm_set_laundry"))
    b.row(types.InlineKeyboardButton(text="👟 Обувь",           callback_data="adm_set_shoes"))
    b.row(types.InlineKeyboardButton(text="🍽 Посуда",          callback_data="adm_set_dishes"))
    b.row(types.InlineKeyboardButton(text="🚿 Ванная",          callback_data="adm_set_bathroom"))
    b.row(types.InlineKeyboardButton(text="🍳 Кухня",           callback_data="adm_set_kitchen"))
    b.row(types.InlineKeyboardButton(text="✨ Поверхности",     callback_data="adm_set_surfaces"))
    b.row(types.InlineKeyboardButton(text="🌸 Ароматизация",    callback_data="adm_set_aroma"))
    b.row(types.InlineKeyboardButton(text="← Назад",           callback_data="adm_links"))
    await cb.message.edit_text("🏠 <b>Ссылки Эко-дом</b>\n\nВыбери категорию для обновления:", reply_markup=b.as_markup())


@dp.callback_query(F.data == "adm_links_other")
async def adm_links_other(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    b = InlineKeyboardBuilder()
    b.row(types.InlineKeyboardButton(text="🧦 Колготки",      callback_data="adm_set_tights"))
    b.row(types.InlineKeyboardButton(text="👙 Нижнее бельё",  callback_data="adm_set_underwear"))
    b.row(types.InlineKeyboardButton(text="👶 Детское",       callback_data="adm_set_kids"))
    b.row(types.InlineKeyboardButton(text="👨 Мужское",       callback_data="adm_set_men"))
    b.row(types.InlineKeyboardButton(text="← Назад",         callback_data="adm_links"))
    await cb.message.edit_text("👗 <b>Ссылки: одежда и другие категории</b>\n\nВыбери что обновить:", reply_markup=b.as_markup())


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
    b.row(types.InlineKeyboardButton(text="📥 Скачать всех в Excel",  callback_data="adm_export"))
    b.row(types.InlineKeyboardButton(text="← Назад",                   callback_data="adm_main"))
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


@dp.callback_query(F.data == "adm_media")
async def adm_media(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Нет доступа")
        return
    await cb.answer()
    MEDIA_DIR.mkdir(exist_ok=True)
    files = list(MEDIA_DIR.iterdir())
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
    slot = "venera_intro" if is_video else "venera"
    ext = "mp4" if is_video else "jpg"
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
    data = await state.get_data()
    slot = data.get("photo_slot", "media")
    ext = data.get("photo_ext", "jpg")
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
    fname = cb.data.removeprefix("adm_delfile_")
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
        await cb.message.edit_text("Файл не найден.", reply_markup=(InlineKeyboardBuilder().row(
            types.InlineKeyboardButton(text="← Назад", callback_data="adm_media")
        ).as_markup()))


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
        "Можно скачать таблицу в Excel.\n\n"
        "📊 <b>Статистика</b> — сколько людей зашло, сколько нажало зарегистрироваться.\n\n"
        "🖼 <b>Фото в боте</b> — поменяй своё фото или видео, которое показывается при /start.\n\n"
        "🔗 <b>Ссылки</b> — обнови реферальные ссылки на каталоги. "
        "Нужно менять каждый каталог.\n\n"
        "📢 <b>Рассылка</b> — отправь сообщение сразу всем, кто писал боту.\n\n"
        "Если что-то не работает — напиши Раилю 💬",
        reply_markup=b.as_markup(),
    )


# ─── /stats и /reset (для обратной совместимости) ────────────────────────────

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
                name = u.get("first_name") or "привет"
                try:
                    await bot.send_message(
                        u["tg_id"],
                        f"Привет! Ты ещё не зарегистрировался(ась)? 💎\n\n"
                        f"Я помогу — займёт 2 минуты 👇",
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
    load_links()  # создаст links.json с дефолтами если не существует
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("BOT Венера запущен")
    asyncio.create_task(reminder_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())