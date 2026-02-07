# ==================================================
# bot.py — PART 1
# PostgreSQL (Render.com, FREE PLAN)
# ==================================================

import asyncio
import logging
import os
import random
import re
import uuid

import asyncpg
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")  # Render ENV

ADMIN_IDS = [
    1945747968,6928797177
]

PAYMENT_CARDS = [
    "5355 2800 2484 3821"
    "5232 4410 2403 2182"
]

DATABASE_URL = os.getenv("DATABASE_URL")

router = Router()
db_pool: asyncpg.Pool | None = None

# --------------------------------------------------
# DATABASE INIT (Render-safe)
# --------------------------------------------------

async def init_db():
    global db_pool

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        ssl="require",
        min_size=1,
        max_size=3
    )

    async with db_pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_seen TIMESTAMP DEFAULT NOW(),
            referrer_id BIGINT,
            referral_count INTEGER DEFAULT 0,
            has_purchased BOOLEAN DEFAULT FALSE,
            blocked_bot BOOLEAN DEFAULT FALSE
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id BIGINT PRIMARY KEY
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            category_name TEXT,
            product_name TEXT,
            weight TEXT,
            price INTEGER
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id UUID PRIMARY KEY,
            short_id TEXT,
            user_id BIGINT,
            product TEXT,
            weight TEXT,
            price INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)

        await conn.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            discount_percent INTEGER,
            is_reusable BOOLEAN,
            owner_id BIGINT
        );
        """)

# --------------------------------------------------
# USERS / BLACKLIST (PostgreSQL)
# --------------------------------------------------

async def add_user_to_db(user_id: int, username: str | None, referrer_id: int | None):
    async with db_pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO users (user_id, username, referrer_id)
        VALUES ($1,$2,$3)
        ON CONFLICT (user_id) DO NOTHING
        """, user_id, username, referrer_id)

async def get_user_data_db(user_id: int) -> dict | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id=$1",
            user_id
        )
        return dict(row) if row else None

async def set_user_has_purchased(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET has_purchased=TRUE WHERE user_id=$1",
            user_id
        )

async def increment_referrer_count(user_id: int) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
        UPDATE users
        SET referral_count = referral_count + 1
        WHERE user_id=$1
        RETURNING referral_count
        """, user_id)
        return row["referral_count"]

async def reset_referral_count(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET referral_count=0 WHERE user_id=$1",
            user_id
        )

async def add_to_blacklist_db(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO blacklist (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
            user_id
        )

async def remove_from_blacklist_db(user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM blacklist WHERE user_id=$1",
            user_id
        )
        return result.endswith("1")

async def is_user_blacklisted(user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM blacklist WHERE user_id=$1",
            user_id
        )
        return row is not None
        # ==================================================
# bot.py — PART 2
# Products / Catalog / Orders / FSM
# ==================================================

# --------------------------------------------------
# FSM STATES
# --------------------------------------------------

class BuyFSM(StatesGroup):
    choosing_category = State()
    choosing_product = State()
    confirming_order = State()
    entering_promo = State()

# --------------------------------------------------
# PRODUCTS (DB)
# --------------------------------------------------

async def add_product_db(category: str, name: str, weight: str, price: int):
    async with db_pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO products (category_name, product_name, weight, price)
        VALUES ($1,$2,$3,$4)
        """, category, name, weight, price)

async def get_categories_db() -> list[str]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
        SELECT DISTINCT category_name FROM products
        ORDER BY category_name
        """)
        return [r["category_name"] for r in rows]

async def get_products_by_category_db(category: str) -> list[dict]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
        SELECT * FROM products
        WHERE category_name=$1
        ORDER BY id
        """, category)
        return [dict(r) for r in rows]

# --------------------------------------------------
# ORDERS (DB)
# --------------------------------------------------

