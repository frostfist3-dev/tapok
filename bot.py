import asyncio
import logging
import re
import uuid 
import random 
import sqlite3 # Для базы данных
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import CommandStart, StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Dict, Any, Callable, Awaitable
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError # Для рассылки
from aiogram.types import BotCommand, TelegramObject # Для установки меню
from aiogram.enums import ChatAction # <-- НОВЕ: Для "печатает..."
from aiogram.fsm.storage.memory import MemoryStorage # <-- НОВЕ: Для FSM

# --- Конфигурация ---
# !!!
# СРОЧНО ЗАМЕНИТЕ ВАШ СТАРЫЙ ТОКЕН НА НОВЫЙ ОТ @BotFather !!!
BOT_TOKEN = "8379189056:AAGiKI8sdhDSohWBtN24fRZa-AjHaCmftKw" 
REVIEWS_LINK = "https://t.me/+s-AyottAPJJlMWUy"

# --- АДМИНИСТРАТОРЫ ---
ADMIN_IDS = [
    1945747968,   # Ваш ID (Основной админ)
    8284390315    # <<< ID ВТОРОГО АДМИНА.
]

# === НОВОЕ: Список карт для оплаты ===
PAYMENT_CARDS = [
    "5355 2800 2484 3821",  
    "5232 4410 2403 2182",
    "5232 4410 2618 5616"
]

# === НОВОЕ: Города для кладов ===
# === ОБНОВЛЕНО: Города и Районы для кладов ===
# Структура: { "Область": { "Город": ["Район1", "Район2"] } }
KLAD_LOCATIONS = {
    "Київська обл": {
        "Київ": ["Дарницький", "Шевченківський", "Подільський", "Оболонський", "Деснянський", "Печерський"],
        "Біла Церква": ["Центр", "Заріччя", "Вокзальний"],
        "Бровари": ["Центр", "Старе місто", "Розвилка", "Лісовий"],
        "Бориспіль": ["Центр", "Нестерівка", "Промзона"],
        "Ірпінь": ["Центр", "Набережна"]
    },
    "Житомирська обл": {
        "Житомир": ["Корольовський р-н", "Окраїна"],
        "Бердичів": ["Центр", "Окраїна"]
    },
    "Хмельницька обл": {
        "Хмельницький": ["Центр", "Заріччя", "Окраїна"],
        "Кам’янець-Подільський": ["Центр", "Окраїна"],
        "Шепетівка": ["Окраїна"],
        "Нетішин": ["Центр", "Окраїна"]
    },
    "Тернопільська обл": {
        "Тернопіль": ["Центр", "Окраїна"]
    },
    "Дніпропетровська обл": {
        "Дніпро": ["Шевченківський", "Центральний", "Окраїна"],
        "Кривий Ріг": ["Покровський", "Окраїна"],
        "Павлоград": ["Окраїна"]
    },
    "Кіровоградська обл": {
        "Кропивницький": ["Подільський", "Окраїна"]
    },
    "Миколаївська обл": {
        "Миколаїв": ["Центральний", "Заводський", "Корабельний", "Окраїна"]
    },
    # --- Старые области (добавил стандартные районы) ---
    "Рівненська обл": {
        "Рівне": ["Центр", "Автовокзал", "Північний"],
        "Дубно": ["Центр", "Окраїна"],
        "Вараш": ["Центр", "Окраїна"],
        "Сарни": ["Центр", "Окраїна"]
    },
    "Волинська обл": {
        "Луцьк": ["Центр", "33-й район", "Вокзал"],
        "Ковель": ["Центр", "Окраїна"],
        "Нововолинськ": ["Центр", "Окраїна"]
    },
    "Львівська обл": {
        "Львів": ["Галицький", "Личаківський", "Сихівський", "Залізничний"],
        "Дрогобич": ["Центр", "Окраїна"],
        "Червоноград": ["Центр", "Окраїна"],
        "Стрий": ["Центр", "Окраїна"]
    },
    "Закарпатскька обл": {
        "Мукачево": ["Центр", "Окраїна"],
        "Ужгород": ["Центр", "Окраїна"]
    }
}



# ----------------------------------------------------------------------
# --- ЛОГИКА: Настройка Базы Данных SQLite ---
# ----------------------------------------------------------------------
DB_FILE = "shop.db" # Файл нашей базы данных

def init_db():
    """Инициализирует (создает) таблицы в базе данных, если их нет"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        
        # Таблица пользователей (для статистики, капчи и рефералов)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            referrer_id INTEGER,
            referral_count INTEGER DEFAULT 0,
            has_purchased INTEGER DEFAULT 0,
            referral_reward_claimed INTEGER DEFAULT 0,
            blocked_bot INTEGER DEFAULT 0 
        )
        """)
        
        # Таблица заказов (для админки)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            short_id TEXT,
            user_id INTEGER,
            username TEXT,
            product TEXT,
            weight TEXT,
            original_price INTEGER,
            final_price INTEGER,
            promo_code_used TEXT,
            contact_info TEXT,
            check_file_id TEXT,
            status TEXT DEFAULT 'pending', -- pending, confirmed, declined
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Таблица Промокодов (обычные + реферальные)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY NOT NULL UNIQUE,
            discount_percent INTEGER NOT NULL,
            is_reusable INTEGER DEFAULT 1, -- 1 = да (как SALE15), 0 = нет (как реферальный)
            owner_id INTEGER -- Для отслеживания, кто получил реф.
        )
        """)
        
        # !!! НОВАЯ ТАБЛИЦА: Товары !!!
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_name TEXT NOT NULL,
            product_name TEXT NOT NULL,
            weight TEXT NOT NULL,
            price INTEGER NOT NULL
        )
        """)
        
        # === НОВАЯ ТАБЛИЦА: Черный список ===
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        conn.commit()

        # !!! НОВОЕ: Заполняем БД, если она пустая
        populate_initial_products()

# --- Функции для работы с БД (Пользователи) ---
def add_user_to_db(user_id: int, username: str, referrer_id: int | None = None):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)", 
            (user_id, username, referrer_id)
        )
        conn.commit()

def is_user_verified(user_id: int) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

def get_user_data_db(user_id: int) -> dict | None:
    """Получает все данные о пользователе"""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_user_count() -> int:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(user_id) FROM users")
        return cursor.fetchone()[0]

def get_all_user_ids_db() -> list[int]:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        return [row[0] for row in cursor.fetchall()]

def get_user_id_by_username(username: str) -> int | None:
    """Ищет ID пользователя по юзернейму в БД"""
    clean_username = username.replace("@", "").strip()
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE username LIKE ?", (clean_username,))
        result = cursor.fetchone()
        return result[0] if result else None

# --- Новые функции для Рефералов и Покупок ---
def set_user_has_purchased(user_id: int):
    """Отмечает, что юзер совершил первую покупку"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET has_purchased = 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def increment_referrer_count(referrer_id: int) -> int:
    """Увеличивает счетчик рефералов у спонсора и возвращает НОВЫЙ счетчик"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (referrer_id,))
        conn.commit()
        
        # Получаем новый счетчик
        cursor.execute("SELECT referral_count FROM users WHERE user_id = ?", (referrer_id,))
        result = cursor.fetchone()
        return result[0] if result else 0

def reset_referral_count(user_id: int):
    """Сбрасывает счетчик (после выдачи приза)"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET referral_count = 0, referral_reward_claimed = referral_reward_claimed + 1 WHERE user_id = ?", (user_id,))
        conn.commit()

# --- НОВЫЕ: Функции для Статистики Блокировок ---
def set_user_blocked_bot_db(user_id: int):
    """Отмечает, что юзер заблокировал бота (во время рассылки)"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET blocked_bot = 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def get_blocked_bot_count_db() -> int:
    """Получает кол-во юзеров, заблокировавших бота"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE blocked_bot = 1")
        return cursor.fetchone()[0]

# --- НОВЫЕ: Функции для Черного Списка ---
def add_to_blacklist_db(user_id: int, reason: str = 'Blocked by admin') -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT OR REPLACE INTO blacklist (user_id, reason) VALUES (?, ?)", (user_id, reason))
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error adding to blacklist: {e}")
            return False

def remove_from_blacklist_db(user_id: int) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0

def is_user_blacklisted_db(user_id: int) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

def get_blocked_user_count_db() -> int:
    """Получает кол-во юзеров, заблокированных админом"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM blacklist")
        return cursor.fetchone()[0]