async def create_order_db(
    user_id: int,
    product_name: str,
    weight: str,
    price: int
) -> tuple[str, str]:

    order_id = uuid.uuid4()
    short_id = str(order_id).split("-")[0]

    async with db_pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO orders (order_id, short_id, user_id, product, weight, price)
        VALUES ($1,$2,$3,$4,$5,$6)
        """, order_id, short_id, user_id, product_name, weight, price)

    return str(order_id), short_id

async def get_order_by_short_id(short_id: str) -> dict | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE short_id=$1",
            short_id
        )
        return dict(row) if row else None

# --------------------------------------------------
# START / MENU
# --------------------------------------------------

@router.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    if await is_user_blacklisted(message.from_user.id):
        return

    ref_id = None
    if message.text and len(message.text.split()) > 1:
        try:
            ref_id = int(message.text.split()[1])
        except:
            pass

    await add_user_to_db(
        message.from_user.id,
        message.from_user.username,
        ref_id
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 Каталог", callback_data="catalog")
    kb.button(text="ℹ️ Профиль", callback_data="profile")
    kb.adjust(1)

    await message.answer(
        "Добро пожаловать 👋\nВыберите действие:",
        reply_markup=kb.as_markup()
    )

# --------------------------------------------------
# CATALOG
# --------------------------------------------------

@router.callback_query(F.data == "catalog")
async def catalog_cb(call: types.CallbackQuery, state: FSMContext):
    categories = await get_categories_db()

    kb = InlineKeyboardBuilder()
    for c in categories:
        kb.button(text=c, callback_data=f"cat:{c}")
    kb.adjust(1)

    await state.set_state(BuyFSM.choosing_category)

    await call.message.edit_text(
        "📦 Выберите категорию:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(
    BuyFSM.choosing_category,
    F.data.startswith("cat:")
)
async def choose_category(call: types.CallbackQuery, state: FSMContext):
    category = call.data.split(":", 1)[1]
    products = await get_products_by_category_db(category)

    kb = InlineKeyboardBuilder()
    for p in products:
        kb.button(
            text=f"{p['product_name']} | {p['weight']} — {p['price']}₽",
            callback_data=f"prod:{p['id']}"
        )
    kb.adjust(1)

    await state.update_data(category=category)
    await state.set_state(BuyFSM.choosing_product)

    await call.message.edit_text(
        f"📂 Категория: {category}\nВыберите товар:",
        reply_markup=kb.as_markup()
    )

@router.callback_query(
    BuyFSM.choosing_product,
    F.data.startswith("prod:")
)
async def choose_product(call: types.CallbackQuery, state: FSMContext):
    product_id = int(call.data.split(":")[1])

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM products WHERE id=$1",
            product_id
        )

    if not row:
        await call.answer("Товар не найден", show_alert=True)
        return

    product = dict(row)
    await state.update_data(product=product)

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Оформить заказ", callback_data="confirm_order")
    kb.button(text="❌ Отмена", callback_data="cancel_buy")
    kb.adjust(1)

    await state.set_state(BuyFSM.confirming_order)

    await call.message.edit_text(
        f"🛒 Товар:\n"
        f"{product['product_name']}\n"
        f"Вес: {product['weight']}\n"
        f"Цена: {product['price']}₽",
        reply_markup=kb.as_markup()
    )

@router.callback_query(F.data == "cancel_buy")
async def cancel_buy(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ Покупка отменена")

# --------------------------------------------------
# CONFIRM ORDER
# --------------------------------------------------

@router.callback_query(BuyFSM.confirming_order, F.data == "confirm_order")
async def confirm_order(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    product = data["product"]

    order_id, short_id = await create_order_db(
        call.from_user.id,
        product["product_name"],
        product["weight"],
        product["price"]
    )

    await set_user_has_purchased(call.from_user.id)

    text = (
        "✅ Заказ создан!\n\n"
        f"🆔 Номер: {short_id}\n"
        f"📦 {product['product_name']}\n"
        f"⚖️ {product['weight']}\n"
        f"💰 {product['price']}₽\n\n"
        "💳 Оплатите на карту:\n"
        f"{random.choice(PAYMENT_CARDS)}\n\n"
        "После оплаты ожидайте подтверждение."
    )

    await state.clear()
    await call.message.edit_text(text)
    # ==================================================
# bot.py — PART 3
# Admin panel / Orders / Promo / Referrals
# ==================================================

# --------------------------------------------------
# ADMIN FILTER
# --------------------------------------------------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# --------------------------------------------------
# PROMO CODES (DB)
# --------------------------------------------------

async def create_promo_db(
    code: str,
    discount: int,
    reusable: bool,
    owner_id: int | None
):
    async with db_pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO promo_codes (code, discount_percent, is_reusable, owner_id)
        VALUES ($1,$2,$3,$4)
        """, code, discount, reusable, owner_id)