# --- Функции для работы с БД (Заказы) ---
def create_db_order(order_data: dict) -> str:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO orders (order_id, short_id, user_id, username, product, weight, 
                            original_price, final_price, promo_code_used, 
                            contact_info, check_file_id, status)
        VALUES (:order_id, :short_id, :user_id, :username, :product, :weight, 
                :original_price, :final_price, :promo_code_used,
                :contact_info, :check_file_id, 'pending')
        """, order_data)
        conn.commit()
    return order_data['short_id']

def get_pending_orders_db() -> list:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row 
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at ASC")
        return [dict(row) for row in cursor.fetchall()]

def get_order_db(order_id: str) -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_order_status_db(order_id: str, status: str) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))
        conn.commit()
        return cursor.rowcount > 0

# --- Функции для работы с БД (Промокоды) ---
def add_promo_db(code: str, percent: int, is_reusable: bool = True, owner_id: int | None = None) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR REPLACE INTO promo_codes (code, discount_percent, is_reusable, owner_id) VALUES (?, ?, ?, ?)", 
                (code.upper(), percent, int(is_reusable), owner_id)
            )
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error adding promo: {e}")
            return False

def del_promo_db(code: str) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM promo_codes WHERE code = ?", (code.upper(),))
        conn.commit()
        return cursor.rowcount > 0 

def get_promo_db(code: str) -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM promo_codes WHERE code = ?", (code.upper(),))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_all_promos_db() -> list:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM promo_codes")
        return [dict(row) for row in cursor.fetchall()]

# --- НОВАЯ ФУНКЦИЯ: Автозаполнение товаров ---
def populate_initial_products():
    """
    Заполняет базу данных товарами из кода. 
    Это решит проблему удаления данных на Render.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        
        # Проверяем, пуста ли база. Если пуста - заполняем.
        cursor.execute("SELECT COUNT(*) FROM products")
        if cursor.fetchone()[0] > 0:
            return

        logging.info("База пуста. Загружаю товары из кода...")

        # Список товаров: (Категория, Название, Вес, Цена)
        FIXED_PRODUCTS = [
            # Шишки АК-47 (ИНДИКА)
            ("Шишки", "Шишки АК-47 (ИНДИКА)", "1.0г", 400),
            ("Шишки", "Шишки АК-47 (ИНДИКА)", "2.0г", 800),
            ("Шишки", "Шишки АК-47 (ИНДИКА)", "3.0г", 1050),
            ("Шишки", "Шишки АК-47 (ИНДИКА)", "5.0г", 1600),
            ("Шишки", "Шишки АК-47 (ИНДИКА)", "7.0г", 2100),

            # Шишки АК-47 (САТИВА)
            ("Шишки", "Шишки АК-47 (САТИВА)", "1.0г", 450),
            ("Шишки", "Шишки АК-47 (САТИВА)", "2.0г", 900),
            ("Шишки", "Шишки АК-47 (САТИВА)", "3.0г", 1200),
            ("Шишки", "Шишки АК-47 (САТИВА)", "5.0г", 1850),
            ("Шишки", "Шишки АК-47 (САТИВА)", "7.0г", 2400),

            # Гашиш АФГАН
            ("Гашиш", "Гашиш АФГАН", "1.0г", 500),
            ("Гашиш", "Гашиш АФГАН", "2.0г", 1000),
            ("Гашиш", "Гашиш АФГАН", "3.0г", 1350),
            ("Гашиш", "Гашиш АФГАН", "5.0г", 2100),
            ("Гашиш", "Гашиш АФГАН", "7.0г", 2700),

            # Киф АФГАН
            ("Киф", "Киф АФГАН", "1.0г", 600),
            ("Киф", "Киф АФГАН", "2.0г", 1200),
            ("Киф", "Киф АФГАН", "3.0г", 1600),
            ("Киф", "Киф АФГАН", "5.0г", 2500),
            ("Киф", "Киф АФГАН", "7.0г", 3300),

            # Амфетамин VHQ
            ("АМФ", "Амфетамин VHQ", "1.0г", 700),
            ("АМФ", "Амфетамин VHQ", "2.0г", 1400),
            ("АМФ", "Амфетамин VHQ", "3.0г", 1850),
            ("АМФ", "Амфетамин VHQ", "5.0г", 2900),
            ("АМФ", "Амфетамин VHQ", "7.0г", 3800),

            # Мефедрон VHQ
            ("Меф", "Мефедрон VHQ", "1.0г", 700),
            ("Меф", "Мефедрон VHQ", "2.0г", 1400),
            ("Меф", "Мефедрон VHQ", "3.0г", 1850),
            ("Меф", "Мефедрон VHQ", "5.0г", 2900),
            ("Меф", "Мефедрон VHQ", "7.0г", 3800),

            # Метадон Уличный
            ("Метадон", "Метадон Уличный", "1.0г", 800),
            ("Метадон", "Метадон Уличный", "2.0г", 1600),
            ("Метадон", "Метадон Уличный", "3.0г", 2150),
            ("Метадон", "Метадон Уличный", "5.0г", 3350),
            ("Метадон", "Метадон Уличный", "7.0г", 4400),

            # Экстази Домино
            ("Психоделики", "Экстази Домино", "1 шт", 450),
            ("Психоделики", "Экстази Домино", "2 шт", 900),
            ("Психоделики", "Экстази Домино", "3 шт", 1200),
            ("Психоделики", "Экстази Домино", "5 шт", 1850),
            ("Психоделики", "Экстази Домино", "7 шт", 2400),

            # Грибы
            ("Психоделики", "Грибы", "1.0г", 450),
            ("Психоделики", "Грибы", "2.0г", 900),
            ("Психоделики", "Грибы", "3.0г", 1200),
            ("Психоделики", "Грибы", "5.0г", 1850),
            ("Психоделики", "Грибы", "7.0г", 2400),
            
            #Мушрум
            ("Психоделики", "Мушрум", "1шт", 450),
            ("Психоделики", "Мушрум", "2шт", 900),
            ("Психоделики", "Мушрум", "3шт", 1200),
            ("Психоделики", "Мушрум", "5шт", 1850),
            ("Психоделики", "Мушрум", "7шт", 2400),
            # ЛСД-300
            ("Психоделики", "ЛСД-300", "1 шт.", 500),
            ("Психоделики", "ЛСД-300", "2 шт", 1000),
            ("Психоделики", "ЛСД-300", "3 шт", 1350),
            ("Психоделики", "ЛСД-300", "5 шт", 2100),
            ("Психоделики", "ЛСД-300", "7 шт", 2700),

            # МДМА
            ("Психоделики", "МДМА", "1.0г.", 500),
            ("Психоделики", "МДМА", "2.0г", 1000),
            ("Психоделики", "МДМА", "3.0г", 1350),
            ("Психоделики", "МДМА", "5.0г", 2100),
            ("Психоделики", "МДМА", "7.0г", 2700),

            # Alfa pvp
            ("Alfa pvp", "Alfa pvp", "1.0г", 600),
            ("Alfa pvp", "Alfa pvp", "2.0г", 1200),
            ("Alfa pvp", "Alfa pvp", "3.0г", 1600),
            ("Alfa pvp", "Alfa pvp", "5.0г", 2500),
            ("Alfa pvp", "Alfa pvp", "7.0г", 3300),

            # Кетамин
            ("Кетамин", "Кетамин", "1.0г", 500),
            ("Кетамин", "Кетамин", "2.0г", 1000),
            ("Кетамин", "Кетамин", "3.0г", 1350),
            ("Кетамин", "Кетамин", "5.0г", 2100),
            ("Кетамин", "Кетамин", "7.0г", 2700),

            # Гер
            ("Героин", "Героин", "0.5г", 900),
            ("Героин", "Героин", "1.0г", 1800),
            ("Героин", "Героин", "3.0г", 4500),
            ("Героин", "Героин", "5.0г", 6800),
            ("Героин", "Героин", "7.0г", 9000),

            # Кокс
            ("Кокс", "Кокс", "0.25г", 1000),
            ("Кокс", "Кокс", "0.5г", 2000),
            ("Кокс", "Кокс", "1.0г", 3500),
            ("Кокс", "Кокс", "3.0г", 9500),
            ("Кокс", "Кокс", "5.0г", 14500),
            ("Кокс", "Кокс", "7.0г", 19000),

            # D-meth
            ("D-meth", "D-meth", "0.25г", 600),
            ("D-meth", "D-meth", "0.5г", 1200),
            ("D-meth", "D-meth", "1.0г", 2000),
            ("D-meth", "D-meth", "3.0г", 5500),
            ("D-meth", "D-meth", "5.0г", 8500),
            ("D-meth", "D-meth", "7.0г", 11000),

            #електронки
            ("Жыжа", "CANAPUFF HHC-P 1500МГ", "1мл", 500),
            ("Жыжа", "CANAPUFF HHC-P 1500МГ", "2мл", 1000),
            ("Жыжа", "CANAPUFF HHC-P 1500МГ", "3мл", 1400),
            ("Жыжа", "CANAPUFF HHC-P 1500МГ", "6мл", 2400),
            ("Жыжа", "CANAPUFF HHC-P 1500МГ", "10мл", 3000),
            ("Одноразки PACKSPOD", "ХубаБуба", "1шт", 2000),
            ("Одноразки PACKSPOD", "Вишня Киви", "1шт", 2000),
            ("Одноразки PACKSPOD", "Ягодное Мороженое", "1шт", 2000),
            ("Одноразки PACKSPOD", "Банан", "1шт", 2000),
            ("Одноразки PACKSPOD", "Лимон с холодком", "1шт", 2000),
        ]

        cursor.executemany(
            "INSERT INTO products (category_name, product_name, weight, price) VALUES (?, ?, ?, ?)",
            FIXED_PRODUCTS
        )
        conn.commit()
        logging.info(f"Добавлено {len(FIXED_PRODUCTS)} позиций товаров.")



# --- НОВЫЕ: Функции для работы с БД (Товары) ---
def get_product_categories_db() -> list[str]:
    """Возвращает список уникальных категорий"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT category_name FROM products ORDER BY category_name")
        return [row[0] for row in cursor.fetchall()]

def get_products_by_category_db(category_name: str) -> list[str]:
    """Возвращает список уникальных товаров в категории"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT product_name FROM products WHERE category_name = ? ORDER BY product_name", (category_name,))
        return [row[0] for row in cursor.fetchall()]

def get_weights_for_product_db(product_name: str) -> list[dict]:
    """Возвращает список весов и цен (id, weight, price) для товара"""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, weight, price, category_name FROM products WHERE product_name = ? ORDER BY price", (product_name,))
        return [dict(row) for row in cursor.fetchall()]

def get_product_by_id_db(product_id: int) -> dict | None:
    """Возвращает один товар по его ID"""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def add_product_db(category: str, name: str, weight: str, price: int) -> bool:
    """Добавляет новый товар в БД"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO products (category_name, product_name, weight, price) VALUES (?, ?, ?, ?)",
                (category, name, weight, price)
            )
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Error adding product: {e}")
            return False

def get_all_products_full_db() -> list[dict]:
    """Возвращает ПОЛНЫЙ список всех товаров для админки"""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM products ORDER BY category_name, product_name, price")
        return [dict(row) for row in cursor.fetchall()]

def delete_product_db(product_id: int) -> bool:
    """Удаляет товар по ID"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        return cursor.rowcount > 0
# ----------------------------------------------------------------------


# --- Машина состояний (FSM) ---
class AuthStates(StatesGroup):
    waiting_for_captcha = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast_message = State()
    waiting_for_promo_code_name = State()
    waiting_for_promo_code_percent = State()
    waiting_for_promo_code_delete = State()
    in_support = State()
    
    # НОВЫЕ Состояния для Товаров
    waiting_for_product_category = State()
    waiting_for_product_name = State()
    waiting_for_product_weight = State()
    waiting_for_product_price = State()
    waiting_for_product_delete = State()
    
    # === НОВЫЕ Состояния для Блокировок ===
    waiting_for_block_id = State()
    waiting_for_unblock_id = State()

class UserSupport(StatesGroup):
    in_support = State()           # Юзер в активном чате

class OrderStates(StatesGroup):
    waiting_for_category = State()
    waiting_for_product = State()
    waiting_for_weight = State()
    waiting_for_delivery_method = State() 
    waiting_for_region = State()          # Выбор области
    waiting_for_city = State()            # Выбор города
    waiting_for_district = State()        # <--- НОВОЕ: Выбор района
    waiting_for_promo_code = State() 
    waiting_for_payment_check = State()
    waiting_for_contact = State() 


# ----------------------------------------------------------------------
# --- КЛАВИАТУРЫ ---
# ----------------------------------------------------------------------

# --- Клиентские клавиатуры ---

def get_main_menu_keyboard():
    """Главное меню с Профилем, Поддержкой и Отзывами"""
    builder = InlineKeyboardBuilder()
    
    # Основные кнопки
    builder.button(text="🛍️ Каталог Товаров", callback_data="show_catalog")
    builder.button(text="👤 Мой Профиль / Рефералы", callback_data="show_profile")
    
    # !!! НОВАЯ КНОПКА ОТЗЫВЫ !!!
    # Обратите внимание: мы используем url=REVIEWS_LINK, а не callback_data
    builder.button(text="⭐️ Отзывы", url=REVIEWS_LINK) 
    
    builder.button(text="💬 Написать Админу", callback_data="start_support")
    
    builder.adjust(1) # Кнопки будут друг под другом (в 1 столбик)
    return builder.as_markup()



# --- НОВЫЕ Клавиатуры Каталога (на основе БД) ---
def get_categories_keyboard(categories: list[str]):
    """Показывает кнопки Категорий из БД"""
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(text=cat, callback_data=f"category:{cat}")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад в Главное Меню", callback_data="main_menu_start"))
    builder.adjust(1)
    return builder.as_markup()

def get_products_keyboard(products: list[str]):
    """Показывает кнопки Товаров из БД"""
    builder = InlineKeyboardBuilder()
    for prod in products:
        builder.button(text=prod, callback_data=f"product:{prod}")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад в Категории", callback_data="show_catalog"))
    builder.adjust(1)
    return builder.as_markup()

def get_weights_keyboard(weights: list[dict]):
    """Показывает кнопки Веса/Цены из БД"""
    builder = InlineKeyboardBuilder()
    
    category_name_for_back_button = ""
    
    for item in weights:
        text = f"{item['weight']} | {item['price']} грн"
        callback_data = f"weight:{item['id']}"
        builder.button(text=text, callback_data=callback_data)
        if not category_name_for_back_button:
             category_name_for_back_button = item['category_name']

    builder.row(types.InlineKeyboardButton(text="⬅️ Назад к Товарам", callback_data=f"category:{category_name_for_back_button}"))
    builder.adjust(1)
    return builder.as_markup()
# --- Конец Новых Клавиатур Каталога ---


def get_promo_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить", callback_data="promo:skip")
    return builder.as_markup()