async def get_promo_db(code: str) -> dict | None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM promo_codes WHERE code=$1",
            code
        )
        return dict(row) if row else None

async def delete_promo_db(code: str):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM promo_codes WHERE code=$1",
            code
        )

# --------------------------------------------------
# ADMIN MENU
# --------------------------------------------------

@router.message(Command("admin"))
async def admin_menu(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    kb = InlineKeyboardBuilder()
    kb.button(text="📦 Заказы", callback_data="admin_orders")
    kb.button(text="🎁 Промокод", callback_data="admin_promo")
    kb.button(text="📊 Статистика", callback_data="admin_stats")
    kb.button(text="⛔ Блокировки", callback_data="admin_blacklist")
    kb.adjust(1)

    await message.answer("🔐 Админ-панель:", reply_markup=kb.as_markup())

# --------------------------------------------------
# ADMIN ORDERS
# --------------------------------------------------

async def get_pending_orders_db() -> list[dict]:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
        SELECT * FROM orders
        WHERE status='pending'
        ORDER BY created_at
        """)
        return [dict(r) for r in rows]

async def update_order_status_db(order_id: str, status: str):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE orders SET status=$1 WHERE order_id=$2",
            status, uuid.UUID(order_id)
        )

@router.callback_query(F.data == "admin_orders")
async def admin_orders(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return

    orders = await get_pending_orders_db()

    if not orders:
        await call.message.edit_text("📦 Нет ожидающих заказов")
        return

    for o in orders:
        kb = InlineKeyboardBuilder()
        kb.button(
            text="✅ Подтвердить",
            callback_data=f"order_ok:{o['order_id']}"
        )
        kb.button(
            text="❌ Отклонить",
            callback_data=f"order_no:{o['order_id']}"
        )
        kb.adjust(2)

        await call.message.answer(
            f"🆔 {o['short_id']}\n"
            f"👤 {o['user_id']}\n"
            f"📦 {o['product']}\n"
            f"⚖️ {o['weight']}\n"
            f"💰 {o['price']}₽",
            reply_markup=kb.as_markup()
        )

@router.callback_query(F.data.startswith("order_ok:"))
async def order_confirm(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return

    order_id = call.data.split(":")[1]
    order = await get_order_by_short_id(order_id[:8])

    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return

    await update_order_status_db(order["order_id"], "confirmed")

    try:
        await call.bot.send_message(
            order["user_id"],
            f"✅ Ваш заказ {order['short_id']} подтверждён!"
        )
    except:
        pass

    await call.message.edit_text("✅ Заказ подтверждён")

@router.callback_query(F.data.startswith("order_no:"))
async def order_reject(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return

    order_id = call.data.split(":")[1]
    order = await get_order_by_short_id(order_id[:8])

    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return

    await update_order_status_db(order["order_id"], "rejected")

    try:
        await call.bot.send_message(
            order["user_id"],
            f"❌ Заказ {order['short_id']} отклонён"
        )
    except:
        pass

    await call.message.edit_text("❌ Заказ отклонён")

# --------------------------------------------------
# PROMO FSM
# --------------------------------------------------

class PromoFSM(StatesGroup):
    waiting_code = State()
    waiting_discount = State()

@router.callback_query(F.data == "admin_promo")
async def admin_promo(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return

    await state.set_state(PromoFSM.waiting_code)
    await call.message.edit_text("Введите код промокода:")

@router.message(PromoFSM.waiting_code)
async def promo_code_input(message: types.Message, state: FSMContext):
    await state.update_data(code=message.text.strip())
    await state.set_state(PromoFSM.waiting_discount)
    await message.answer("Введите процент скидки:")

@router.message(PromoFSM.waiting_discount)
async def promo_discount_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    discount = int(message.text)

    await create_promo_db(
        code=data["code"],
        discount=discount,
        reusable=True,
        owner_id=message.from_user.id
    )

    await state.clear()
    await message.answer("🎁 Промокод создан")

# --------------------------------------------------
# STATS / REFERRALS
# --------------------------------------------------

@router.callback_query(F.data == "admin_stats")
async def admin_stats(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return

    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        orders = await conn.fetchval("SELECT COUNT(*) FROM orders")
        confirmed = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE status='confirmed'"
        )

    await call.message.edit_text(
        f"📊 Статистика:\n\n"
        f"👤 Пользователей: {users}\n"
        f"📦 Заказов: {orders}\n"
        f"✅ Подтверждено: {confirmed}"
    )

# --------------------------------------------------
# BLACKLIST (ADMIN)
# --------------------------------------------------

@router.callback_query(F.data == "admin_blacklist")
async def admin_blacklist(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return

    await call.message.edit_text(
        "Используйте команды:\n"
        "/ban <id>\n"
        "/unban <id>"
    )

@router.message(Command("ban"))
async def ban_user(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    try:
        uid = int(message.text.split()[1])
    except:
        return

    await add_to_blacklist_db(uid)
    await message.answer("⛔ Пользователь заблокирован")

@router.message(Command("unban"))
async def unban_user(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    try:
        uid = int(message.text.split()[1])
    except:
        return

    if await remove_from_blacklist_db(uid):
        await message.answer("✅ Пользователь разблокирован")
    else:
        await message.answer("Пользователь не был в блоке")
        # ==================================================
# bot.py — PART 4
# Support / Captcha / Middleware / Bot start
# ==================================================

# --------------------------------------------------
# CAPTCHA
# --------------------------------------------------

CAPTCHA_ANSWERS = {}

async def send_captcha(message: types.Message) -> bool:
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    CAPTCHA_ANSWERS[message.from_user.id] = a + b
    await message.answer(f"🤖 Проверка:\nСколько будет {a} + {b}?")
    return True

@router.message()
async def captcha_check(message: types.Message):
    if message.from_user.id not in CAPTCHA_ANSWERS:
        return

    try:
        if int(message.text) == CAPTCHA_ANSWERS[message.from_user.id]:
            del CAPTCHA_ANSWERS[message.from_user.id]
            await message.answer("✅ Проверка пройдена")
        else:
            await message.answer("❌ Неверно, попробуй ещё раз")
    except:
        await message.answer("❌ Введи число")

# --------------------------------------------------
# SUPPORT (USER → ADMIN)
# --------------------------------------------------

SUPPORT_SESSIONS = {}

@router.message(Command("support"))
async def support_start(message: types.Message):
    if await is_user_blacklisted(message.from_user.id):
        return

    SUPPORT_SESSIONS[message.from_user.id] = True
    await message.answer(
        "✉️ Напишите сообщение, оно будет отправлено администратору.\n"
        "Для выхода — /stop"
    )

@router.message(Command("stop"))
async def support_stop(message: types.Message):
    SUPPORT_SESSIONS.pop(message.from_user.id, None)
    await message.answer("❌ Поддержка завершена")

@router.message()
async def support_forward(message: types.Message):
    if message.from_user.id not in SUPPORT_SESSIONS:
        return

    for admin_id in ADMIN_IDS:
        try:
            await message.forward(admin_id)
        except:
            pass

# --------------------------------------------------
# SUPPORT (ADMIN → USER)
# --------------------------------------------------

@router.message(F.reply_to_message)
async def admin_reply(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    try:
        original = message.reply_to_message.forward_from
        if not original:
            return

        await message.bot.send_message(
            original.id,
            f"💬 Ответ от поддержки:\n{message.text}"
        )
    except:
        pass

# --------------------------------------------------
# MIDDLEWARE (BLOCK CHECK)
# --------------------------------------------------

@router.message()
@router.callback_query()
async def block_middleware(event):
    uid = event.from_user.id
    if await is_user_blacklisted(uid):
        return

# --------------------------------------------------
# BOT STARTUP
# --------------------------------------------------

async def main():
    logging.basicConfig(level=logging.INFO)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await init_db()

    logging.info("Bot started (PostgreSQL / Render)")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