# --- Клавиатуры Поддержки (на русском) ---
def get_user_cancel_support_keyboard():
    """Кнопка для отмены (пока админ не ответил)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Завершить диалог", callback_data="user_close_chat")
    return builder.as_markup()

def get_admin_close_chat_keyboard():
    """Кнопка для админа, чтобы выйти из чата"""
    builder = InlineKeyboardBuilder()
    builder.button(text="Завершить чат ❌", callback_data="admin_close_chat")
    return builder.as_markup()


def get_client_back_to_main_menu_keyboard():
    """Кнопка 'Назад' в Главное меню для клиента"""
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад в Главное Меню", callback_data="main_menu_start")
    return builder.as_markup()

# --- Админ-клавиатуры ---

def get_admin_order_keyboard(order_id: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=f"admin:confirm:{order_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin:decline:{order_id}")
    return builder.as_markup()

def get_admin_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика и Заказы", callback_data="admin:stats")
    builder.button(text="📣 Сделать Рассылку", callback_data="admin:broadcast")
    builder.button(text="🎟️ Управление Промокодами", callback_data="admin:promo_menu")
    builder.button(text="📦 Управление Товарами", callback_data="admin:prod_menu")
    # === НОВАЯ КНОПКА ===
    builder.button(text="🚫 Управление Блокировками", callback_data="admin:block_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_promo_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить Промокод", callback_data="promo:add")
    builder.button(text="➖ Удалить Промокод", callback_data="promo:delete")
    builder.button(text="📋 Список Промокодов", callback_data="promo:list")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад в Админ-панель", callback_data="admin:main_menu"))
    builder.adjust(1)
    return builder.as_markup()

def get_admin_back_keyboard():
    """Кнопка "Назад" ТОЛЬКО для админ-панели"""
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад в Админ-панель", callback_data="admin:main_menu")
    return builder.as_markup()

# --- НОВЫЕ Клавиатуры Управления Товарами ---
def get_product_admin_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить Товар", callback_data="prod:add")
    builder.button(text="➖ Удалить Товар", callback_data="prod:delete_list")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад в Админ-панель", callback_data="admin:main_menu"))
    builder.adjust(1)
    return builder.as_markup()

def get_product_delete_keyboard(products: list[dict]):
    """Показывает список всех товаров для удаления"""
    builder = InlineKeyboardBuilder()
    if not products:
        builder.button(text="Нет товаров для удаления", callback_data="noop") # no operation
    else:
        for prod in products:
            text = f"{prod['category_name']} -> {prod['product_name']} ({prod['weight']}) - {prod['price']} грн"
            builder.button(text=f"❌ {text}", callback_data=f"prod:del:{prod['id']}")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:prod_menu"))
    builder.adjust(1)
    return builder.as_markup()
# --- Конец Новых Клавиатур ---

# === НОВАЯ Клавиатура: Меню Блокировок ===
def get_block_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Заблокировать по ID", callback_data="block:add")
    builder.button(text="➖ Разблокировать по ID", callback_data="block:remove")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад в Админ-панель", callback_data="admin:main_menu"))
    builder.adjust(1)
    return builder.as_markup()

# --- Роутер ---
router = Router()

# ----------------------------------------------------------------------
# --- ОБЩИЕ ФУНКЦИИ (Капча, Меню, Оплата, Заказы) ---
# ----------------------------------------------------------------------

async def send_captcha(message: types.Message, state: FSMContext, referrer_id: int | None = None):
    num1, num2 = random.randint(1, 10), random.randint(1, 10)
    answer = num1 + num2
    await state.update_data(captcha_answer=answer, referrer_id=referrer_id)
    await state.set_state(AuthStates.waiting_for_captcha)
    
    await message.answer(
        f"🤖 **Здравствуйте, {message.from_user.first_name}!**\n\n"
        f"Для защиты от ботов, пожалуйста, решите простой пример:\n\n"
        f"**{num1} + {num2} = ?**\n\n"
        f"Отправьте только ответ (число)."
    )

async def show_main_menu(message_or_cb: types.Message | types.CallbackQuery, state: FSMContext, first_name: str):
    """Отображает ГЛАВНОЕ МЕНЮ (текст, без фото)"""
    await state.clear() 
    
    text = f"🛍️ **{first_name}, это Главное меню**\n\nВыберите действие:"
    reply_markup = get_main_menu_keyboard()

    if isinstance(message_or_cb, types.CallbackQuery):
        try:
            await message_or_cb.message.answer(text, reply_markup=reply_markup)
            if message_or_cb.message.reply_markup:
                 await message_or_cb.message.delete()
            await message_or_cb.answer()
        except Exception:
            await message_or_cb.answer()
    else:
        await message_or_cb.answer(text, reply_markup=reply_markup)


async def show_catalog(callback_or_message: types.CallbackQuery | types.Message, state: FSMContext, bot: Bot):
    """
    Отображает КАТАЛОГ (Шаг 1: Категории)
    """
    await state.set_state(OrderStates.waiting_for_category)
    
    chat_id = callback_or_message.from_user.id
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    
    categories = get_product_categories_db()
    
    if not categories:
        text = "🛍️ К сожалению, каталог сейчас пуст."
        reply_markup = get_client_back_to_main_menu_keyboard()
    else:
        text = "🛍️ Выберите интересующую вас категорию:"
        reply_markup = get_categories_keyboard(categories)
    
    if isinstance(callback_or_message, types.CallbackQuery):
        await callback_or_message.message.edit_text(text, reply_markup=reply_markup)
        await callback_or_message.answer()
    else:
        await callback_or_message.answer(text, reply_markup=reply_markup)

# --- Функция: Отправка сообщения об оплате ---
async def send_payment_instructions(message: types.Message, state: FSMContext, bot: Bot):
    await state.set_state(OrderStates.waiting_for_payment_check)
    user_data = await state.get_data()
    
    # ... старые переменные ...
    product_name = user_data.get('chosen_product', 'N/A')
    weight = user_data.get('chosen_weight', 'N/A')
    
    # НОВЫЕ ПЕРЕМЕННЫЕ
    delivery_type = user_data.get('delivery_type', 'Не указано')
    delivery_loc = user_data.get('delivery_location', '')

    original_price = user_data.get('original_price', 0)
    final_price = user_data.get('final_price', original_price)
    promo_code = user_data.get('promo_code_used')
    
    price_text = f"Цена: **{final_price} грн**"
    if promo_code:
        price_text += f"\n(Скидка по коду `{promo_code}`, старая цена: {original_price} грн)"

    chosen_card = random.choice(PAYMENT_CARDS)

    payment_message = (
        f"🔥 **Ваш заказ:**\n"
        f"Товар: **{product_name}**\n"
        f"Вес: **{weight}**\n"
        f"🚚 **Доставка:** {delivery_type} ({delivery_loc})\n" # <-- ДОБАВИЛИ ЭТУ СТРОКУ
        f"{price_text}\n\n"
        "--- **РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ** ---\n"
        f"Карта: `{chosen_card}`\n"
        "--- **Обязательно оплатите** ---\n\n"
        "⚠️ **После оплаты, пожалуйста, отправьте скриншот (чек) об оплате** в ответ на это сообщение.\n"
    )
       
    await message.answer(payment_message, reply_markup=get_client_back_to_main_menu_keyboard())

# --- Функция: Обработка и сохранение заказа ---
async def process_new_order(message: types.Message, state: FSMContext, bot: Bot, contact_info: str):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    user_data = await state.get_data()
    
    order_id = str(uuid.uuid4())
    short_order_id = order_id[:8] 
    
    check_file_id = user_data.get('payment_check_file_id')
    if not check_file_id:
        logging.error(f"No payment_check_file_id for user {message.from_user.id}")
        await message.answer("❌ Произошла ошибка: не найдено фото чека. Пожалуйста, начните сначала через /start")
        return

    # Извлекаем данные о доставке
    delivery_type = user_data.get('delivery_type', 'Не указан')
    delivery_loc = user_data.get('delivery_location', 'Не указано')

    original_price = user_data.get('original_price', 0)
    final_price = user_data.get('final_price', original_price)
    promo_code = user_data.get('promo_code_used')

    order_data = {
        "order_id": order_id,
        "short_id": short_order_id,
        "user_id": message.from_user.id,
        "username": message.from_user.username or 'Нет',
        "product": user_data.get('chosen_product', 'N/A'),
        "weight": user_data.get('chosen_weight', 'N/A'),
        "original_price": original_price,
        "final_price": final_price,
        "promo_code_used": promo_code,
        "contact_info": contact_info,
        "check_file_id": check_file_id
    }
    
    # Сохраняем в базу (в текущей структуре БД полей под доставку может не быть, 
    # но админ получит все данные в сообщении)
    create_db_order(order_data)
    
    price_text_admin = f"**{final_price} грн**"
    if promo_code:
        price_text_admin += f" (код: `{promo_code}`, было {original_price} грн)"

    # Формируем сообщение для админа с учетом новых данных
    admin_caption = (
        f"🚨 **НОВЫЙ ЗАКАЗ!** (ID: `{short_order_id}`) 🚨\n\n"
        f"👤 **Клиент:** @{order_data['username']} (ID: `{message.from_user.id}`)\n"
        f"📦 **Товар:** {order_data['product']} ({order_data['weight']})\n"
        f"💰 **К оплате:** {price_text_admin}\n"
        f"--------------------------\n"
        f"🚚 **Способ:** {delivery_type}\n"
        f"📍 **Локация:** {delivery_loc}\n"
        f"--------------------------\n"
        f"📝 **Контактные данные:**\n{contact_info}\n"
    )

    admin_kb = get_admin_order_keyboard(order_id)
    
    # Рассылка админам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=check_file_id,
                caption=admin_caption,
                reply_markup=admin_kb
            )
        except Exception as e:
            logging.error(f"Failed to send order {short_order_id} to admin {admin_id}: {e}")
    
    # Ответ пользователю
    await message.answer(
        f"🎉 **Заказ #{short_order_id} принят!**\n\n"
        f"Вы выбрали: **{order_data['product']}**\n"
        f"Доставка: **{delivery_type}** ({delivery_loc})\n\n"
        "⏳ Администратор проверит оплату и свяжется с вами. Спасибо за выбор!"
    )
    
    await state.clear()
    await show_main_menu(message, state, message.from_user.first_name)


# ----------------------------------------------------------------------
# --- ХЕНДЛЕРЫ КЛИЕНТА (Капча, Заказ) ---
# ----------------------------------------------------------------------

# /start с проверкой в БД и Рефералом
@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    
    referrer_id = None
    try:
        payload = message.text.split(maxsplit=1)[1] 
        if payload.isdigit():
            ref_id_candidate = int(payload)
            if ref_id_candidate != message.from_user.id: 
                referrer_id = ref_id_candidate
    except IndexError:
        pass 
    
    if is_user_verified(message.from_user.id):
        await show_main_menu(message, state, message.from_user.first_name)
    else:
        await send_captcha(message, state, referrer_id)

# Обработка капчи с записью в БД (С ДИАГНОСТИКОЙ)
@router.message(StateFilter(AuthStates.waiting_for_captcha), F.text)
async def process_captcha_answer(message: types.Message, state: FSMContext):
    data = await state.get_data()
    correct_answer = data.get('captcha_answer')
    try:
        user_answer = int(message.text.strip())
        
        if user_answer == correct_answer:
            # --- НАЧАЛО БЛОКА ДИАГНОСТИКИ ---
            try:
                referrer_id = data.get('referrer_id')
                
                # --- Шаг 1: Проверяем запись в БД ---
                logging.info(f"Adding user {message.from_user.id} to DB...")
                add_user_to_db(message.from_user.id, message.from_user.username, referrer_id)
                logging.info(f"User {message.from_user.id} added successfully.")
                
                # --- Шаг 2: Отправляем ответ ---
                await message.answer(f"✅ **Верно, {message.from_user.first_name}!** Добро пожаловать.")
                
                # --- Шаг 3: Показываем меню ---
                logging.info("Showing main menu...")
                await show_main_menu(message, state, message.from_user.first_name)
                logging.info("Main menu shown.")
            
            except Exception as e:
                # --- ЭТО САМОЕ ВАЖНОЕ ---
                # Если ошибка, мы ее ловим и сообщаем пользователю И в лог
                logging.error(f"!!! ОШИБКА ПРИ ДОБАВЛЕНИИ ЮЗЕРА В БД: {e}", exc_info=True)
                await message.answer(
                    f"❌ **Произошла внутренняя ошибка.**\n\n"
                    f"Бот не смог записать вас в базу данных.\n"
                    f"**Текст ошибки:** `{e}`\n\n"
                    f"Скорее всего, у бота нет прав на запись файла `shop.db`."
                )
            # --- КОНЕЦ БЛОКА ДИАГНОСТИКИ ---

        else:
            await message.answer("❌ **Неверно.** Попробуйте еще раз.")
            await send_captcha(message, state, data.get('referrer_id')) 
    
    except ValueError:
        await message.answer("❌ **Неверно.** Пожалуйста, отправьте только число.")
        await send_captcha(message, state, data.get('referrer_id'))


@router.message(StateFilter(AuthStates.waiting_for_captcha), ~F.text)
async def process_captcha_invalid_input(message: types.Message):
    await message.answer("Пожалуйста, отправьте **ответ в виде числа**.")

# --- Хендлеры Главного Меню Клиента ---
@router.callback_query(F.data == "main_menu_start")
async def cb_main_menu_start(callback: types.CallbackQuery, state: FSMContext):
    """Кнопка 'Назад' в Главное Меню"""
    await show_main_menu(callback, state, callback.from_user.first_name)

@router.callback_query(F.data == "show_catalog")
async def cb_show_catalog(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    """Кнопка 'Каталог'"""
    await show_catalog(callback, state, bot)
    
@router.callback_query(F.data == "show_profile")
async def cb_show_profile(callback: types.CallbackQuery, bot: Bot):
    """Показывает профиль и реферальную ссылку"""
    await bot.send_chat_action(chat_id=callback.from_user.id, action=ChatAction.TYPING)
    
    user_data = get_user_data_db(callback.from_user.id)
    if not user_data:
        await callback.answer("Ошибка: не могу найти ваш профиль. Попробуйте /start", show_alert=True)
        return
        
    bot_info = await bot.get_me()
    bot_username = bot_info.username
    ref_link = f"https://t.me/{bot_username}?start={callback.from_user.id}"
    
    ref_count = user_data.get('referral_count', 0)
    
    text = (
        f"👤 **Ваш Профиль**\n\n"
        f"Ваш ID: `{callback.from_user.id}`\n\n"
        f"--- **🏆 Реферальная Программа** ---\n"
        f"Пригласите **5 друзей**, которые совершат *первую* покупку, и получите **промокод на 50% скидки!**\n\n"
        f"📈 **Ваш прогресс:** {ref_count} / 5\n\n"
        f"🔗 **Ваша ссылка для приглашений:**\n"
        f"`{ref_link}`"
    )
    
    await callback.message.edit_text(text, reply_markup=get_client_back_to_main_menu_keyboard()) 
    await callback.answer()

# --- НОВЫЕ Хендлеры Заказа (на основе БД) ---

@router.callback_query(F.data.startswith("category:"), StateFilter(OrderStates.waiting_for_category, OrderStates.waiting_for_weight))
async def cb_select_category(callback: types.CallbackQuery, state: FSMContext):
    """Отображает Товары в Категории"""
    category_name = callback.data.split(":")[1]
    
    await state.update_data(chosen_category=category_name)
    await state.set_state(OrderStates.waiting_for_product)
    
    products = get_products_by_category_db(category_name)
    
    if not products:
        await callback.answer("В этой категории пока нет товаров.", show_alert=True)
        return
    
    keyboard = get_products_keyboard(products)
    caption = (
        f"✅ Вы выбрали категорию: **{category_name}**\n\n"
        f"Теперь выберите товар:"
    )
    
    await callback.message.edit_text(caption, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("product:"), StateFilter(OrderStates.waiting_for_product))
async def callback_select_product(callback: types.CallbackQuery, state: FSMContext):
    """Отображает Вес/Цену для Товара"""
    product_name = callback.data.split(":")[1]
    
    await state.update_data(chosen_product_name=product_name)
    await state.set_state(OrderStates.waiting_for_weight)
    
    weights = get_weights_for_product_db(product_name)
    
    if not weights:
        await callback.answer("У этого товара почему-то нет веса.", show_alert=True)
        return

    keyboard = get_weights_keyboard(weights)
    caption = (
        f"✅ Вы выбрали товар: **{product_name}**\n\n"
        f"Теперь выберите вес:"
    )
    
    await callback.message.edit_text(caption, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("weight:"), StateFilter(OrderStates.waiting_for_weight))
async def callback_select_weight(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    try:
        product_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка, неверный ID товара.", show_alert=True)
        return

    product_data = get_product_by_id_db(product_id)
    if not product_data:
        await callback.answer("Ошибка, товар не найден в БД.", show_alert=True)
        return
    
    price = product_data['price']
    
    # Сохраняем данные о товаре
    await state.update_data(
        chosen_product=product_data['product_name'],
        chosen_weight=product_data['weight'],
        original_price=price,
        final_price=price
    )
    
    # === ИЗМЕНЕНИЕ: Переходим к выбору доставки ===
    await state.set_state(OrderStates.waiting_for_delivery_method)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📍Клад (Магнит/Прикоп)", callback_data="delivery:klad")
    builder.button(text="📦 Почта (Отправка)", callback_data="delivery:postal")
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад к выбору веса", callback_data=f"product:{product_data['product_name']}"))
    builder.adjust(1)

    await callback.message.edit_text(
        f"✅ Вы выбрали: **{product_data['product_name']}** ({product_data['weight']}) - **{price} грн**\n\n"
        f"🚚 **Выберите способ получения:**",
        reply_markup=builder.as_markup()
    )
    await callback.answer()
    
    # --- ХЕНДЛЕРЫ ВЫБОРА ДОСТАВКИ ---

@router.callback_query(F.data == "delivery:postal", StateFilter(OrderStates.waiting_for_delivery_method))
async def cb_delivery_postal(callback: types.CallbackQuery, state: FSMContext):
    """Клиент выбрал Почту"""
    await state.update_data(delivery_type="Почта", delivery_location="Отделение почты")
    # Переходим к промокоду
    await state.set_state(OrderStates.waiting_for_promo_code)
    await ask_promo_code(callback.message, state) 
    await callback.answer()

@router.callback_query(F.data == "delivery:klad", StateFilter(OrderStates.waiting_for_delivery_method))
async def cb_delivery_klad(callback: types.CallbackQuery, state: FSMContext):
    """Клиент выбрал Клад -> Показываем Области"""
    await state.update_data(delivery_type="Клад")
    await state.set_state(OrderStates.waiting_for_region)
    
    builder = InlineKeyboardBuilder()
    # Берем ключи (названия областей) из словаря
    for region in KLAD_LOCATIONS.keys():
        builder.button(text=region, callback_data=f"region:{region}")
    
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="show_catalog")) 
    builder.adjust(1)
    
    await callback.message.edit_text(
        "🗺️ **Выберите область:**",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("region:"), StateFilter(OrderStates.waiting_for_region))
async def cb_select_region(callback: types.CallbackQuery, state: FSMContext):
    """Клиент выбрал Область -> Показываем Города"""
    region = callback.data.split(":")[1]
    
    # Получаем словарь городов этой области
    cities_dict = KLAD_LOCATIONS.get(region, {})
    cities_list = list(cities_dict.keys()) # Превращаем ключи (города) в список
    
    await state.update_data(chosen_region=region)
    await state.set_state(OrderStates.waiting_for_city)
    
    builder = InlineKeyboardBuilder()
    for city in cities_list:
        builder.button(text=city, callback_data=f"city:{city}")
    
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад к областям", callback_data="delivery:klad"))
    builder.adjust(2) 
    
    await callback.message.edit_text(
        f"📍 Область: **{region}**\n👇 Выберите город:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("city:"), StateFilter(OrderStates.waiting_for_city))
async def cb_select_city(callback: types.CallbackQuery, state: FSMContext):
    """Клиент выбрал Город -> Показываем Районы (НОВАЯ ЛОГИКА)"""
    city = callback.data.split(":")[1]
    
    data = await state.get_data()
    region = data.get('chosen_region', '')
    
    # Получаем список районов для выбранного города
    # KLAD_LOCATIONS[Область][Город] -> список районов
    districts = KLAD_LOCATIONS.get(region, {}).get(city, [])
    
    await state.update_data(chosen_city=city)
    await state.set_state(OrderStates.waiting_for_district)
    
    builder = InlineKeyboardBuilder()
    for dist in districts:
        # callback_data ограничен 64 байтами, поэтому обрезаем длинные названия если нужно
        safe_dist = dist[:20] 
        builder.button(text=dist, callback_data=f"dist:{safe_dist}")
        
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад к городам", callback_data=f"region:{region}"))
    builder.adjust(2)
    
    await callback.message.edit_text(
        f"📍 Город: **{city}**\n👇 Выберите район/ориентир:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

# === НОВЫЙ ХЕНДЛЕР ДЛЯ РАЙОНОВ ===
@router.callback_query(F.data.startswith("dist:"), StateFilter(OrderStates.waiting_for_district))
async def cb_select_district(callback: types.CallbackQuery, state: FSMContext):
    """Клиент выбрал Район -> Идем к оплате"""
    district = callback.data.split(":")[1]
    
    data = await state.get_data()
    region = data.get('chosen_region', '')
    city = data.get('chosen_city', '')
    
    # Формируем полный адрес
    full_location = f"{region}, г.{city}, р-н {district}"
    await state.update_data(delivery_location=full_location)
    
    # Переходим к промокоду
    await state.set_state(OrderStates.waiting_for_promo_code)
    await ask_promo_code(callback.message, state)
    await callback.answer()

# Вспомогательная функция, чтобы не дублировать код запроса промокода
async def ask_promo_code(message: types.Message, state: FSMContext):
    """Показывает меню ввода промокода"""
    # Если это CallbackQuery (нажатие кнопки), message будет доступен через callback.message
    # Но так как мы передаем message, нам нужно редактировать именно его
    
    await message.edit_text(
        "🎟️ **Есть промокод?**\n\n"
        "Отправьте код сообщением или нажмите **Пропустить**.",
        reply_markup=get_promo_keyboard()
    )

@router.callback_query(F.data == "promo:skip", StateFilter(OrderStates.waiting_for_promo_code))
async def callback_skip_promo(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await callback.message.edit_text("Промокод пропущен.")
    await send_payment_instructions(callback.message, state, bot)
    await callback.answer()

@router.message(StateFilter(OrderStates.waiting_for_promo_code), F.text)
async def process_promo_code(message: types.Message, state: FSMContext, bot: Bot):
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    promo_code = message.text.strip().upper()
    promo_data = get_promo_db(promo_code)
    
    if promo_data:
        user_data = await state.get_data()
        original_price = user_data.get('original_price', 0)
        discount_percent = promo_data['discount_percent']
        new_price = round(original_price * (1 - discount_percent / 100))
        
        await state.update_data(final_price=new_price, promo_code_used=promo_code)
        
        if not promo_data['is_reusable']:
            del_promo_db(promo_code)
            
        await message.answer(
            f"✅ **Промокод `{promo_code}` принят!**\n"
            f"Скидка: {discount_percent}%\n"
            f"Новая цена: {new_price} грн (было {original_price} грн)."
        )
            
        await send_payment_instructions(message, state, bot)
    else:
        await message.answer(
            "❌ **Промокод не найден или строк действия истёк.**\n"
            "Попробуйте еще раз или нажмите 'Пропустить' (в сообщении выше)."
        )

# --- Хендлеры Заказа (Чек, Контакт) ---
@router.message(F.photo, StateFilter(OrderStates.waiting_for_payment_check))
async def message_payment_check(message: types.Message, state: FSMContext, bot: Bot):
    await state.update_data(payment_check_file_id=message.photo[-1].file_id)
    user = message.from_user
    if user.username:
        contact_info = f"👤 **Telegram (Автоматически):** @{user.username}"
        await process_new_order(message, state, bot, contact_info)
    else:
        await state.set_state(OrderStates.waiting_for_contact)
        await message.answer(
            "🧾 **Чек принят!**\n\n"
            "⚠️ **У вас не установлен @username.**\n"
            "Пожалуйста, **отправьте свой контакт** (через кнопку 📎 -> Контакт, или введите номер телефона) для связи с вами.",
        )

@router.message(StateFilter(OrderStates.waiting_for_payment_check))
async def invalid_payment_check(message: types.Message):
    await message.answer(
        "⚠️ **Пожалуйста, отправьте фото!**\n"
        "Мы ожидаем **скриншот (чек) оплаты** в виде изображения.",
    )

@router.message((F.contact | F.text), StateFilter(OrderStates.waiting_for_contact))
async def message_contact(message: types.Message, state: FSMContext, bot: Bot):
    contact_info = ""
    if message.contact:
        contact_info = (f"📞 **Контакт (с кнопки):**\n"
                        f"Телефон: `{message.contact.phone_number}`\n"
                        f"ID Telegram: `{message.contact.user_id}`")
    elif message.text:
        text = message.text.strip()
        if text.startswith("@") and 5 <= len(text[1:]) <= 32:
            contact_info = f"👤 **Telegram (текст):** `{text}`"
        elif re.match(r'^\+?[\d\s\-\(\)]{7,20}$', text):
            contact_info = f"📞 **Телефон (текст):** `{text}`"
        else:
            await message.answer("⚠️ **Неверный формат!**\n"
                             "Отправьте контакт (📎 -> Контакт) или введите **номер телефона**.")
            return
    await process_new_order(message, state, bot, contact_info)

# ----------------------------------------------------------------------
# --- ОБНОВЛЕННАЯ СИСТЕМА ПОДДЕРЖКИ (Live Chat) ---
# ----------------------------------------------------------------------

# 1. Пользователь нажимает "Написать Админу" - ВХОД В РЕЖИМ
@router.callback_query(F.data == "start_support", StateFilter('*'))
async def cb_start_support(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear() 
    await state.set_state(UserSupport.in_support) # Сразу ставим состояние чата
    
    # Уведомляем пользователя
    await callback.message.edit_text(
        "**Вы вошли в режим чата с Админом!** 💬\n\n"
        "Теперь вы можете отправлять сообщения (текст, фото, видео, голосовые и т.д.).\n"
        "Администратор ответит вам здесь же.",
        reply_markup=get_user_cancel_support_keyboard()
    )
    await callback.answer()
    
    # Уведомляем админов о новом чате
    user = callback.from_user
    user_link = f"@{user.username}" if user.username else f"ID: {user.id}"
    
    admin_text = (
        f"🔔 **Новый запрос в поддержку!**\n"
        f"От: {user.first_name} ({user_link})\n"
        f"Нажмите 'Ответить', чтобы начать диалог."
    )
    
    # Кнопка для админа, чтобы быстро войти в чат с этим юзером
    kb = InlineKeyboardBuilder()
    kb.button(text="💬 Ответить", callback_data=f"admin_reply_to:{user.id}")
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, reply_markup=kb.as_markup())
        except Exception:
            pass

# 2. Пользователь отправляет ЛЮБОЕ сообщение (будучи в режиме поддержки)
@router.message(StateFilter(UserSupport.in_support))
async def handle_user_support_message(message: types.Message, state: FSMContext, bot: Bot):
    # Просто пересылаем ВСЁ админам
    user = message.from_user
    
    # Кнопка "Ответить" под каждым сообщением для удобства
    kb = InlineKeyboardBuilder()
    kb.button(text=f"Ответить {user.first_name}", callback_data=f"admin_reply_to:{user.id}")
    
    sent_count = 0
    for admin_id in ADMIN_IDS:
        try:
            # Используем copy_to, чтобы поддерживать все типы медиа (фото, видео, гс, и т.д.)
            await message.copy_to(chat_id=admin_id, reply_markup=kb.as_markup())
            sent_count += 1
        except Exception as e:
            logging.error(f"Failed to copy msg to admin {admin_id}: {e}")
            
    if sent_count > 0:
        # Можно поставить реакцию "глаз" или просто ничего не делать, чтобы не спамить
        pass
    else:
        await message.answer("⚠️ Ошибка доставки сообщения администраторам.")

# 3. Админ нажимает "Ответить"
@router.callback_query(F.data.startswith('admin_reply_to:'), F.from_user.id.in_(ADMIN_IDS))
async def admin_start_reply_mode(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    user_id = int(callback.data.split(":")[1])
    
    # Устанавливаем админу состояние "В чате с user_id"
    await state.set_state(AdminStates.in_support)
    await state.update_data(chatting_with_user_id=user_id)
    
    await callback.message.answer(
        f"✅ **Вы вошли в чат с пользователем {user_id}.**\n"
        f"Все ваши сообщения будут отправлены ему.\n"
        f"Поддерживаются текст, фото, видео, голосовые и т.д.",
        reply_markup=get_admin_close_chat_keyboard()
    )
    await callback.answer()

# 4. Админ отправляет сообщение (будучи в режиме чата)
@router.message(StateFilter(AdminStates.in_support), F.from_user.id.in_(ADMIN_IDS))
async def admin_chat_message(message: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    user_id = data.get('chatting_with_user_id')
    
    if not user_id:
        await message.answer("Ошибка: потерян ID пользователя. Нажмите /admin")
        await state.clear()
        return

    try:
        # Показываем пользователю, что админ печатает/записывает
        action = ChatAction.TYPING
        if message.voice: action = ChatAction.RECORD_VOICE
        elif message.video_note: action = ChatAction.RECORD_VIDEO_NOTE
        elif message.photo or message.video: action = ChatAction.UPLOAD_PHOTO
        
        await bot.send_chat_action(chat_id=user_id, action=action)
        
        # Копируем сообщение пользователю
        await message.copy_to(chat_id=user_id)
        
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить (возможно, бот заблокирован): {e}")

# 5. Завершение чата (Пользователем)
@router.callback_query(F.data == 'user_close_chat', StateFilter(UserSupport.in_support))
async def user_quit_chat(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("✅ Диалог завершен. Возврат в меню.", reply_markup=get_main_menu_keyboard())
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()

# 6. Завершение чата (Админом)
@router.callback_query(F.data == 'admin_close_chat', StateFilter(AdminStates.in_support), F.from_user.id.in_(ADMIN_IDS))
async def admin_quit_chat(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("✅ Вы вышли из чата.", reply_markup=get_admin_main_keyboard())
    try:
        await callback.message.delete() # Удаляем кнопку "Завершить"
    except:
        pass
    await callback.answer()

# ----------------------------------------------------------------------
# --- КОНЕЦ НОВОЙ СИСТЕМЫ ПОДДЕРЖКИ ---
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# --- ХЕНДЛЕРЫ АДМИН-ПАНЕЛИ (Кнопки, FSM) ---
# ----------------------------------------------------------------------

@router.message(Command("admin"), F.from_user.id.in_(ADMIN_IDS))
async def cmd_admin_panel(message: types.Message, state: FSMContext, bot: Bot):
    await state.clear() 
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    await message.answer(
        "🛡️ **Админ-панель**\n\n"
        "Добро пожаловать. Выберите действие:",
        reply_markup=get_admin_main_keyboard()
    )

@router.callback_query(F.data == "admin:main_menu", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_main_menu(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await bot.send_chat_action(chat_id=callback.from_user.id, action=ChatAction.TYPING)
    await callback.message.edit_text(
        "🛡️ **Админ-панель**\n\n"
        "Добро пожаловать. Выберите действие:",
        reply_markup=get_admin_main_keyboard()
    )
    await callback.answer()

# --- Раздел "Статистика и Заказы" ---
@router.callback_query(F.data == "admin:stats", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_stats(callback: types.CallbackQuery, bot: Bot):
    await bot.send_chat_action(chat_id=callback.from_user.id, action=ChatAction.TYPING)
    
    user_count = get_user_count()
    pending_orders_list = get_pending_orders_db()
    
    # === НОВЫЕ СЧЕТЧИКИ СТАТИСТИКИ ===
    blocked_bot_count = get_blocked_bot_count_db()
    blacklisted_count = get_blocked_user_count_db()
    
    pending_list_text = []
    if not pending_orders_list:
        pending_list_text.append("✅ *Нет нерассмотренных заказов.*")
    else:
        for order in pending_orders_list:
            price_text = f"**{order['final_price']} грн**"
            if order['promo_code_used']:
                price_text += f" (было {order['original_price']} грн, код: `{order['promo_code_used']}`)"
            
            pending_list_text.append(
                f"🆔 **Заказ:** `{order['short_id']}`\n"
                f"👤 **Клиент:** @{order['username']} ({order['contact_info']})\n"
                f"📦 **Товар:** {order['product']} ({order['weight']})\n"
                f"💰 **Цена:** {price_text}"
            )
    
    await callback.message.edit_text(
        f"🛡️ **Статистика и Заказы**\n\n"
        f"--- **📊 Статистика** ---\n"
        f"👤 Всего пользователей: **{user_count}**\n"
        f"❌ Заблокировали бота: **{blocked_bot_count}**\n"
        f"🚫 Заблокировано админом: **{blacklisted_count}**\n\n"
        f"--- **⏳ Нерассмотренные заказы ({len(pending_orders_list)})** ---\n\n"
        + "\n\n".join(pending_list_text),
        reply_markup=get_admin_back_keyboard()
    )
    await callback.answer()

# --- Раздел "Рассылка" ---
@router.callback_query(F.data == "admin:broadcast", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_broadcast_message)
    await callback.message.edit_text(
        "📣 **Режим рассылки.**\n"
        "Отправьте мне **любое сообщение** (текст, фото, стикер), "
        "и я разошлю его всем пользователям бота.\n\n"
        "Для отмены введите /cancel или нажмите кнопку 'Назад'.",
        reply_markup=get_admin_back_keyboard()
    )
    await callback.answer()

@router.message(StateFilter(AdminStates.waiting_for_broadcast_message), F.from_user.id.in_(ADMIN_IDS))
async def process_broadcast_message(message: types.Message, state: FSMContext, bot: Bot):
    await state.clear() 
    
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    user_ids = get_all_user_ids_db()
    if not user_ids:
        await message.answer("❌ Нет пользователей для рассылки.", reply_markup=get_admin_main_keyboard())
        return

    await message.answer(f"🚀 **Начинаю рассылку...**\n"
                         f"Пользователей в базе: {len(user_ids)}")
    
    success_count = 0
    fail_count = 0
    
    for user_id in user_ids:
        try:
            await message.copy_to(chat_id=user_id)
            success_count += 1
            await asyncio.sleep(0.1) 
        except (TelegramForbiddenError, TelegramAPIError):
            fail_count += 1
            # === НОВОЕ: Отмечаем, что юзер заблокал бота ===
            set_user_blocked_bot_db(user_id)
        except Exception as e:
            logging.error(f"Unknown error during broadcast to {user_id}: {e}")
            fail_count += 1
            
    await message.answer(
        f"🏁 **Рассылка завершена!**\n\n"
        f"✅ Успешно отправлено: **{success_count}**\n"
        f"❌ Заблокировали бота: **{fail_count}**",
        reply_markup=get_admin_main_keyboard() 
    )

# --- Раздел "Промокоды" ---
@router.callback_query(F.data == "admin:promo_menu", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_promo_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🎟️ **Управление Промокодами**\n\n"
        "Выберите действие:",
        reply_markup=get_promo_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "promo:list", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_list_promo(callback: types.CallbackQuery, bot: Bot):
    await bot.send_chat_action(chat_id=callback.from_user.id, action=ChatAction.TYPING)
    
    promos = get_all_promos_db()
    if not promos:
        await callback.answer("ℹ️ Нет активных промокодов.", show_alert=True)
        return
        
    text = "🎟️ **Активные промокоды:**\n\n"
    text += "\n".join([
        f"`{p['code']}` - {p['discount_percent']}% "
        f"({'Одноразовый' if not p['is_reusable'] else 'Многоразовый'})" 
        for p in promos
    ])
    
    
    await callback.message.edit_text(
        text,
        reply_markup=get_promo_menu_keyboard()
    )
    await callback.answer()

# --- FSM для Добавления Промокода ---
@router.callback_query(F.data == "promo:add", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_add_promo_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_promo_code_name)
    await callback.message.edit_text(
        "➕ **Введите название нового промокода** (например, `SALE15`)\n\n"
        "Для отмены введите /cancel или нажмите кнопку 'Назад'.",
        reply_markup=get_admin_back_keyboard()
    )
    await callback.answer()

@router.message(StateFilter(AdminStates.waiting_for_promo_code_name), F.text, F.from_user.id.in_(ADMIN_IDS))
async def process_promo_code_name(message: types.Message, state: FSMContext):
    code_name = message.text.strip().upper()
    await state.update_data(promo_code_name=code_name)
    await state.set_state(AdminStates.waiting_for_promo_code_percent)
    await message.answer(
        f"Отлично. Код: `{code_name}`.\n"
        f"**Теперь введите процент скидки** (просто число, например `15`)."
    )

@router.message(StateFilter(AdminStates.waiting_for_promo_code_percent), F.text, F.from_user.id.in_(ADMIN_IDS))
async def process_promo_code_percent(message: types.Message, state: FSMContext):
    try:
        percent = int(message.text.strip())
        if not (0 < percent <= 100):
            raise ValueError("Процент должен быть от 1 до 100")
        
        user_data = await state.get_data()
        code_name = user_data['promo_code_name']
        
        if add_promo_db(code_name, percent, is_reusable=True):
            await message.answer(f"✅ **Промокод `{code_name}` на {percent}% скидки создан/обновлен.**")
        else:
            await message.answer("❌ Ошибка при добавлении в БД.")
        
        await state.clear()
        await message.answer("🎟️ **Управление Промокодами**", reply_markup=get_promo_menu_keyboard())

    except (ValueError, IndexError):
        await message.answer("❌ **Неверный формат.**\n"
                             "Введите **просто число** (например `15`).")

# --- FSM для Удаления Промокода ---
@router.callback_query(F.data == "promo:delete", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_del_promo_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_promo_code_delete)
    await callback.message.edit_text(
        "➖ **Введите название промокода для удаления** (например, `SALE15` или `REF-ABC123`)\n\n"
        "Для отмены введите /cancel или нажмите кнопку 'Назад'.",
        reply_markup=get_admin_back_keyboard()
    )
    await callback.answer()

@router.message(StateFilter(AdminStates.waiting_for_promo_code_delete), F.text, F.from_user.id.in_(ADMIN_IDS))
async def process_promo_code_delete(message: types.Message, state: FSMContext):
    code_name = message.text.strip().upper()
    
    if del_promo_db(code_name):
        await message.answer(f"✅ **Промокод `{code_name}` удален.**")
    else:
        await message.answer(f"❌ Промокод `{code_name}` не найден.")
    
    await state.clear()
    await message.answer("🎟️ **Управление Промокодами**", reply_markup=get_promo_menu_keyboard())


# --- НОВЫЕ ХЕНДЛЕРЫ: Управление Товарами ---

@router.callback_query(F.data == "admin:prod_menu", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_prod_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "📦 **Управление Товарами**\n\Выберите действие:",
        reply_markup=get_product_admin_menu()
    )
    await callback.answer()

# --- FSM для Добавления Товара ---
@router.callback_query(F.data == "prod:add", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_add_prod_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_product_category)
    await callback.message.edit_text(
        "➕ **Шаг 1/4: Введите название Категории**\n"
        "(например, `Шишки`, `Гашиш`, `Экстази`)\n\n"
        "Для отмены введите /cancel.",
        reply_markup=get_admin_back_keyboard()
    )
    await callback.answer()

@router.message(StateFilter(AdminStates.waiting_for_product_category), F.text, F.from_user.id.in_(ADMIN_IDS))
async def process_prod_category(message: types.Message, state: FSMContext):
    await state.update_data(prod_category=message.text.strip())
    await state.set_state(AdminStates.waiting_for_product_name)
    await message.answer(
        f"✅ Категория: `{message.text.strip()}`\n\n"
        f"➕ **Шаг 2/4: Введите Название Товара**\n"
        f"(например, `АК-47 (ИНДИКА)`)"
    )

@router.message(StateFilter(AdminStates.waiting_for_product_name), F.text, F.from_user.id.in_(ADMIN_IDS))
async def process_prod_name(message: types.Message, state: FSMContext):
    await state.update_data(prod_name=message.text.strip())
    await state.set_state(AdminStates.waiting_for_product_weight)
    await message.answer(
        f"✅ Название: `{message.text.strip()}`\n\n"
        f"➕ **Шаг 3/4: Введите Вес/Кол-во**\n"
        f"(например, `1.0г` или `1 шт`)"
    )

@router.message(StateFilter(AdminStates.waiting_for_product_weight), F.text, F.from_user.id.in_(ADMIN_IDS))
async def process_prod_weight(message: types.Message, state: FSMContext):
    await state.update_data(prod_weight=message.text.strip())
    await state.set_state(AdminStates.waiting_for_product_price)
    await message.answer(
        f"✅ Вес: `{message.text.strip()}`\n\n"
        f"➕ **Шаг 4/4: Введите Цену (только число)**\n"
        f"(например, `400`)"
    )

@router.message(StateFilter(AdminStates.waiting_for_product_price), F.text, F.from_user.id.in_(ADMIN_IDS))
async def process_prod_price(message: types.Message, state: FSMContext):
    try:
        price = int(message.text.strip())
        if price <= 0:
            raise ValueError("Цена должна быть > 0")
        
        data = await state.get_data()
        category = data['prod_category']
        name = data['prod_name']
        weight = data['prod_weight']
        
        if add_product_db(category, name, weight, price):
            await message.answer(
                f"✅ **Товар успешно добавлен!**\n\n"
                f"Категория: `{category}`\n"
                f"Товар: `{name}`\n"
                f"Вес: `{weight}`\n"
                f"Цена: `{price}` грн"
            )
        else:
            await message.answer("❌ Ошибка при добавлении в БД.")
        
        await state.clear()
        await message.answer("📦 **Управление Товарами**", reply_markup=get_product_admin_menu())

    except (ValueError, IndexError):
        await message.answer("❌ **Неверный формат.**\n"
                             "Введите **просто число** (например `400`).")

# --- FSM для Удаления Товара ---
@router.callback_query(F.data == "prod:delete_list", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_del_prod_list(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await state.set_state(AdminStates.waiting_for_product_delete)
    
    await bot.send_chat_action(chat_id=callback.from_user.id, action=ChatAction.TYPING)
    
    all_products = get_all_products_full_db()
    
    await callback.message.edit_text(
        "➖ **Удаление Товара**\n\n"
        "Нажмите на товар, который хотите удалить. "
        "**Это действие необратимо!**",
        reply_markup=get_product_delete_keyboard(all_products)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("prod:del:"), StateFilter(AdminStates.waiting_for_product_delete), F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_del_prod_confirm(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    try:
        product_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка, неверный ID товара.", show_alert=True)
        return
        
    if delete_product_db(product_id):
        await callback.answer("✅ Товар удален!", show_alert=True)
    else:
        await callback.answer("❌ Товар не найден в БД.", show_alert=True)
    
    await admin_cb_del_prod_list(callback, state, bot)


# === НОВЫЕ ХЕНДЛЕРЫ: Управление Блокировками (С ЮЗЕРНЕЙМАМИ) ===

@router.callback_query(F.data == "admin:block_menu", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_block_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🚫 **Управление Блокировками**\n\nВыберите действие:",
        reply_markup=get_block_menu_keyboard()
    )
    await callback.answer()

# --- FSM для Блокировки ---
@router.callback_query(F.data == "block:add", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_block_user_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_block_id)
    await callback.message.edit_text(
        "➕ **Блокировка пользователя**\n\n"
        "Отправьте **ID** пользователя ИЛИ его **@username**.\n\n"
        "Для отмены введите /cancel.",
        reply_markup=get_admin_back_keyboard()
    )
    await callback.answer()

@router.message(StateFilter(AdminStates.waiting_for_block_id), F.text, F.from_user.id.in_(ADMIN_IDS))
async def process_block_user(message: types.Message, state: FSMContext):
    input_text = message.text.strip()
    user_id_to_block = None
    
    # Проверяем, число ли это (ID)
    if input_text.isdigit():
        user_id_to_block = int(input_text)
    else:
        # Если не число, пробуем найти по юзернейму
        user_id_to_block = get_user_id_by_username(input_text)
        if not user_id_to_block:
            await message.answer(
                f"❌ Пользователь с юзернеймом `{input_text}` не найден в базе бота.\n"
                f"Бот должен знать пользователя (он должен был запускать бота), чтобы забанить по нику.\n"
                f"Попробуйте ввести ID."
            )
            return

    if user_id_to_block in ADMIN_IDS:
        await message.answer("❌ Нельзя заблокировать администратора.")
        return

    if add_to_blacklist_db(user_id_to_block):
        await message.answer(f"✅ **Пользователь `{user_id_to_block}` успешно заблокирован.**")
    else:
        await message.answer("❌ Ошибка при добавлении в БД.")
    
    await state.clear()
    await message.answer("🚫 **Управление Блокировками**", reply_markup=get_block_menu_keyboard())


# --- FSM для Разблокировки ---
@router.callback_query(F.data == "block:remove", F.from_user.id.in_(ADMIN_IDS))
async def admin_cb_unblock_user_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_unblock_id)
    await callback.message.edit_text(
        "➖ **Разблокировка пользователя**\n\n"
        "Отправьте **ID** пользователя ИЛИ его **@username**.\n\n"
        "Для отмены введите /cancel.",
        reply_markup=get_admin_back_keyboard()
    )
    await callback.answer()

@router.message(StateFilter(AdminStates.waiting_for_unblock_id), F.text, F.from_user.id.in_(ADMIN_IDS))
async def process_unblock_user(message: types.Message, state: FSMContext):
    input_text = message.text.strip()
    user_id_to_unblock = None
    
    if input_text.isdigit():
        user_id_to_unblock = int(input_text)
    else:
        user_id_to_unblock = get_user_id_by_username(input_text)
        if not user_id_to_unblock:
            await message.answer(f"❌ Пользователь `{input_text}` не найден в базе.")
            return
    
    if remove_from_blacklist_db(user_id_to_unblock):
        await message.answer(f"✅ **Пользователь `{user_id_to_unblock}` успешно разблокирован.**")
    else:
        await message.answer(f"❌ Пользователь `{user_id_to_unblock}` не найден в черном списке.")
    
    await state.clear()
    await message.answer("🚫 **Управление Блокировками**", reply_markup=get_block_menu_keyboard())

# --- Общий /cancel для FSM админа (включая товары) ---
@router.message(Command("cancel"), StateFilter(AdminStates), F.from_user.id.in_(ADMIN_IDS))
async def cmd_cancel_admin_fsm(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🚫 Действие отменено. Возврат в Админ-панель.",
        reply_markup=get_admin_main_keyboard()
    )

# ----------------------------------------------------------------------
# --- ХЕНДЛЕРЫ ОБРАБОТКИ ЗАКАЗОВ (Подтвердить/Отклонить) ---
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("admin:confirm:"), F.from_user.id.in_(ADMIN_IDS))
async def admin_confirm_order(callback: types.CallbackQuery, bot: Bot):
    await bot.send_chat_action(chat_id=callback.from_user.id, action=ChatAction.TYPING)
    
    order_id = callback.data.split(":")[-1]
    
    order_data = get_order_db(order_id)
    if not order_data or order_data['status'] != 'pending':
        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n*Заказ `{order_data.get('short_id', '???')}` уже был обработан.*",
            reply_markup=None
        )
        await callback.answer("Заказ уже обработан.", show_alert=True)
        return

    update_order_status_db(order_id, "confirmed")
    admin_user = callback.from_user.username or f"ID: {callback.from_user.id}"
    short_id = order_data['short_id']
    
    # --- ЛОГИКА: Проверка Реферала ---
    try:
        buyer_id = order_data['user_id']
        buyer_data = get_user_data_db(buyer_id)
        
        if buyer_data and buyer_data['referrer_id'] and not buyer_data['has_purchased']:
            
            set_user_has_purchased(buyer_id)
            referrer_id = buyer_data['referrer_id']
            new_ref_count = increment_referrer_count(referrer_id)
            
            if new_ref_count >= 5:
                promo_code = f"REF-{str(uuid.uuid4())[:6].upper()}"
                add_promo_db(promo_code, 75, is_reusable=False, owner_id=referrer_id)
                reset_referral_count(referrer_id)
                
                await bot.send_message(
                    referrer_id,
                    f"🎉 **ПОЗДРАВЛЯЕМ!** 🎉\n\n"
                    f"5 ваших рефералов совершили покупку!\n"
                    f"🎁 Ваш приз: **Одноразовый промокод на 75% скидки!**\n\n"
                    f"Код: `{promo_code}`\n\n"
                    f"Ваш счетчик сброшен, можете приглашать новых друзей!"
                )
    except Exception as e:
        logging.error(f"Referral system error during order confirmation: {e}")
    # --- КОНЕЦ Логики Реферала ---

    try:
        await bot.send_message(
            order_data["user_id"],
            f"✅ **Ваш заказ #{short_id} ПОДТВЕРЖДЕН!**\n\n"
            f"Товар: {order_data['product']} ({order_data['weight']})\n"
            f"Администратор скоро свяжется с вами для выдачи товара."
        )
    except Exception as e:
        logging.warning(f"Failed to notify user {order_data['user_id']} about confirmation: {e}")
    
    await callback.message.edit_caption(
        caption=callback.message.caption + f"\n\n✅ **ПОДТВЕРЖДЕН** админом @{admin_user}",
        reply_markup=None
    )
    await callback.answer(f"Заказ #{short_id} подтвержден!")

@router.callback_query(F.data.startswith("admin:decline:"), F.from_user.id.in_(ADMIN_IDS))
async def admin_decline_order(callback: types.CallbackQuery, bot: Bot):
    await bot.send_chat_action(chat_id=callback.from_user.id, action=ChatAction.TYPING)
    
    order_id = callback.data.split(":")[-1]

    order_data = get_order_db(order_id)
    if not order_data or order_data['status'] != 'pending':
        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n*Заказ `{order_data.get('short_id', '???')}` уже был обработан.*",
            reply_markup=None
        )
        await callback.answer("Заказ уже обработан.", show_alert=True)
        return

    update_order_status_db(order_id, "declined")
    admin_user = callback.from_user.username or f"ID: {callback.from_user.id}"
    short_id = order_data['short_id']

    try:
        await bot.send_message(
            order_data["user_id"],
            f"❌ **Ваш заказ #{short_id} ОТКЛОНЕН.**\n\n"
            f"Товар: {order_data['product']} ({order_data['weight']})\n"
            f"Пожалуйста, свяжитесь с администратором для уточнения деталей."
        )
    except Exception as e:
        logging.warning(f"Failed to notify user {order_data['user_id']} about rejection: {e}")
    
    await callback.message.edit_caption(
        caption=callback.message.caption + f"\n\n❌ **ОТКЛОНЕН** админом @{admin_user}",
        reply_markup=None
    )
    await callback.answer(f"Заказ #{short_id} отклонен!", show_alert=True)

# ----------------------------------------------------------------------

# !!! НОВЫЙ КЛАСС Middleware для передачи 'dp'
class DpMiddleware:
    """
    Middleware для передачи объекта Dispatcher в хендлеры.
    Это необходимо для работы FSM (получения FSMContext) для *других* пользователей.
    """
    def __init__(self, dp: Dispatcher):
        self.dp = dp

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        # Передаем 'dp' в data, чтобы он был доступен в хендлере
        data['dp'] = self.dp
        return await handler(event, data)

# === НОВЫЙ КЛАСС Middleware: Проверка Черного Списка ===
class BlacklistMiddleware:
    """
    Middleware для блокировки пользователей из черного списка.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        user_id = None
        if isinstance(event, types.Message):
            user_id = event.from_user.id
        elif isinstance(event, types.CallbackQuery):
            user_id = event.from_user.id
        
        if user_id:
            # Админов не блокируем
            if user_id in ADMIN_IDS:
                return await handler(event, data)
            
            # Проверяем остальных в БД
            if is_user_blacklisted_db(user_id):
                logging.warning(f"Ignored event from blacklisted user: {user_id}")
                return # Просто игнорируем
        
        # Если юзер не в ЧС, продолжаем
        return await handler(event, data)


# --- Запуск бота --
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Мини-сервер для "обмана" Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_health_check():
    # Render сам передает порт в переменную окружения PORT
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()
    
async def main():
    threading.Thread(target=run_health_check, daemon=True).start()

    default_properties = DefaultBotProperties(parse_mode="Markdown")
    bot = Bot(token=BOT_TOKEN, default=default_properties)

    storage = MemoryStorage()

    dp = Dispatcher(storage=storage)

    dp.include_router(router)

    # === РЕГИСТРАЦИЯ MIDDLEWARE ===
    # Исправляем регистрацию (см. пункт 3 ниже)
    dp.message.middleware(DpMiddleware(dp))
    dp.callback_query.middleware(DpMiddleware(dp))

    dp.message.middleware(BlacklistMiddleware())
    dp.callback_query.middleware(BlacklistMiddleware())

    init_db()
    logging.info("Database initialized.")

    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить/Перезапустить бота"),
        BotCommand(command="admin", description="[Только для Админов] Админ-панель")
    ])

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    try:
        logging.info("Bot starting...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped.")
