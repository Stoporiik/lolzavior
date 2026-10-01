# -*- coding: utf-8 -*-
import os
import re
import json
import random
import string
import sqlite3
import asyncio
import time
import html
import logging
import datetime
from typing import Dict, Any, Optional, List

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.markdown import hbold, hlink, hcode
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest

# --- Telethon (аккаунт-наблюдатель за NFT). Необязателен: без него бот работает как раньше.
try:
    from telethon import TelegramClient, utils as tl_utils, events
    from telethon.sessions import StringSession
    from telethon.tl.functions.payments import (
        GetSavedStarGiftsRequest,
        GetUniqueStarGiftRequest,
    )
    from telethon.tl.types import StarGiftUnique
    from telethon.errors import FloodWaitError
    TELETHON_AVAILABLE = True
except Exception as _e:  # pragma: no cover - окружение без telethon
    TELETHON_AVAILABLE = False
    _TELETHON_IMPORT_ERROR = _e

# =======================
# TELEGRAM BOT SETTINGS
# =======================
BOT_TOKEN = "8655002668:AAGT_4hvDnoW_Tb72tI5607wvjx4oUY-Hr0"
# Владелец №1 — получает ТОЛЬКО тревогу NFT-наблюдателя (send_fraud_alert).
OWNER_ID = 8597498587
# Владелец №2 — получает ЛЕНТУ ВСЕХ сделок (продавец/покупатель/сумма/ссылки/мемо/статус)
# и команду /deals (история). Впиши сюда Telegram-ID второго владельца.
# Пока стоит 0 — лента просто не отправляется (ошибок нет).
OWNER2_ID = 8597498587
BASE_DIR = os.path.dirname(__file__)
IMG_DIR = os.path.join(BASE_DIR, "images")
DB_PATH = os.path.join(BASE_DIR, "bot.db")

# PAYMENT_PROVIDER_TOKEN: если есть — вставь сюда, чтобы инвойсы работали авьтоматически
PAYMENT_PROVIDER_TOKEN = ""  # <- поставь токен провайдера, если есть

# STATIC TON ADDRESS
STATIC_TON = "UQBiVvFujn-8UuKEgY_ASZOZpb5ZRGmGOwTedvYGw1DLY4E2"
CONFETTI_EFFECT_ID = ""

LOG_CHAT_ID = -1004492555651   # ID вашей супергруппы (отрицательное число)
LOG_THREAD_ID = 1257     # ID темы, куда будут приходить логи (если не нужна тема, поставьте N

# =======================
# NFT-НАБЛЮДАТЕЛЬ (Telethon-юзербот)
# =======================
# Аккаунт-наблюдатель смотрит, кому продавец передал свои NFT-подарки после
# подключения покупателя. Если подарок ушёл НЕ саппорту (@SUPPORT_USERNAME),
# а покупателю или третьему лицу в течение WATCH_DURATION_SEC — владельцу
# уходит срочное уведомление.
# Данные для входа берутся по приоритету:
#   1) константы ниже (USERBOT_SESSION и т.д.)
#   2) переменные окружения USERBOT_API_ID / USERBOT_API_HASH / USERBOT_SESSION
#   3) файл userbot_session.json рядом с bot.py (его создаёт login_userbot.py)
#   4) bot.db (если вход делали прямо на этой машине)
# РЕКОМЕНДУЕМЫЙ способ для хостинга: войти дома через login_userbot.py → он напечатает
# строку-сессию и создаст userbot_session.json. Дальше либо залей этот .json рядом
# с bot.py, либо вставь строку-сессию в USERBOT_SESSION ниже. Повторный вход на
# хостинге не нужен — код из Telegram там вводить НЕ придётся.
USERBOT_API_ID = ""
USERBOT_API_HASH = ""
USERBOT_SESSION = ""    # ← вставь сюда строку-сессию из login_userbot.py (или используй .json)

SUPPORT_USERNAME_DEFAULT = "FunPay_Sapports"   # кому продавцы ОБЯЗАНЫ отдавать NFT (без
WATCH_DURATION_SEC = 30 * 60             # сколько следим за сделкой (30 минут)
WATCH_POLL_INTERVAL_SEC = 25             # как часто опрашивать владельцев подарков, сек
WATCH_MAX_SNAPSHOT_GIFTS = 100           # сколько NFT профиля брать в «слепок»

# =======================
# РЕФЕРАЛЬНАЯ ПРОГРАММА
# =======================
# Вознаграждение пригласившему начисляется при ЗАВЕРШЕНИИ сделки (подарок передан).
# Считается только для сделок в TON, потому что баланс «Заработано» показывается в TON.
#   reward = amount * DEAL_COMMISSION_PERCENT/100 * REFERRAL_SHARE_PERCENT/100
# Пример: сделка 100 TON, комиссия 5% = 5 TON, реферал получает 50% = 2.5 TON.
# Поставь DEAL_COMMISSION_PERCENT = 0, чтобы отключить начисление (счётчик рефералов
# продолжит работать в любом случае).
DEAL_COMMISSION_PERCENT = 5.0    # сервисная комиссия с суммы сделки, %
REFERRAL_SHARE_PERCENT = 50.0    # доля комиссии, которую получает пригласивший, %

# =======================
# FILES
# =======================
START_IMAGE = "start.jpg"
DEFAULT_IMAGE = "start.jpg"
ADMIN_IMAGE = "start.jpg"
ACK_IMAGE = "start.jpg"
CREATE_DEAL_IMAGE = "start.jpg"
MANAGE_IMAGE = "start.jpg"
DEAL_VIDEO = "start.jpg"
CURRENCY_VIDEO = "start.jpg"
JOIN_VIDEO = "start.jpg"


# =======================
# STATES
# =======================
class UserStates(StatesGroup):
    waiting_ton = State()
    waiting_card = State()
    waiting_stars_username = State()
    waiting_usdt = State()
    waiting_btc = State()
    waiting_gift_link = State()
    waiting_description = State()
    waiting_amount = State()
    waiting_pay_ton_for_deal = State()
    waiting_deal_code = State()
    waiting_search_code = State()
    # Продавец подключается к сделке, созданной покупателем, и вводит реквизит
    # для получения оплаты (кошелёк/карта/Stars-username).
    waiting_join_requisite = State()
    # Новичок впервые вошёл в сделку — читает предупреждение о безопасности и
    # проходит проверочный вопрос перед тем, как сделка продолжится.
    onboarding = State()


class OwnerStates(StatesGroup):
    waiting_block_user = State()
    waiting_unblock_user = State()
    waiting_broadcast_admins = State()
    waiting_mirror_token = State()
    # Рассылка ВСЕМ пользователям: сначала пост, затем (опционально) кнопки.
    waiting_broadcast_all = State()
    waiting_broadcast_all_button = State()


# =======================
# BOT INIT
# =======================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
mirror_bots: Dict[str, Bot] = {}
polling_task: Optional[asyncio.Task] = None
polling_restart_lock = asyncio.Lock()
# Время запуска бота (unix). Сообщения старше игнорируются — см. IgnoreStaleMiddleware.
BOT_START_TIME = 0.0


# =======================
# АНТИ-ФЛУД (throttling)
# =======================
# При лавине запросов (например, многократный /start) бот раньше пытался
# обработать каждый апдейт — слал кучу запросов к Telegram (delete/answer и т.п.),
# упирался в лимиты Telegram и падал по таймаутам. Этот middleware молча
# отбрасывает слишком частые события от одного пользователя ДО входа в
# обработчик, поэтому лишних запросов к Telegram не делается и нагрузка резко
# падает. Считается отдельно по каждому боту (основной + зеркала). Владелец не
# троттлится, чтобы админ-панель всегда отвечала.
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit: float = 0.7):
        self.rate_limit = rate_limit
        self._last: Dict[Any, float] = {}
        self._calls = 0

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user") or getattr(event, "from_user", None)
        if user is None or user.id == OWNER_ID:
            return await handler(event, data)

        bot_obj = data.get("bot")
        key = (getattr(bot_obj, "id", 0), user.id)
        now = time.monotonic()
        last = self._last.get(key, 0.0)
        self._last[key] = now

        # периодическая чистка старых записей, чтобы словарь не рос бесконечно
        self._calls += 1
        if self._calls >= 1000:
            self._calls = 0
            cutoff = now - 60.0
            for stale in [k for k, v in self._last.items() if v < cutoff]:
                self._last.pop(stale, None)

        if now - last < self.rate_limit:
            return None  # слишком часто — игнорируем без обращения к Telegram
        return await handler(event, data)


# Игнорировать сообщения, пришедшие ДО запуска бота (накопились, пока он был
# офлайн/перезапускался). drop_pending_updates сбрасывает очередь на стороне
# Telegram, а этот фильтр — страховка: даже если старый апдейт просочится, бот
# просто его не увидит и пойдёт к новым.
class IgnoreStaleMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        ts = getattr(event, "date", None)
        if BOT_START_TIME and ts is not None:
            try:
                if ts.timestamp() < BOT_START_TIME:
                    return None  # старое сообщение — игнорируем
            except Exception:
                pass
        return await handler(event, data)


# outer_middleware: срабатывает ДО фильтров/обработчиков. Порядок важен —
# сначала отсекаем старые сообщения, затем анти-флуд. Раздельные экземпляры
# троттла для сообщений и кнопок.
dp.message.outer_middleware(IgnoreStaleMiddleware())
dp.message.outer_middleware(ThrottlingMiddleware(rate_limit=0.7))
dp.callback_query.outer_middleware(ThrottlingMiddleware(rate_limit=0.5))


def get_active_bot() -> Bot:
    try:
        return Bot.get_current()
    except Exception:
        return bot


# =======================
# DATABASE HELPERS
# =======================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        wallet TEXT,
        card TEXT,
        stars_username TEXT,
        lang TEXT DEFAULT 'RU',
        successful INTEGER DEFAULT 0,
        username TEXT,
        ref_code TEXT,
        earned REAL DEFAULT 0,
        blocked INTEGER DEFAULT 0,
        referred_by INTEGER
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deals (
        code TEXT PRIMARY KEY,
        seller_id INTEGER,
        buyer_id INTEGER,
        description TEXT,
        gift_link TEXT,
        amount REAL,
        currency TEXT,
        seller_wallet TEXT,
        seller_card TEXT,
        confirmed INTEGER DEFAULT 0,
        notified INTEGER DEFAULT 0,
        paid INTEGER DEFAULT 0,
        delivered INTEGER DEFAULT 0,
        creator_id INTEGER,
        creator_role TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mirrors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id INTEGER,
        username TEXT,
        token TEXT UNIQUE,
        is_active INTEGER DEFAULT 1
    )
    """)
    # NFT-наблюдатель: активные «слежки» за подарками продавца по сделке.
    # slugs — JSON-список slug'ов NFT (t.me/nft/<slug>), которые отслеживаем.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS gift_watch (
        code TEXT PRIMARY KEY,
        seller_id INTEGER,
        buyer_id INTEGER,
        slugs TEXT,
        baseline TEXT,
        started_at REAL,
        expires_at REAL,
        alerted INTEGER DEFAULT 0,
        done INTEGER DEFAULT 0
    )
    """)
    # Журнал сообщений поддержки пользователю (команда /chat).
    # Нужен для лимита «не больше 10 сообщений на пользователя в рамках одной сделки».
    cur.execute("""
    CREATE TABLE IF NOT EXISTS support_chat (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deal_code TEXT,
        user_id INTEGER,
        admin_id INTEGER,
        text TEXT,
        ts REAL
    )
    """)
    # migrate older schema: add missing columns if needed
    cur.execute("PRAGMA table_info(users)")
    cols = [r[1] for r in cur.fetchall()]
    if 'earned' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN earned REAL DEFAULT 0")
    if 'ref_code' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN ref_code TEXT")
    if 'blocked' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
    if 'stars_username' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN stars_username TEXT")
    if 'usdt_wallet' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN usdt_wallet TEXT")
    if 'btc_wallet' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN btc_wallet TEXT")
    if 'referred_by' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
    if 'balances' not in cols:
        # Мультивалютный баланс пользователя в формате JSON {"RUB": 2500.0, "TON": 1.5}
        cur.execute("ALTER TABLE users ADD COLUMN balances TEXT DEFAULT '{}'")
    # Прошёл ли пользователь обучение безопасности при ПЕРВОМ входе в сделку —
    # отдельно как продавец и как покупатель. 0 = ещё не проходил (покажем онбординг).
    if 'onboarded_seller' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN onboarded_seller INTEGER DEFAULT 0")
    if 'onboarded_buyer' not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN onboarded_buyer INTEGER DEFAULT 0")
    cur.execute("PRAGMA table_info(deals)")
    deal_cols = [r[1] for r in cur.fetchall()]
    if 'notified' not in deal_cols:
        cur.execute("ALTER TABLE deals ADD COLUMN notified INTEGER DEFAULT 0")
    if 'paid' not in deal_cols:
        cur.execute("ALTER TABLE deals ADD COLUMN paid INTEGER DEFAULT 0")
    if 'delivered' not in deal_cols:
        cur.execute("ALTER TABLE deals ADD COLUMN delivered INTEGER DEFAULT 0")
    # Кто и в какой роли создал сделку. Нужно, чтобы НЕ путать покупателя/продавца:
    # создатель может быть как продавцом, так и покупателем, а подключившийся —
    # противоположной ролью. seller_id/buyer_id всегда = реальная роль.
    if 'creator_id' not in deal_cols:
        cur.execute("ALTER TABLE deals ADD COLUMN creator_id INTEGER")
    if 'creator_role' not in deal_cols:
        cur.execute("ALTER TABLE deals ADD COLUMN creator_role TEXT")
    cur.execute("PRAGMA table_info(gift_watch)")
    gw_cols = [r[1] for r in cur.fetchall()]
    if 'baseline' not in gw_cols:
        cur.execute("ALTER TABLE gift_watch ADD COLUMN baseline TEXT")
    conn.commit()
    conn.close()


def get_setting(key: str) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def get_mirrors(active_only: bool = False) -> list:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    query = "SELECT id, bot_id, username, token, is_active FROM mirrors"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY id"
    cur.execute(query)
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": row[0],
            "bot_id": row[1],
            "username": row[2],
            "token": row[3],
            "is_active": bool(row[4]),
        }
        for row in rows
    ]


def get_mirror(mirror_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, bot_id, username, token, is_active FROM mirrors WHERE id = ?", (mirror_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "bot_id": row[1],
        "username": row[2],
        "token": row[3],
        "is_active": bool(row[4]),
    }


def get_mirror_by_token(token: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, bot_id, username, token, is_active FROM mirrors WHERE token = ?", (token,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "bot_id": row[1],
        "username": row[2],
        "token": row[3],
        "is_active": bool(row[4]),
    }


def save_mirror(bot_id: int, username: Optional[str], token: str, is_active: bool = True) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO mirrors (bot_id, username, token, is_active) VALUES (?, ?, ?, ?)",
        (bot_id, username, token, int(is_active)),
    )
    conn.commit()
    mirror_id = cur.lastrowid
    conn.close()
    return mirror_id


def update_mirror_status(mirror_id: int, is_active: bool):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE mirrors SET is_active = ? WHERE id = ?", (int(is_active), mirror_id))
    conn.commit()
    conn.close()


def delete_mirror(mirror_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM mirrors WHERE id = ?", (mirror_id,))
    conn.commit()
    conn.close()


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, wallet, card, stars_username, lang, successful, username, ref_code, earned, blocked, usdt_wallet, btc_wallet, referred_by, balances, onboarded_seller, onboarded_buyer "
        "FROM users WHERE user_id = ?",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0],
            'wallet': row[1],
            'card': row[2],
            'stars_username': row[3],
            'lang': row[4] or 'RU',
            'successful': row[5] or 0,
            'username': row[6],
            'ref_code': row[7],
            'earned': float(row[8] or 0),
            'blocked': bool(row[9] if len(row) > 9 else 0),
            'usdt_wallet': row[10] if len(row) > 10 else None,
            'btc_wallet': row[11] if len(row) > 11 else None,
            'referred_by': row[12] if len(row) > 12 else None,
            'balances': _parse_balances(row[13] if len(row) > 13 else None),
            'onboarded_seller': bool(row[14]) if len(row) > 14 else False,
            'onboarded_buyer': bool(row[15]) if len(row) > 15 else False,
        }
    return None


def get_user_by_ref(ref_code: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, wallet, card, stars_username, lang, successful, username, ref_code, earned, blocked, usdt_wallet, btc_wallet, referred_by, balances, onboarded_seller, onboarded_buyer "
        "FROM users WHERE ref_code = ?",
        (ref_code,)
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0],
            'wallet': row[1],
            'card': row[2],
            'stars_username': row[3],
            'lang': row[4] or 'RU',
            'successful': row[5] or 0,
            'username': row[6],
            'ref_code': row[7],
            'earned': float(row[8] or 0),
            'blocked': bool(row[9] if len(row) > 9 else 0),
            'usdt_wallet': row[10] if len(row) > 10 else None,
            'btc_wallet': row[11] if len(row) > 11 else None,
            'referred_by': row[12] if len(row) > 12 else None,
            'balances': _parse_balances(row[13] if len(row) > 13 else None),
            'onboarded_seller': bool(row[14]) if len(row) > 14 else False,
            'onboarded_buyer': bool(row[15]) if len(row) > 15 else False,
        }
    return None


def save_user(user_data: Dict[str, Any]):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    user_id = user_data['user_id']
    wallet = user_data.get('wallet')
    card = user_data.get('card')
    stars_username = user_data.get('stars_username')
    lang = user_data.get('lang', 'RU')
    successful = user_data.get('successful', 0)
    username = user_data.get('username')
    ref_code = user_data.get('ref_code', f"ref{user_id}")
    earned = float(user_data.get('earned', 0))
    blocked = int(user_data.get('blocked', 0))
    usdt_wallet = user_data.get('usdt_wallet')
    btc_wallet = user_data.get('btc_wallet')
    referred_by = user_data.get('referred_by')
    balances = json.dumps(_parse_balances_value(user_data.get('balances')), ensure_ascii=False)
    # ВАЖНО: INSERT OR REPLACE переписывает всю строку — флаги онбординга обязательно
    # передаём явно, иначе они сбросятся в 0 при любом сохранении пользователя.
    onboarded_seller = int(bool(user_data.get('onboarded_seller', 0)))
    onboarded_buyer = int(bool(user_data.get('onboarded_buyer', 0)))
    cur.execute("""
    INSERT OR REPLACE INTO users (user_id, wallet, card, stars_username, lang, successful, username, ref_code, earned, blocked, usdt_wallet, btc_wallet, referred_by, balances, onboarded_seller, onboarded_buyer)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, wallet, card, stars_username, lang, successful, username, ref_code, earned, blocked, usdt_wallet, btc_wallet, referred_by, balances, onboarded_seller, onboarded_buyer))
    conn.commit()
    conn.close()


# =======================
# Онбординг безопасности (первый вход в сделку)
# =======================
def is_onboarded(user_id: int, role: str) -> bool:
    """Прошёл ли пользователь обучение безопасности для данной роли ('seller'/'buyer')."""
    col = 'onboarded_seller' if role == 'seller' else 'onboarded_buyer'
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT {col} FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


def mark_onboarded(user_id: int, role: str):
    """Отмечает, что пользователь прошёл обучение для роли. Прямой UPDATE, чтобы
    не затронуть остальные поля профиля."""
    col = 'onboarded_seller' if role == 'seller' else 'onboarded_buyer'
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE users SET {col} = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def user_has_deal_history(user_id: int, exclude_code: Optional[str] = None) -> bool:
    """Участвовал ли пользователь в каких-либо сделках раньше (как продавец, покупатель
    или создатель). exclude_code — исключить текущую сделку из проверки."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        if exclude_code:
            cur.execute("SELECT 1 FROM deals WHERE (seller_id=? OR buyer_id=? OR creator_id=?) "
                        "AND code<>? LIMIT 1", (user_id, user_id, user_id, exclude_code))
        else:
            cur.execute("SELECT 1 FROM deals WHERE (seller_id=? OR buyer_id=? OR creator_id=?) LIMIT 1",
                        (user_id, user_id, user_id))
        return cur.fetchone() is not None
    finally:
        conn.close()


def needs_onboarding(user_id: int, role: str, exclude_code: Optional[str] = None) -> bool:
    """Показывать ли обучение — ТОЛЬКО настоящим новичкам в данной роли. Пропускаем,
    если пользователь уже проходил обучение, имеет завершённые сделки (successful>0)
    или уже участвовал в каких-либо сделках раньше (не новичок)."""
    if is_onboarded(user_id, role):
        return False
    user = get_user(user_id) or {}
    if int(user.get('successful', 0) or 0) > 0:
        return False
    if user_has_deal_history(user_id, exclude_code):
        return False
    return True


# =======================
# Баланс пользователя (мультивалютный)
# Хранится в users.balances как JSON {"RUB": 2500.0, "TON": 1.5}.
# Баланс ведётся в валюте сделки и складывается из суммы завершённых сделок,
# а владелец может вручную добавлять/убавлять/обнулять баланс командой /balance.
# =======================
FIAT_CURRENCIES = {"RUB", "UAH", "KZT", "BYN"}
KNOWN_CURRENCIES = FIAT_CURRENCIES | {"TON", "USDT", "BTC", "STARS"}
CURRENCY_EMOJI = {
    "RUB": "🇷🇺", "UAH": "🇺🇦", "KZT": "🇰🇿", "BYN": "🇧🇾",
    "TON": "💎", "USDT": "🪙", "BTC": "🪙", "STARS": "⭐",
}


def _parse_balances_value(value) -> Dict[str, float]:
    """Принимает dict или JSON-строку → чистый словарь {ВАЛЮТА: сумма}.

    Нулевые/битые значения отбрасываются, ключи приводятся к верхнему регистру.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    if not isinstance(value, dict):
        return {}
    result: Dict[str, float] = {}
    for cur_code, val in value.items():
        try:
            amt = float(val)
        except (TypeError, ValueError):
            continue
        if abs(amt) > 1e-9:
            result[str(cur_code).upper()] = amt
    return result


def _parse_balances(raw) -> Dict[str, float]:
    """JSON-строка из БД → словарь балансов {ВАЛЮТА: сумма}."""
    return _parse_balances_value(raw)


def _fmt_money(amount: float, currency: str) -> str:
    """Аккуратное число: фиат — до 2 знаков, крипта — до 8, без лишних нулей."""
    currency = (currency or "").upper()
    digits = 2 if currency in FIAT_CURRENCIES or currency == "STARS" else 8
    s = f"{float(amount):.{digits}f}".rstrip("0").rstrip(".")
    return s or "0"


def add_user_balance(user_id: int, currency: str, delta: float) -> Dict[str, float]:
    """Изменить баланс пользователя в указанной валюте на delta (может быть < 0).

    Возвращает обновлённый словарь балансов. Если пользователя нет — создаёт запись.
    """
    currency = (currency or "").upper()
    user = get_user(user_id) or {'user_id': user_id, 'lang': 'RU', 'ref_code': f"ref{user_id}"}
    balances = dict(user.get('balances') or {})
    new_val = float(balances.get(currency, 0.0)) + float(delta)
    if abs(new_val) > 1e-9:
        balances[currency] = new_val
    else:
        balances.pop(currency, None)
    user['balances'] = balances
    save_user(user)
    return balances


def set_user_balance(user_id: int, currency: str, value: float) -> Dict[str, float]:
    """Установить баланс пользователя в указанной валюте равным value."""
    currency = (currency or "").upper()
    user = get_user(user_id) or {'user_id': user_id, 'lang': 'RU', 'ref_code': f"ref{user_id}"}
    balances = dict(user.get('balances') or {})
    if abs(float(value)) > 1e-9:
        balances[currency] = float(value)
    else:
        balances.pop(currency, None)
    user['balances'] = balances
    save_user(user)
    return balances


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Найти пользователя по @username (без учёта регистра)."""
    normalized = (username or "").lstrip("@").strip()
    if not normalized:
        return None
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE username = ? COLLATE NOCASE", (normalized,))
    row = cur.fetchone()
    conn.close()
    return get_user(row[0]) if row else None


def _sorted_balance_items(balances: Dict[str, float]):
    """Валюты по порядку: сначала фиат, потом крипта, внутри — по убыванию суммы."""
    items = [(c, a) for c, a in (balances or {}).items() if abs(a) > 1e-9]
    items.sort(key=lambda x: (x[0] not in FIAT_CURRENCIES, -x[1]))
    return items


def get_all_users() -> list:
    """Получить всех пользователей"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username FROM users ORDER BY user_id")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_active_user_ids() -> list:
    """ID всех НЕзаблокированных пользователей — целевая аудитория рассылки."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE blocked = 0 OR blocked IS NULL ORDER BY user_id")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


def get_users_count() -> int:
    """Сколько всего людей запускало бота (каждый /start создаёт запись в users)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    n = cur.fetchone()[0]
    conn.close()
    return int(n or 0)


def get_users_stats() -> Dict[str, int]:
    """Сводка по пользователям: всего / заблокировано / активно."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total = int(cur.fetchone()[0] or 0)
    cur.execute("SELECT COUNT(*) FROM users WHERE blocked = 1")
    blocked = int(cur.fetchone()[0] or 0)
    conn.close()
    return {"total": total, "blocked": blocked, "active": total - blocked}


def count_referrals(user_id: int) -> int:
    """Сколько пользователей пришло по реферальной ссылке этого пользователя."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
    n = cur.fetchone()[0]
    conn.close()
    return int(n or 0)


def get_admins() -> set:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM admins")
    admins = {row[0] for row in cur.fetchall()}
    conn.close()
    return admins


def get_all_admins() -> list:
    """Получить всех админов (включая владельца)"""
    admins = list(get_admins())
    if OWNER_ID not in admins:
        admins.append(OWNER_ID)
    return admins


def save_admin(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


def remove_admin(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_deal(code: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM deals WHERE code = ?", (code,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            'code': row[0],
            'seller_id': row[1],
            'buyer_id': row[2],
            'description': row[3],
            'gift_link': row[4],
            'amount': row[5],
            'currency': row[6],
            'seller_wallet': row[7],
            'seller_card': row[8],
            'confirmed': bool(row[9]),
            'notified': bool(row[10]) if len(row) > 10 else False,
            'paid': bool(row[11]) if len(row) > 11 else False,
            'delivered': bool(row[12]) if len(row) > 12 else False,
            'creator_id': row[13] if len(row) > 13 else None,
            'creator_role': row[14] if len(row) > 14 else None
        }
    return None


def save_deal(deal_data: Dict[str, Any]):
    code = deal_data.get('code')
    if not code:
        logging.error("save_deal called without 'code'. Skipping.")
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    notified = int(deal_data.get('notified', False))
    paid = int(deal_data.get('paid', False))
    delivered = int(deal_data.get('delivered', False))
    cur.execute("""
    INSERT OR REPLACE INTO deals
      (code, seller_id, buyer_id, description, gift_link, amount, currency, seller_wallet, seller_card, confirmed, notified, paid, delivered, creator_id, creator_role)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        code,
        deal_data.get('seller_id'),
        deal_data.get('buyer_id'),
        deal_data.get('description'),
        deal_data.get('gift_link'),
        deal_data.get('amount'),
        deal_data.get('currency'),
        deal_data.get('seller_wallet'),
        deal_data.get('seller_card'),
        int(deal_data.get('confirmed', False)),
        notified,
        paid,
        delivered,
        deal_data.get('creator_id'),
        deal_data.get('creator_role')
    ))
    conn.commit()
    conn.close()


def delete_deal(code: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM deals WHERE code = ?", (code,))
    conn.commit()
    conn.close()


def get_all_deals() -> Dict[str, Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM deals")
    rows = cur.fetchall()
    conn.close()
    deals = {}
    for row in rows:
        code = row[0]
        deals[code] = {
            'code': code,
            'seller_id': row[1],
            'buyer_id': row[2],
            'description': row[3],
            'gift_link': row[4],
            'amount': row[5],
            'currency': row[6],
            'seller_wallet': row[7],
            'seller_card': row[8],
            'confirmed': bool(row[9]),
            'notified': bool(row[10]) if len(row) > 10 else False,
            'paid': bool(row[11]) if len(row) > 11 else False,
            'delivered': bool(row[12]) if len(row) > 12 else False,
            'creator_id': row[13] if len(row) > 13 else None,
            'creator_role': row[14] if len(row) > 14 else None
        }
    return deals


# =======================
# CUSTOM EMOJI (премиум-эмодзи в тексте)
# -----------------------
# ПРЕМИУМ-ЭМОДЗИ — КАК ВКЛЮЧИТЬ ПО ОДНОМУ:
#   Сейчас все ID пустые → ВЕЗДЕ обычные эмодзи, бот работает без ошибок.
#   Чтобы конкретный эмодзи стал премиумным:
#     1. Перешли его боту @idstickerbot — получишь "Emoji id: <число>".
#     2. Впиши это число в кавычках в EMOJI_MAP (для текста) или BUTTON_EMOJI_MAP (для кнопок).
#   Заполняешь по одному — заполненные станут премиум, пустые останутся обычными. Само.
# ВАЖНО: неверный/чужой ID Telegram отклоняет → escalating fallback вернёт обычный эмодзи.
# =======================
USE_CUSTOM_EMOJI = True

# Запасной ID на случай, если для эмодзи/кнопки свой не задан. Оставь "" — тогда без ID
# показывается ОБЫЧНЫЙ эмодзи. Менять не нужно: задавай ID точечно в картах ниже.
DEFAULT_EMOJI_ID = ""

# Цвет всех инлайн-кнопок (Bot API 9.4+): "success" = зелёный, "primary" = синий,
# "danger" = красный, None = обычный. Поставь None, чтобы отключить.
BUTTON_STYLE = None

# ===========================================================================
# ПРЕМИУМ-ЭМОДЗИ В ТЕКСТЕ СООБЩЕНИЙ: символ эмодзи -> ТВОЙ emoji_id.
# ---------------------------------------------------------------------------
# КАК ПОЛЬЗОВАТЬСЯ:
#   1. Перешли нужный премиум-эмодзи боту @idstickerbot — он вернёт "Emoji id: <число>".
#   2. Впиши это число в КАВЫЧКАХ рядом с нужным символом ниже.
#   Пример:  "💰": "5368324170671202286",
#   Пусто ("") — этот эмодзи останется ОБЫЧНЫМ. Заполняй по одному, сколько хочешь.
# Слева — символ, который надо заменить. Справа — твой ID. В комментарии — где встречается.
# ===========================================================================
EMOJI_MAP = {
    "🏆": "5893255507380014983",   # корона — лого/заголовки
    "⚡": "5456140674028019486",   # молния — акценты, подсказки
    "🤖": "5794164805065514131",   # робот — упоминание бота
    "🛡": "5794085322400733645",   # щит — гарант/безопасность
    "💰": "5794280000383358988",   # мешок денег — баланс, сумма сделки
    "📦": "5794241397217304511",   # коробка — товар
    "📢": "5893290369629556374",   # рупор — рассылка/объявления
    "💎": "5377620962390857342",   # алмаз — премиум
    "💳": "5445353829304387411",   # карта — способ оплаты «Карта»
    "⭐️": "5438496463044752972",  # звезда (вариант 1) — Telegram Stars
    "⭐": "5267500801240092311",   # звезда (вариант 2) — Telegram Stars
    "🪙": "5379773896352355687",   # монета — крипта
    "📌": "5253961389285845297",   # кнопка — описание/важное
    "🔥": "5217822164362739968",   # огонь — акцент
    "🛒": "5312361253610475399",   # корзина — покупатель
    "🔴": "5253780051471642059",   # красный круг — пустой баланс/статус
    "💬": "5467538555158943525",   # облако — диалог/подсказка
    "✅": "5316827280863934685",   # галочка — успех/подтверждение
    "❌": "5316660455744223443",   # крест — отмена/ошибка
    "⚠️": "5316826301611390284",  # внимание — предупреждение
    "🏠": "5226513232549664618",   # дом — главное меню
    "◀️": "6039420807900303010",  # стрелка влево — назад
    "➕": "5397916757333654639",   # плюс — создать/добавить
    "💵": "5375296873982604963",   # купюра — вывод средств
    "📊": "5244837092042750681",   # график — транзакции/статистика
    "👥": "5255835635704408236",   # люди — рефералы/роли
    "🌐": "5776233299424843260",   # глобус — язык
    "💔": "5316583309541651465",   # разбитое сердце
    "🚀": "5316571734604790521",   # ракета
    "🎉": "5895713431264170680",   # конфетти — сделка создана
    "🔗": "5253490441826870592",   # звено — ссылка
    "🔍": "5429571366384842791",   # лупа — поиск сделки
    "🧾": "5197269100878907942",   # чек — реквизиты/транзакции
}

# ===========================================================================
# ПРЕМИУМ-ЭМОДЗИ НА ИНЛАЙН-КНОПКАХ: ключ кнопки -> ТВОЙ emoji_id.
# ---------------------------------------------------------------------------
# Тот же принцип: впиши ID в кавычках рядом с нужной кнопкой. Пусто ("") —
# кнопка покажет свой обычный эмодзи из текста. Слева ключ, в комментарии — что за кнопка.
# ===========================================================================
BUTTON_EMOJI_MAP = {
    "manage_details": "5258134813302332906",   # «Мои реквизиты»
    "create_deal":    "5395732581780040886",   # «Создать сделку»
    "balance":        "5375296873982604963",   # «Баланс»
    "my_deals":       "5877597667231534929",   # «Мои сделки»
    "referral":       "5253490441826870592",   # «Рефералы»
    "change_lang":    "5776233299424843260",   # «Сменить язык»
    "support":        "5893365724830765382",   # «Поддержка»
    "back_to_menu":   "6039420807900303010",   # «Назад в меню»
    "change_ton":     "5776023601941582822",   # реквизит: TON-кошелёк
    "change_bank":    "5902056028513505203",   # реквизит: банковская карта
    "receive_stars":  "5319156402973849743",   # реквизит: Stars / юзернейм
    "change_usdt":    "5316711376876485361",   # реквизит: USDT TRC20
    "change_btc":     "5258368777350816286",   # реквизит: Bitcoin
    "role_seller":    "5217822164362739968",   # «Я продавец»
    "role_buyer":     "5312361253610475399",   # «Я покупатель»
    "dm_card":        "5445353829304387411",   # способ: Карта
    "dm_stars":       "5463289097336405244",   # способ: Stars
    "dm_crypto":      "5197434882321567830",   # способ: Крипта
    "dm_back":        "5258236805890710909",   # «Назад» в выборе способа
    "withdraw":       "5375296873982604963",   # «Вывести»
    "transactions":   "5190806721286657692",   # «Транзакции»
    "search_deal":    "5893382531037794941",   # «Найти сделку»
    "set_lang":       "5253836448687204081",   # выбор языка 🇷🇺/🇺🇸
    "dc":             "",   # выбор валюты (dc_RUB, dc_TON, ...)
    "dealview":       "5257991477358763590",   # открыть сделку из списка (dealview_<код>)
    "cancel":         "5893163582194978381",   # «Отменить сделку» (cancel_<код>)
    "confirm":        "5902002809573740949",   # «Подтвердить» (confirm_<код>)
    "mdpage":         "5258389041006518073",   # пагинация в «Мои сделки»
}

# ===========================================================================
# ОТДЕЛЬНЫЕ ПРЕМИУМ-ЭМОДЗИ ДЛЯ ОНБОРДИНГА (обучение новичка при первой сделке)
# ---------------------------------------------------------------------------
# Тут ID именно для сообщений/кнопок онбординга — независимо от общей EMOJI_MAP.
# Впиши свои emoji_id в кавычках. Пусто ("") — возьмётся обычный эмодзи
# (а для текста — общий premium из EMOJI_MAP, если он там задан).
# ===========================================================================
# 1) Эмодзи в ТЕКСТЕ предупреждений и вопросов:
ONB_EMOJI_MAP = {
    "shield": "5276262671962892944",   # 🛡 — заголовок предупреждения (щит/гарант)
    "warn":   "5276240711795107620",   # ⚠️ — ключевое правило безопасности
    "cross":  "5408930028438188841",   # ❌ — запрет / «Неверно»
    "check":  "5278411813468269386",   # ✅ — правильный порядок / «Верно» / «Проверка пройдена»
    "pin":    "5253836448687204081",   # 📌 — «Небольшая проверка»
    "point":  "5877410604225924969",   # 👇 — «Попробуйте ещё раз»
}
# 2) Иконки на ИНЛАЙН-КНОПКАХ онбординга (icon_custom_emoji_id, Bot API 9.4+).
#    Одна иконка на ТИП кнопки. Для ответов иконка ОБЩАЯ (одинаковая на всех
#    вариантах) — чтобы по иконке нельзя было угадать правильный ответ.
ONB_BTN_EMOJI_MAP = {
    "reading":  "5893102202817352158",   # ⏳ — кнопка с обратным отсчётом
    "continue": "5309762875705823313",   # ✅ — «Я всё прочитал(а) и понял(а)»
    "answer":   "5456659360048516909",   # — иконка на кнопках-ответах (общая для всех вариантов)
}


def emoji_id_for(ch: str) -> str:
    return EMOJI_MAP.get(ch, DEFAULT_EMOJI_ID)


def button_emoji_id_for(callback_data: str) -> str:
    if not callback_data:
        return DEFAULT_EMOJI_ID
    if callback_data in BUTTON_EMOJI_MAP:
        return BUTTON_EMOJI_MAP[callback_data]
    prefix = callback_data.split("_", 1)[0]
    return BUTTON_EMOJI_MAP.get(prefix, DEFAULT_EMOJI_ID)


def ce(fallback: str, emoji_id: str = None) -> str:
    """Оборачивает эмодзи в премиум <tg-emoji>. ID берётся из EMOJI_MAP по символу
    (либо переданный явно). Без премиума — обычный эмодзи."""
    if not USE_CUSTOM_EMOJI:
        return fallback
    eid = emoji_id or emoji_id_for(fallback)
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback


def oe(slot: str, fallback: str) -> str:
    """Премиум-эмодзи ОНБОРДИНГА по имени слота из ONB_EMOJI_MAP. Пусто/выкл —
    возвращает обычный эмодзи (его затем может обернуть общий premiumize_text)."""
    if not USE_CUSTOM_EMOJI:
        return fallback
    eid = ONB_EMOJI_MAP.get(slot, "")
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback


_CE_ID = DEFAULT_EMOJI_ID  # обратная совместимость
_CE_CROWN   = ce("🏆")
_CE_BOLT    = ce("⚡")
_CE_BOT     = ce("🤖")
_CE_SHIELD  = ce("🛡")
_CE_MONEY   = ce("💰")
_CE_BOX     = ce("📦")
_CE_CHANNEL = ce("📢")
_CE_DIAMOND = ce("💎")
_CE_CARD    = ce("💳")
_CE_STAR    = ce("⭐️")
_CE_COIN    = ce("🪙")
_CE_PIN     = ce("📌")
_CE_FIRE    = ce("🔥")
_CE_CART    = ce("🛒")
_CE_RED     = ce("🔴")
_CE_SPEECH  = ce("💬")
_CE_CHECK   = ce("✅")
_CE_CROSS   = ce("❌")
_CE_WARN    = ce("⚠️")
_CE_HOME    = ce("🏠")
_CE_BACK    = ce("◀️")
_CE_PLUS    = ce("➕")
_CE_DOLLAR  = ce("💵")
_CE_CHART   = ce("📊")
_CE_PEOPLE  = ce("👥")
_CE_GLOBE   = ce("🌐")
_CE_BUBBLE  = ce("💬")
_CE_LANG    = ce("🌐")
_CE_BROKEN  = ce("💔")
_CE_ROCKET  = ce("🚀")
_CE_PARTY   = ce("🎉")
_CE_CHECK2  = ce("✅")
_CE_LINK    = ce("🔗")
_CE_SEARCH  = ce("🔍")
_CE_RECEIPT = ce("🧾")


def strip_custom_emoji(text: str) -> str:
    """Убирает <tg-emoji ...>X</tg-emoji>, оставляя сам эмодзи X (на случай ошибки Telegram)."""
    if not text:
        return text
    return re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', text, flags=re.DOTALL)


# Широкий диапазон ведущих эмодзи — чтобы убирать дублирующий юникод-эмодзи из текста кнопок,
# когда на кнопку ставится premium-иконка (icon_custom_emoji_id).
_LEADING_EMOJI_RE = re.compile(
    "^(?:[\U0001F000-\U0001FAFF"      # эмодзи supplement planes
    "\U00002600-\U000027BF"            # Misc symbols + Dingbats (☀..➿)
    "\U0001F1E6-\U0001F1FF"            # региональные индикаторы (флаги)
    "\U00002190-\U000021FF"            # стрелки (←↔↩)
    "\U00002B00-\U00002BFF"            # доп. стрелки/символы (⬅⭐)
    "\U00002300-\U000023FF"            # техн. символы (⏰⌚)
    "\U00002460-\U000024FF"            # enclosed alphanumerics (™ⓘ окрестность)
    "\U00002122\U00002139"            # ™ ℹ
    "\U0000FE00-\U0000FE0F"            # variation selectors
    "\U0000200D"                       # zero-width joiner
    "\U000020E3"                       # combining keycap
    "\U00002934\U00002935"
    "\U000025AA-\U000025FE"
    "\U0000203C\U00002049\U0000203B"
    "]+\\s*)+"
)


def _strip_leading_emoji(s: str) -> str:
    if not s:
        return s
    cleaned = _LEADING_EMOJI_RE.sub("", s).strip()
    return cleaned if cleaned else s


def apply_button_icons(markup):
    """Ставит premium-эмодзи (свой ID по BUTTON_EMOJI_MAP) + цвет (BUTTON_STYLE) на
    ВСЕ инлайн-кнопки и убирает дублирующий обычный эмодзи из текста. Bot API 9.4+."""
    if markup is None:
        return markup
    rows = getattr(markup, "inline_keyboard", None)
    if not rows:
        return markup
    for row in rows:
        for btn in row:
            try:
                if getattr(btn, "pay", None):
                    continue  # платёжные кнопки трогать нельзя
                # Цвет кнопки (success = зелёный)
                if BUTTON_STYLE and not getattr(btn, "style", None):
                    btn.style = BUTTON_STYLE
                # Premium-эмодзи (copy-кнопки оставляем без иконки).
                # Иконку ставим ТОЛЬКО если для кнопки задан непустой ID — иначе
                # оставляем обычный эмодзи прямо в тексте кнопки.
                if not getattr(btn, "copy_text", None) and USE_CUSTOM_EMOJI \
                        and not getattr(btn, "icon_custom_emoji_id", None):
                    _eid = button_emoji_id_for(getattr(btn, "callback_data", None))
                    if _eid:
                        btn.icon_custom_emoji_id = _eid
                        btn.text = _strip_leading_emoji(btn.text)
            except Exception:
                pass
    return markup


def strip_button_icons(markup):
    """Снимает premium-иконки и стиль со всех кнопок — fallback, если Telegram отклонит."""
    if markup is None:
        return markup
    rows = getattr(markup, "inline_keyboard", None)
    if not rows:
        return markup
    for row in rows:
        for btn in row:
            try:
                if getattr(btn, "icon_custom_emoji_id", None):
                    btn.icon_custom_emoji_id = None
                if getattr(btn, "style", None):
                    btn.style = None
            except Exception:
                pass
    return markup


# Любой одиночный эмодзи в произвольном месте строки (для авто-премиумизации текста).
_EMOJI_CHAR_RE = re.compile(
    "(?:[\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF"
    "\U00002300-\U000023FF"
    "\U00002460-\U000024FF"
    "\U00002122\U00002139"
    "\U00002934\U00002935"
    "\U000025AA-\U000025FE"
    "\U0000203C\U00002049\U0000203B]"
    "[\U0000FE00-\U0000FE0F\U000020E3]*)"
)


def premiumize_text(text: str) -> str:
    """Оборачивает КАЖДЫЙ обычный эмодзи в тексте в premium <tg-emoji>, беря СВОЙ ID
    для каждого символа из EMOJI_MAP. Уже обёрнутые <tg-emoji> не трогаются. Если
    Telegram отклонит ID — escalating fallback в send_media_message вернёт обычные."""
    if not USE_CUSTOM_EMOJI or not text:
        return text
    parts = re.split(r'(<tg-emoji.*?</tg-emoji>)', text, flags=re.DOTALL)
    out = []
    for p in parts:
        if p.startswith('<tg-emoji'):
            out.append(p)
        else:
            out.append(_EMOJI_CHAR_RE.sub(_wrap_one_emoji, p))
    return ''.join(out)


def _wrap_one_emoji(mo):
    """Оборачивает один символ в premium <tg-emoji> ТОЛЬКО если для него задан
    непустой ID в EMOJI_MAP. Иначе возвращает обычный эмодзи как есть."""
    ch = mo.group(0)
    eid = emoji_id_for(ch)
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{ch}</tg-emoji>'
    return ch


# =======================
# ВАЛИДАЦИЯ РЕКВИЗИТОВ
# =======================
def validate_card(text: str):
    """Карта: только цифры, 12–19 цифр. Возвращает (ok, нормализованное_значение)."""
    digits = re.sub(r"[\s\-]", "", text or "")
    return (digits.isdigit() and 12 <= len(digits) <= 19), digits


def validate_ton(text: str):
    """TON-адрес: начинается на UQ/EQ, 48 символов."""
    v = (text or "").strip()
    ok = v[:2].lower() in ("uq", "eq") and len(v) == 48
    return ok, v


def validate_usdt(text: str):
    """USDT TRC20: начинается с T, 34 символа."""
    v = (text or "").strip()
    ok = v.startswith("T") and len(v) == 34 and v[1:].isalnum()
    return ok, v


def validate_btc(text: str):
    """BTC: начинается с 1, 3 или bc1, длина 26–62."""
    v = (text or "").strip()
    ok = (v.startswith("1") or v.startswith("3") or v.lower().startswith("bc1")) and 26 <= len(v) <= 62
    return ok, v


# =======================
# TEXTS / TRANSLATIONS
# =======================
TEXTS = {
    'start_button': {'RU': "Запустить бота", 'EN': "Start bot"},
    'instruction_line': {
        'RU': "📖 Как пользоваться? Ознакомьтесь с инструкцией — https://telegra.ph/Rukovodstvo-Giftich---OTC-10-10",
        'EN': "📖 How to use — read the guide: https://telegra.ph/Rukovodstvo-Giftich---OTC-10-10"
    },
    'welcome_caption': {
        'RU': (
            f"<b>{_CE_CROWN} Добро пожаловать в Lolz Market {_CE_CROWN}</b>\n\n"
            f"<blockquote><i>{_CE_BOLT} Ваш надёжный P2P-гарант: </i>\n"
            f"{_CE_BOT} Автоматические сделки с NFT и валютами\n"
            f"{_CE_SHIELD} Полная защита обеих сторон\n"
            f"{_CE_MONEY} Реферальная программа — 50% от комиссии\n"
            f"{_CE_BOX} Передача товаров через менеджера: @FunPay_Sapports</blockquote>\n\n"
        ),
        'EN': (
            f"<b>{_CE_CROWN} Welcome to Lolz {_CE_CROWN}</b>\n\n"
            f"<blockquote><i>{_CE_BOLT} Your reliable P2P escrow: </i>\n"
            f"{_CE_BOT} Automatic deals with NFT and gifts\n"
            f"{_CE_SHIELD} Full protection of both parties\n"
            f"{_CE_MONEY} Referral program — 50% of commission\n"
            f"{_CE_BOX} Item transfer via manager: @FunPay_Sapports</blockquote>\n\n"
        )
    },
    'btn_manage': {'RU': "📋 Мои реквизиты", 'EN': "📋 My Requisites"},
    'btn_create': {'RU': "➕ Создать сделку", 'EN': "➕ Create Deal"},
    'btn_referral': {'RU': "👥 Рефералы", 'EN': "👥 Referrals"},
    'btn_change_lang': {'RU': "🌐 Язык / Lang", 'EN': "🌐 Language / Lang"},
    'btn_support': {'RU': "💬 Техподдержка", 'EN': "💬 Support"},
    'btn_balance': {'RU': "💵 Баланс", 'EN': "💵 Balance"},
    'btn_my_deals': {'RU': "📊 Мои сделки", 'EN': "📊 My Deals"},
    'balance_text': {'RU': "Ваш баланс: {balance}", 'EN': "Your balance: {balance}"},
    'manage_details_text': {
        'RU': (
            f"{_CE_PIN} <b>Мои реквизиты</b>\n\n"
            f"<blockquote>{_CE_DIAMOND} TON-кошелёк: {{wallet}}\n"
            f"{_CE_CARD} Карта: {{card}}\n"
            f"{_CE_STAR} Stars: {{stars}}\n"
            f"{_CE_COIN} USDT (TRC20): {{usdt}}\n"
            f"{_CE_COIN} BTC: {{btc}}</blockquote>"
        ),
        'EN': (
            f"{_CE_PIN} <b>My Requisites</b>\n\n"
            f"<blockquote>{_CE_DIAMOND} TON wallet: {{wallet}}\n"
            f"{_CE_CARD} Card: {{card}}\n"
            f"{_CE_STAR} Stars: {{stars}}\n"
            f"{_CE_COIN} USDT (TRC20): {{usdt}}\n"
            f"{_CE_COIN} BTC: {{btc}}</blockquote>"
        )
    },
    'btn_change_ton': {'RU': "💎 TON-кошелёк", 'EN': "💎 TON wallet"},
    'btn_change_card': {'RU': "💳 Карта", 'EN': "💳 Card"},
    'btn_receive_stars': {'RU': "⭐ Stars", 'EN': "⭐ Stars"},
    'btn_change_usdt': {'RU': "🪙 USDT-кошелёк", 'EN': "🪙 USDT wallet"},
    'btn_change_btc': {'RU': "🪙 BTC-кошелёк", 'EN': "🪙 BTC wallet"},
    'btn_back': {'RU': "◀️ Назад в меню", 'EN': "◀️ Back to menu"},
    'prompt_ton_set':  {'RU': f"{_CE_DIAMOND} <b>Введите TON-кошелёк:</b>", 'EN': f"{_CE_DIAMOND} <b>Enter TON wallet:</b>"},
    'prompt_ton_new':  {'RU': f"{_CE_DIAMOND} <b>Введите новый TON-кошелёк:</b>", 'EN': f"{_CE_DIAMOND} <b>Enter new TON wallet:</b>"},
    'prompt_card_set': {'RU': f"{_CE_CARD} <b>Введите номер карты:</b>", 'EN': f"{_CE_CARD} <b>Enter card number:</b>"},
    'prompt_card_new': {'RU': f"{_CE_CARD} <b>Введите новый номер карты:</b>", 'EN': f"{_CE_CARD} <b>Enter new card number:</b>"},
    'prompt_usdt_set': {'RU': f"{_CE_COIN} <b>Введите USDT-адрес (TRC20):</b>", 'EN': f"{_CE_COIN} <b>Enter USDT address (TRC20):</b>"},
    'prompt_usdt_new': {'RU': f"{_CE_COIN} <b>Введите новый USDT-адрес (TRC20):</b>", 'EN': f"{_CE_COIN} <b>Enter new USDT address (TRC20):</b>"},
    'prompt_btc_set':  {'RU': f"{_CE_COIN} <b>Введите BTC-адрес:</b>", 'EN': f"{_CE_COIN} <b>Enter BTC address:</b>"},
    'prompt_btc_new':  {'RU': f"{_CE_COIN} <b>Введите новый BTC-адрес:</b>", 'EN': f"{_CE_COIN} <b>Enter new BTC address:</b>"},
    'prompt_stars_set': {
        'RU': (f"{_CE_STAR} <b>Введите @username для Stars (без @):</b>\n\n"
               f"<blockquote>Stars будут зачислены на указанный аккаунт после завершения сделки.</blockquote>"),
        'EN': (f"{_CE_STAR} <b>Enter @username for Stars (without @):</b>\n\n"
               f"<blockquote>Stars will be credited to the specified account after the deal.</blockquote>")
    },
    'prompt_stars_new': {
        'RU': (f"{_CE_STAR} <b>Введите новый @username для Stars (без @):</b>\n\n"
               f"<blockquote>Stars будут зачислены на указанный аккаунт после завершения сделки.</blockquote>"),
        'EN': (f"{_CE_STAR} <b>Enter new @username for Stars (without @):</b>\n\n"
               f"<blockquote>Stars will be credited to the specified account after the deal.</blockquote>")
    },
    'ton_saved': {'RU': f"<blockquote>{_CE_CHECK} <b>TON-кошелёк сохранён.</b></blockquote>",
                  'EN': f"<blockquote>{_CE_CHECK} <b>TON wallet saved.</b></blockquote>"},
    'card_saved': {'RU': f"<blockquote>{_CE_CHECK} <b>Банковская карта сохранена.</b></blockquote>",
                   'EN': f"<blockquote>{_CE_CHECK} <b>Bank card saved.</b></blockquote>"},
    'stars_saved': {'RU': f"<blockquote>{_CE_CHECK} <b>Получатель Stars сохранён.</b></blockquote>",
                    'EN': f"<blockquote>{_CE_CHECK} <b>Stars recipient saved.</b></blockquote>"},
    'usdt_saved': {'RU': f"<blockquote>{_CE_CHECK} <b>USDT-кошелёк сохранён.</b></blockquote>",
                   'EN': f"<blockquote>{_CE_CHECK} <b>USDT wallet saved.</b></blockquote>"},
    'btc_saved': {'RU': f"<blockquote>{_CE_CHECK} <b>BTC-адрес сохранён.</b></blockquote>",
                  'EN': f"<blockquote>{_CE_CHECK} <b>BTC address saved.</b></blockquote>"},
    'invalid_card': {
        'RU': (f"{_CE_CROSS} <b>Неверный номер карты.</b>\n\n"
               f"<blockquote>Введите только цифры карты — от 12 до 19 цифр, без букв.</blockquote>"),
        'EN': (f"{_CE_CROSS} <b>Invalid card number.</b>\n\n"
               f"<blockquote>Enter only digits — 12 to 19 digits, no letters.</blockquote>")
    },
    'invalid_ton': {
        'RU': (f"{_CE_CROSS} <b>Неверный TON-кошелёк.</b>\n\n"
               f"<blockquote>Адрес должен начинаться с UQ или EQ и содержать 48 символов.</blockquote>"),
        'EN': (f"{_CE_CROSS} <b>Invalid TON wallet.</b>\n\n"
               f"<blockquote>Address must start with UQ or EQ and be 48 characters long.</blockquote>")
    },
    'invalid_usdt': {
        'RU': (f"{_CE_CROSS} <b>Неверный USDT (TRC20) адрес.</b>\n\n"
               f"<blockquote>Адрес TRC20 начинается с буквы T и содержит 34 символа.</blockquote>"),
        'EN': (f"{_CE_CROSS} <b>Invalid USDT (TRC20) address.</b>\n\n"
               f"<blockquote>A TRC20 address starts with T and is 34 characters long.</blockquote>")
    },
    'invalid_btc': {
        'RU': (f"{_CE_CROSS} <b>Неверный BTC-адрес.</b>\n\n"
               f"<blockquote>Адрес должен начинаться с 1, 3 или bc1 и содержать 26–62 символа.</blockquote>"),
        'EN': (f"{_CE_CROSS} <b>Invalid BTC address.</b>\n\n"
               f"<blockquote>Address must start with 1, 3 or bc1 and be 26–62 characters long.</blockquote>")
    },
    'ref_invalid': {'RU': "Неверная реферальная ссылка.", 'EN': "Invalid referral link."},
    'ref_welcome': {'RU': "Добро пожаловать! Вы были приглашены по реферальной ссылке.",
                    'EN': "Welcome! You were invited via a referral."},
    'ref_notify_referrer': {'RU': "👥 У вас новый реферал: @{username}", 'EN': "👥 You have a new referral: @{username}"},
    'ref_earned_notify': {
        'RU': "💰 Ваш реферал завершил сделку! Вам начислено: <b>{amount} TON</b>",
        'EN': "💰 Your referral completed a deal! You earned: <b>{amount} TON</b>"
    },
    'your_ref_link': {'RU': "Ваша реферальная ссылка:\n{link}", 'EN': "Your referral link:\n{link}"},
    'enter_gift_link': {
        'RU': ("<b>💼 Создание сделки</b>\n\n"
               "<b>Укажите ссылку на подарок который продаете:</b>\n"
               "<blockquote><b>пример: https://t.me/nft/...</b></blockquote>"),
        'EN': ("<b>💼 Создание сделки</b>\n\n"
               "<b>Укажите ссылку на подарок который продаете:</b>\n"
               "<blockquote><b>пример: https://t.me/nft/...</b></blockquote>")
    },
    'enter_description': {
        'RU': ("<b>💼 Создание сделки</b>\n\n"
               "<blockquote><b>Укажите что вы предлагаете в сделке. Например: 10 Персиков и Кепка. 👇</b></blockquote>"),
        'EN': ("<b>💼 Создание сделки</b>\n\n"
               "<blockquote><b>Укажите что вы предлагаете в сделке. Например: 10 Персиков и Кепка. 👇</b></blockquote>")
    },
    'enter_amount': {
        'RU': ("<b>💼 Создание сделки</b>\n\n"
               "<blockquote><b>Введите сумму сделки в формате: 100.5</b></blockquote>"),
        'EN': ("<b>💼 Создание сделки</b>\n\n"
               "<blockquote><b>Введите сумму сделки в формате: 100.5</b></blockquote>")
    },
    'invalid_amount': {'RU': "Неправильный формат суммы. Введите число:",
                       'EN': "Invalid amount format. Enter a number:"},
    'choose_currency': {
        'RU': ("<b>💼 Создание сделки</b>\n\n"
               "<blockquote><b>Выберите валюту сделки.</b></blockquote>"),
        'EN': ("<b>💼 Создание сделки</b>\n\n"
               "<blockquote><b>Выберите валюту сделки.</b></blockquote>")
    },
    'deal_created': {
        'RU': (f"{_CE_PARTY} <b>Сделка #{{code}} успешно создана!</b>\n\n"
               f"<blockquote>{_CE_PEOPLE} Роль: <b>{{role}}</b>\n"
               f"{_CE_DOLLAR} Валюта: <b>{{currency}}</b>\n"
               f"{_CE_MONEY} Сумма: <b>{{amount}}</b>\n"
               f"{_CE_PIN} Описание: {{desc}}</blockquote>\n\n"
               f"{_CE_LINK} <b>Ссылка для {{target}}:</b>\n{{link}}\n\n"
               f"{_CE_BOLT} <i>Или пригласите через инлайн: введите "
               f"<code>@{{botname}} #{{code}}</code> в любом чате</i>"),
        'EN': (f"{_CE_PARTY} <b>Deal #{{code}} successfully created!</b>\n\n"
               f"<blockquote>{_CE_PEOPLE} Role: <b>{{role}}</b>\n"
               f"{_CE_DOLLAR} Currency: <b>{{currency}}</b>\n"
               f"{_CE_MONEY} Amount: <b>{{amount}}</b>\n"
               f"{_CE_PIN} Description: {{desc}}</blockquote>\n\n"
               f"{_CE_LINK} <b>Link for {{target}}:</b>\n{{link}}\n\n"
               f"{_CE_BOLT} <i>Or invite via inline: type "
               f"<code>@{{botname}} #{{code}}</code> in any chat</i>")
    },
    'role_seller_word': {'RU': "Продавец", 'EN': "Seller"},
    'role_buyer_word': {'RU': "Покупатель", 'EN': "Buyer"},
    'target_buyer': {'RU': "покупателя", 'EN': "the buyer"},
    'target_seller': {'RU': "продавца", 'EN': "the seller"},
    'admin_new_deal_notify': {
        'RU': "Новая сделка #{code}\nСоздатель: {creator} ({role})\nОписание: {desc}\nСумма: {amount} {currency}\nСсылка на подарок: {link}",
        'EN': "New deal #{code}\nCreator: {creator} ({role})\nDescription: {desc}\nAmount: {amount} {currency}\nGift link: {link}"
    },
    'deal_join_own_forbidden': {'RU': "❌ Вы не можете зайти в свою же сделку.",
                                'EN': "You cannot join your own deal."},
    'deal_taken': {'RU': "Эта сделка уже занята.", 'EN': "This deal is already taken."},
    'buyer_joined_notify_seller': {
        'RU': ("<b>Пользователь {buyer} присоединился к вашей сделке <code>{code}</code>\n"
               "• Количество сделок покупателя: <code>{buyer_success}</code></b>\n\n"
               "<blockquote><b>‼️ Убедитесь, что это тот же пользователь, с которым вы вели диалог ранее!\n\n"
               "Передавайте товар только поддержке бота - @FunPay_Sapports, в ином случае товар будет утерян.</b></blockquote>"),
        'EN': ("<b>Пользователь {buyer} присоединился к вашей сделке <code>{code}</code>\n"
               "• Количество сделок покупателя: <code>{buyer_success}</code></b>\n\n"
               "<blockquote><b>‼️ Убедитесь, что это тот же пользователь, с которым вы вели диалог ранее!\n\n"
               "Не передавайте товар/ы покупателю до получения сообщения от бота о подтверждения оплаты!</b></blockquote>")
    },
    'admin_buyer_joined': {
        'RU': "Покупатель подключился к сделке #{code}\nПродавец: {seller}\nПокупатель: {buyer}",
        'EN': "Buyer joined deal #{code}\nSeller: {seller}\nBuyer: {buyer}"},
    # Сообщение продавцу, который подключился к сделке, созданной покупателем.
    'seller_joined_notify': {
        'RU': ("<b>✅ Вы подключились к сделке <code>{code}</code> как продавец.\n"
               "• Покупатель: {buyer}\n"
               "• Количество сделок покупателя: <code>{buyer_success}</code></b>\n\n"
               "<blockquote><b>‼️ Убедитесь, что это тот же пользователь, с которым вы вели диалог ранее!\n\n"
               "Передавайте товар только поддержке бота — @FunPay_Sapports, иначе товар будет утерян. "
               "Дождитесь сообщения от бота о подтверждении оплаты.</b></blockquote>"),
        'EN': ("<b>✅ You joined deal <code>{code}</code> as the seller.\n"
               "• Buyer: {buyer}\n"
               "• Buyer's deals: <code>{buyer_success}</code></b>\n\n"
               "<blockquote><b>‼️ Make sure this is the same user you talked to earlier!\n\n"
               "Only hand the item to the bot's support — @FunPay_Sapports, otherwise it will be lost. "
               "Wait for the bot's payment confirmation.</b></blockquote>")
    },
    # Запрос реквизита у подключающегося продавца (сделку создал покупатель).
    'join_requisite_prompt': {
        'RU': (f"{_CE_MONEY} <b>Куда получить оплату?</b>\n\n"
               f"<blockquote>Сделку создал покупатель. Валюта сделки — <b>{{currency}}</b>.\n"
               f"Отправьте реквизит для получения ({{req_name}}), чтобы продолжить.</blockquote>"),
        'EN': (f"{_CE_MONEY} <b>Where to receive payment?</b>\n\n"
               f"<blockquote>This deal was created by the buyer. Currency — <b>{{currency}}</b>.\n"
               f"Send your payout requisite ({{req_name}}) to continue.</blockquote>")
    },
    'join_requisite_confirm': {
        'RU': (f"{_CE_MONEY} <b>Подтвердите реквизит для получения</b>\n\n"
               f"<blockquote>Валюта сделки — <b>{{currency}}</b>.\n"
               f"Ваш сохранённый реквизит ({{req_name}}):\n<code>{{value}}</code></blockquote>\n\n"
               f"Использовать его для этой сделки?"),
        'EN': (f"{_CE_MONEY} <b>Confirm your payout requisite</b>\n\n"
               f"<blockquote>Currency — <b>{{currency}}</b>.\n"
               f"Your saved requisite ({{req_name}}):\n<code>{{value}}</code></blockquote>\n\n"
               f"Use it for this deal?")
    },
    'join_requisite_saved': {
        'RU': f"{_CE_CHECK} <b>Реквизит сохранён. Ожидайте оплату от покупателя.</b>",
        'EN': f"{_CE_CHECK} <b>Requisite saved. Wait for the buyer's payment.</b>"
    },
    'join_requisite_invalid': {
        'RU': f"{_CE_CROSS} Некорректный реквизит. Отправьте ещё раз:",
        'EN': f"{_CE_CROSS} Invalid requisite. Please send it again:"
    },
    'btn_joinreq_use':  {'RU': "✅ Использовать", 'EN': "✅ Use it"},
    'btn_joinreq_edit': {'RU': "✏️ Ввести другой", 'EN': "✏️ Enter another"},
    'req_name_ton':   {'RU': "TON-кошелёк", 'EN': "TON wallet"},
    'req_name_usdt':  {'RU': "USDT (TRC20) кошелёк", 'EN': "USDT (TRC20) wallet"},
    'req_name_btc':   {'RU': "BTC-адрес", 'EN': "BTC address"},
    'req_name_card':  {'RU': "номер карты", 'EN': "card number"},
    'req_name_stars': {'RU': "@username для Stars", 'EN': "Stars @username"},
    # buyer_info will be filled and sent both when using invoice and when fallback/manual
    'buyer_info': {
        'RU': ("<b>💼 Сделка <code>{code}</code>\n"
               "Тип сделки: 🎁 Подарки\n\n"
               "<i>👤 Вы покупатель, у вас есть 15 минут на оплату сделки!</i>\n"
               "™️ Продавец: {seller}\n\n"
               "• Количество сделок продавца: <code>{seller_success}</code>\n\n"
               "• Вы покупаете: <code>{desc}</code>\n"
               "• Вы отдаете: <code>{amount} {currency}</code>\n\n"
               "👛 Кошелёк для оплаты: <code>{pay_address}</code>\n\n"
               "💸 Сумма к оплате:\n"
               "💎 <code>{amount} {currency}</code>\n\n"
               "🚨 Комментарий к транзакции: <code>{code}</code> 🚨</b>\n\n"
               "<blockquote><b>‼️ Пожалуйста, убедитесь что при оплате указываете обязательный комментарий (memo) и точную сумму!</b></blockquote>\n\n"
               "<b>Оплата подтвердится автоматически</b>"),
        'EN': ("<b>💼 Сделка <code>{code}</code>\n"
               "Тип сделки: 🎁 Подарки\n\n"
               "<i>👤 Вы покупатель, у вас есть 15 минут на оплату сделки!</i>\n"
               "™️ Продавец: {seller}\n\n"
               "• Количество сделок продавца: <code>{seller_success}</code>\n\n"
               "• Вы покупаете: <code>{desc}</code>\n"
               "• Вы отдаете: <code>{amount} {currency}</code>\n\n"
               "👛 Кошелёк для оплаты: <code>{pay_address}</code>\n\n"
               "💸 Сумма к оплате:\n"
               "💎 <code>{amount} {currency}</code>\n\n"
               "🚨 Комментарий к транзакции: <code>{code}</code> 🚨</b>\n\n"
               "<blockquote><b>‼️ Пожалуйста, убедитесь что при оплате указываете обязательный комментарий (memo) и точную сумму!</b></blockquote>\n\n"
               "<b>Оплата подтвердится автоматически</b>")
    },
    'no_details': {'RU': "Доступных реквизитов сейчас нет. Обратитесь в поддержку: @FunPay_Sapports",
                   'EN': "No available details now. Contact support: @FunPay_Sapports"},
    'btn_confirm_payment': {'RU': "✅ Подтвердить оплату", 'EN': "✅ Confirm payment"},
    'btn_cancel_deal': {'RU': "❌ Отменить сделку", 'EN': "❌ Cancel deal"},
    'enter_credentials': {'RU': "Введите ваши реквизиты или укажите адрес TON, либо @username для получения Stars.",
                          'EN': "Enter your details or provide a Stars @username before creating a deal."},
    'stars_recipient_line': {'RU': "⭐ Получатель Stars: {recipient}", 'EN': "⭐ Stars recipient: {recipient}"},
    'ack_warning': {
        'RU': ("<b>⚠️ ОБЯЗАТЕЛЬНО К ПРОЧТЕНИЮ!</b>\n\n"
               "<b>Проверка получение подарка происходит автоматически — только если вы отправляете подарок на аккаунт @FunPay_Sapports</b>\n\n"
               "<blockquote><b>Если же вы отправите подарок напрямую покупателю, то проверка НЕ СРАБОТАЕТ, и:\n"
               "• Подарок будет потерян\n"
               "• Вывести средства станет невозможно\n"
               "• Сделка будет считаться несостоявшейся и вы потеряете свой подарок и деньги</b></blockquote>\n\n"
               "<b>👉 Чтобы успешно завершить сделку и получить средства — всегда отправляйте подарок только на аккаунт @FunPay_Sapports</b>"),
        'EN': ("<b>⚠️ ОБЯЗАТЕЛЬНО К ПРОЧТЕНИЮ!</b>\n\n"
               "<b>Проверка получение подарка происходит автоматически — только если вы отправляете подарок на аккаунт @FunPay_Sapports</b>\n\n"
               "<blockquote><b>Если же вы отправите подарок напрямую покупателю, то проверка НЕ СРАБОТАЕТ, и:\n"
               "• Подарок будет потерян\n"
               "• Вывести средства станет невозможно\n"
               "• Сделка будет считаться несостоявшейся и вы потеряете свой подарок и деньги</b></blockquote>\n\n"
               "<b>👉 Чтобы успешно завершить сделку и получить средства — всегда отправляйте подарок только на аккаунт @FunPay_Sapports</b>")
    },
    'deal_role_select': {
        'RU': (
            f"{_CE_RED} <b>Новая сделка</b>\n\n"
            f"<blockquote>{_CE_SPEECH} Кем вы выступаете в этой сделке?</blockquote>\n\n"
            f"{_CE_FIRE} <b>Продавец</b> — вы продаёте товар/услугу и получаете оплату.\n"
            f"{_CE_CART} <b>Покупатель</b> — вы платите и получаете товар/услугу."
        ),
        'EN': (
            f"{_CE_RED} <b>New Deal</b>\n\n"
            f"<blockquote>{_CE_SPEECH} What is your role in this deal?</blockquote>\n\n"
            f"{_CE_FIRE} <b>Seller</b> — you sell an item/service and receive payment.\n"
            f"{_CE_CART} <b>Buyer</b> — you pay and receive the item/service."
        )
    },
    'buyer_enter_code': {
        'RU': (
            f"{_CE_CART} <b>Вы — покупатель</b>\n\n"
            f"<blockquote>{_CE_SPEECH} Введите код сделки или вставьте ссылку, "
            f"которую получили от продавца:</blockquote>"
        ),
        'EN': (
            f"{_CE_CART} <b>You are the buyer</b>\n\n"
            f"<blockquote>{_CE_SPEECH} Enter the deal code or paste the link "
            f"you received from the seller:</blockquote>"
        )
    },
    'btn_role_seller': {'RU': "🔥 Я продавец", 'EN': "🔥 I'm the seller"},
    'btn_role_buyer':  {'RU': "🛒 Я покупатель", 'EN': "🛒 I'm the buyer"},
    # --- Новый флоу: способ -> валюта -> сумма ---
    'method_select_seller': {
        'RU': (f"{_CE_MONEY} <b>Способ получения оплаты</b>\n\n"
               f"<blockquote>{_CE_SPEECH} Как покупатель переведёт средства?</blockquote>"),
        'EN': (f"{_CE_MONEY} <b>Payout method</b>\n\n"
               f"<blockquote>{_CE_SPEECH} How will the buyer transfer funds?</blockquote>")
    },
    'method_select_buyer': {
        'RU': (f"{_CE_MONEY} <b>Способ оплаты</b>\n\n"
               f"<blockquote>{_CE_SPEECH} Каким способом вы хотите оплатить?</blockquote>"),
        'EN': (f"{_CE_MONEY} <b>Payment method</b>\n\n"
               f"<blockquote>{_CE_SPEECH} How would you like to pay?</blockquote>")
    },
    'choose_card_currency': {
        'RU': f"{_CE_CARD} <b>Выберите валюту карты:</b>",
        'EN': f"{_CE_CARD} <b>Choose card currency:</b>"
    },
    'enter_stars_amount': {
        'RU': f"{_CE_BOLT} <b>Укажите количество Stars:</b>",
        'EN': f"{_CE_BOLT} <b>Enter the amount of Stars:</b>"
    },
    'choose_crypto': {
        'RU': (f"{_CE_DIAMOND} <b>Выберите криптовалюту:</b>\n\n"
               f"<blockquote>TON, USDT (TRC20) или BTC</blockquote>"),
        'EN': (f"{_CE_DIAMOND} <b>Choose cryptocurrency:</b>\n\n"
               f"<blockquote>TON, USDT (TRC20) or BTC</blockquote>")
    },
    'enter_amount_in': {
        'RU': f"{_CE_MONEY} <b>Введите сумму в {{currency}}:</b>",
        'EN': f"{_CE_MONEY} <b>Enter the amount in {{currency}}:</b>"
    },
    'enter_item_description': {
        'RU': (f"{_CE_BOX} <b>Опишите предмет сделки:</b>\n\n"
               f"<blockquote><i>Например: https://t.me/nft/PlushPepe-111 "
               f"или просто текстовое описание товара</i></blockquote>"),
        'EN': (f"{_CE_BOX} <b>Describe the deal item:</b>\n\n"
               f"<blockquote><i>For example: https://t.me/nft/PlushPepe-111 "
               f"or just a text description</i></blockquote>")
    },
    'need_requisite_card': {
        'RU': (f"{_CE_WARN} <b>Сначала добавьте карту</b>\n\n"
               f"<blockquote>Чтобы выбрать этот способ, укажите карту в реквизитах ниже.</blockquote>"),
        'EN': (f"{_CE_WARN} <b>Add a card first</b>\n\n"
               f"<blockquote>Add your card in the requisites below to use this method.</blockquote>")
    },
    'need_requisite_stars': {
        'RU': (f"{_CE_WARN} <b>Сначала укажите @username для Stars</b>\n\n"
               f"<blockquote>Чтобы принимать Stars, укажите username в реквизитах ниже.</blockquote>"),
        'EN': (f"{_CE_WARN} <b>Set a Stars @username first</b>\n\n"
               f"<blockquote>Add your Stars username in the requisites below.</blockquote>")
    },
    'need_requisite_ton': {
        'RU': (f"{_CE_WARN} <b>Сначала добавьте TON-кошелёк</b>\n\n"
               f"<blockquote>Укажите TON-кошелёк в реквизитах ниже.</blockquote>"),
        'EN': (f"{_CE_WARN} <b>Add a TON wallet first</b>\n\n"
               f"<blockquote>Add your TON wallet in the requisites below.</blockquote>")
    },
    'need_requisite_usdt': {
        'RU': (f"{_CE_WARN} <b>Сначала добавьте USDT-кошелёк</b>\n\n"
               f"<blockquote>Укажите USDT (TRC20) в реквизитах ниже.</blockquote>"),
        'EN': (f"{_CE_WARN} <b>Add a USDT wallet first</b>\n\n"
               f"<blockquote>Add your USDT (TRC20) in the requisites below.</blockquote>")
    },
    'need_requisite_btc': {
        'RU': (f"{_CE_WARN} <b>Сначала добавьте BTC-адрес</b>\n\n"
               f"<blockquote>Укажите BTC-адрес в реквизитах ниже.</blockquote>"),
        'EN': (f"{_CE_WARN} <b>Add a BTC address first</b>\n\n"
               f"<blockquote>Add your BTC address in the requisites below.</blockquote>")
    },
    'btn_method_card':  {'RU': "💳 Карта", 'EN': "💳 Card"},
    'btn_method_stars': {'RU': "⭐ Stars", 'EN': "⭐ Stars"},
    'btn_method_crypto': {'RU': "🪙 Крипта", 'EN': "🪙 Crypto"},
    'btn_change_currency': {'RU': "🪙 Изменить валюту", 'EN': "🪙 Change currency"},
    'btn_back_step': {'RU': "◀️ Назад", 'EN': "◀️ Back"},
    'btn_cur_rub':  {'RU': "🇷🇺 RUB", 'EN': "🇷🇺 RUB"},
    'btn_cur_uah':  {'RU': "🇺🇦 UAH", 'EN': "🇺🇦 UAH"},
    'btn_cur_kzt':  {'RU': "🇰🇿 KZT", 'EN': "🇰🇿 KZT"},
    'btn_cur_byn':  {'RU': "🇧🇾 BYN", 'EN': "🇧🇾 BYN"},
    'btn_cur_ton':  {'RU': "💎 TON", 'EN': "💎 TON"},
    'btn_cur_usdt': {'RU': "🪙 USDT", 'EN': "🪙 USDT"},
    'btn_cur_btc':  {'RU': "🪙 BTC", 'EN': "🪙 BTC"},
    # --- Баланс ---
    'balance_screen': {
        'RU': (f"{_CE_DOLLAR} <b>Ваш баланс:</b>\n\n"
               f"{{balance_line}}\n"
               f"{_CE_CHART} Завершённых сделок: <b>{{count}}</b>\n\n"
               f"{_CE_ROCKET} <i>Для вывода средств необходимо минимум 2 завершённых сделок</i>"),
        'EN': (f"{_CE_DOLLAR} <b>Your balance:</b>\n\n"
               f"{{balance_line}}\n"
               f"{_CE_CHART} Completed deals: <b>{{count}}</b>\n\n"
               f"{_CE_ROCKET} <i>At least 2 completed deals are required to withdraw</i>")
    },
    'balance_empty_line': {
        'RU': f"{_CE_BROKEN} <i>Ваш баланс пока пуст</i>",
        'EN': f"{_CE_BROKEN} <i>Your balance is empty</i>"
    },
    'balance_amount_line': {
        'RU': f"{_CE_MONEY} Баланс: <b>{{amount}} {{currency}}</b>",
        'EN': f"{_CE_MONEY} Balance: <b>{{amount}} {{currency}}</b>"
    },
    'btn_withdraw': {'RU': "💰 Вывод средств", 'EN': "💰 Withdraw"},
    'btn_transactions': {'RU': "🎉 Транзакции", 'EN': "🎉 Transactions"},
    'withdraw_not_enough': {
        'RU': (f"{_CE_WARN} <b>Недостаточно завершённых сделок</b>\n\n"
               f"<blockquote>Для вывода средств необходимо минимум 2 завершённых сделок.</blockquote>"),
        'EN': (f"{_CE_WARN} <b>Not enough completed deals</b>\n\n"
               f"<blockquote>At least 2 completed deals are required to withdraw.</blockquote>")
    },
    # --- Транзакции (история выводов) ---
    'transactions_screen': {
        'RU': (f"{_CE_RECEIPT} <b>Транзакции</b>\n\n"
               f"<blockquote>{_CE_BROKEN} <i>История выводов пока пуста</i></blockquote>"),
        'EN': (f"{_CE_RECEIPT} <b>Transactions</b>\n\n"
               f"<blockquote>{_CE_BROKEN} <i>No withdrawals yet</i></blockquote>")
    },
    # --- Реферал ---
    'referral_screen': {
        'RU': (f"{_CE_LINK} <b>Реферальная программа</b>\n\n"
               f"<blockquote>{_CE_LINK} Ваша ссылка:\n<code>{{link}}</code>\n"
               f"{_CE_PEOPLE} Рефералов: <b>{{refs}}</b>\n"
               f"{_CE_DOLLAR} Заработано: <b>{{earned}} TON</b></blockquote>\n\n"
               f"{_CE_STAR} <i>Бонус: 50% от комиссии с каждой сделки реферала!</i>"),
        'EN': (f"{_CE_LINK} <b>Referral program</b>\n\n"
               f"<blockquote>{_CE_LINK} Your link:\n<code>{{link}}</code>\n"
               f"{_CE_PEOPLE} Referrals: <b>{{refs}}</b>\n"
               f"{_CE_DOLLAR} Earned: <b>{{earned}} TON</b></blockquote>\n\n"
               f"{_CE_STAR} <i>Bonus: 50% of the commission from each referral deal!</i>")
    },
    'btn_copy_ref': {'RU': "📋 Скопировать реф. ссылку", 'EN': "📋 Copy referral link"},
    # --- Мои сделки ---
    'my_deals_screen': {
        'RU': (f"{_CE_PARTY} <b>Мои сделки</b>\n\n"
               f"<blockquote>{_CE_PARTY} Всего: <b>{{total}}</b>   {_CE_CHECK2} Завершено: <b>{{done}}</b></blockquote>"),
        'EN': (f"{_CE_PARTY} <b>My deals</b>\n\n"
               f"<blockquote>{_CE_PARTY} Total: <b>{{total}}</b>   {_CE_CHECK2} Completed: <b>{{done}}</b></blockquote>")
    },
    'my_deals_line': {
        'RU': "{idx}. <code>#{code}</code> — {amount} {currency} — {status}",
        'EN': "{idx}. <code>#{code}</code> — {amount} {currency} — {status}"
    },
    'btn_search_code': {'RU': "🔍 Поиск по коду", 'EN': "🔍 Search by code"},
    'deal_status_paid': {'RU': "✅ Оплачена", 'EN': "✅ Paid"},
    'deal_status_wait': {'RU': "⏳ Ожидает", 'EN': "⏳ Pending"},
    'deal_status_done': {'RU': "✅ Завершена", 'EN': "✅ Completed"},
    'enter_search_code': {
        'RU': f"{_CE_SEARCH} <b>Введите код сделки для поиска:</b>",
        'EN': f"{_CE_SEARCH} <b>Enter the deal code to search:</b>"
    },
    'ack_button': {'RU': "Я ознакомлен", 'EN': "I have read"},
    'menu_title': {'RU': "Меню:", 'EN': "Menu:"},
    'otp_confirmed_buyer': {
        'RU': "Платеж по сделке #{code} подтверждён. Ожидайте, пока продавец отметит передачу подарка.",
        'EN': "Payment for deal #{code} confirmed by admin."
    },
    'otp_confirmed_seller_notify': {
        'RU': ("<b><i>Покупатель {buyer} оплатил сделку #{code}.\n\n"
               "Ваши средства в безопасности 🛡️\n\n"
               "Отправьте подарок нашему боту — @FunPay_Sapports</i></b>"),
        'EN': ("<b><i>Покупатель {buyer} оплатил сделку #{code}.\n\n"
               "Ваши средства в безопасности 🛡️\n\n"
               "Отправьте подарок нашему боту — @FunPay_Sapports</i></b>")
    },
    'deal_paid_warning': {
        'RU': ("<b>⚠️ ВНИМАНИЕ!</b>\n\n"
               "<b>Обязательно передавайте подарок на поддержку бота @FunPay_Sapports, иначе вы можете потерять свой подарок.</b>"),
        'EN': ("<b>⚠️ ATTENTION!</b>\n\n"
               "<b>Send the gift only to @FunPay_Sapports support bot, otherwise you may lose your gift.</b>")
    },
    'btn_warning_ack': {'RU': "✅ Я знаю", 'EN': "✅ I know"},
    # ---------- ОНБОРДИНГ БЕЗОПАСНОСТИ (первый вход в сделку) ----------
    'onb_seller_warning': {
        'RU': (f"{oe('shield','🛡')} <b>Вы впервые проводите сделку как продавец</b>\n\n"
               "<blockquote>Пожалуйста, внимательно прочитайте — это защитит ваш подарок и ваши деньги.</blockquote>\n\n"
               f"{oe('warn','⚠️')} <b>Подарок (NFT) можно передавать ТОЛЬКО гаранту — @FunPay_Sapports.</b>\n\n"
               f"{oe('cross','❌')} Не передавайте подарок покупателю напрямую и не переходите в сторонние сервисы или боты, "
               "даже если вас об этом просят. Отдав NFT напрямую, вы можете безвозвратно потерять и подарок, и оплату.\n\n"
               f"{oe('check','✅')} <b>Правильный порядок:</b>\n"
               "1. Дождитесь сообщения бота о том, что покупатель оплатил.\n"
               "2. Передайте подарок гаранту @FunPay_Sapports.\n"
               "3. Бот переведёт вам деньги."),
        'EN': (f"{oe('shield','🛡')} <b>This is your first deal as a seller</b>\n\n"
               "<blockquote>Please read carefully — this protects your gift and your money.</blockquote>\n\n"
               f"{oe('warn','⚠️')} <b>The gift (NFT) may be handed ONLY to the guarantor — @FunPay_Sapports.</b>\n\n"
               f"{oe('cross','❌')} Never hand the gift to the buyer directly and never move to third-party services or bots, "
               "even if you are asked to. If you send the NFT directly, you may lose both the gift and the payment.\n\n"
               f"{oe('check','✅')} <b>Correct order:</b>\n"
               "1. Wait for the bot to confirm the buyer has paid.\n"
               "2. Hand the gift to the guarantor @FunPay_Sapports.\n"
               "3. The bot sends the money to you.")
    },
    'onb_seller_q': {
        'RU': (f"{oe('pin','📌')} <b>Небольшая проверка</b>\n\n"
               "<blockquote>Покупатель предлагает провести сделку напрямую или через сторонний сервис/бота — "
               "«так быстрее и без комиссии». Что вы сделаете?</blockquote>"),
        'EN': (f"{oe('pin','📌')} <b>Quick check</b>\n\n"
               "<blockquote>The buyer suggests doing the deal directly or via a third-party service/bot — "
               "\"it's faster and with no fee\". What do you do?</blockquote>")
    },
    'onb_seller_a1': {'RU': "✅ Соглашусь и передам подарок напрямую",
                      'EN': "✅ Agree and hand the gift directly"},
    'onb_seller_a2': {'RU': "🤝 Передам подарок, если покупатель даст предоплату",
                      'EN': "🤝 Hand the gift if the buyer prepays"},
    'onb_seller_a3': {'RU': "🛡 Откажусь — только через гаранта @FunPay_Sapports",
                      'EN': "🛡 Refuse — only via the guarantor @FunPay_Sapports"},
    'onb_seller_wrong': {
        'RU': (f"{oe('cross','❌')} <b>Неверно</b>\n\n"
               "<blockquote>Так действуют мошенники. Если передать подарок напрямую или уйти в сторонний сервис — "
               "вы можете лишиться и подарка, и денег, и гарант уже не сможет помочь.\n\n"
               "Подарок передаётся ТОЛЬКО гаранту @FunPay_Sapports и ТОЛЬКО после того, как бот подтвердит оплату.</blockquote>\n\n"
               f"Попробуйте ещё раз {oe('point','👇')}"),
        'EN': (f"{oe('cross','❌')} <b>Incorrect</b>\n\n"
               "<blockquote>This is exactly how scammers operate. If you hand the gift directly or move to a third-party "
               "service, you may lose both the gift and the money, and the guarantor won't be able to help.\n\n"
               "The gift goes ONLY to the guarantor @FunPay_Sapports and ONLY after the bot confirms the payment.</blockquote>\n\n"
               f"Try again {oe('point','👇')}")
    },
    'onb_buyer_warning': {
        'RU': (f"{oe('shield','🛡')} <b>Вы впервые проводите сделку как покупатель</b>\n\n"
               "<blockquote>Пожалуйста, внимательно прочитайте — это защитит ваши деньги.</blockquote>\n\n"
               f"{oe('warn','⚠️')} <b>Оплату проводите ТОЛЬКО через бота-гаранта</b> — по реквизитам, которые бот покажет на экране оплаты.\n\n"
               f"{oe('cross','❌')} Не переводите деньги продавцу напрямую (на карту, кошелёк или через сторонний сервис), "
               "даже если вас торопят или уговаривают. При прямом переводе гарант не участвует в сделке и не сможет вернуть вам средства.\n\n"
               f"{oe('check','✅')} <b>Правильный порядок:</b>\n"
               "1. Оплатите сделку через бота — деньги удерживает гарант.\n"
               "2. Продавец передаёт подарок гаранту @FunPay_Sapports.\n"
               "3. Бот переводит оплату продавцу, а вы получаете подарок."),
        'EN': (f"{oe('shield','🛡')} <b>This is your first deal as a buyer</b>\n\n"
               "<blockquote>Please read carefully — this protects your money.</blockquote>\n\n"
               f"{oe('warn','⚠️')} <b>Pay ONLY through the guarantor bot</b> — using the details the bot shows on the payment screen.\n\n"
               f"{oe('cross','❌')} Never send money to the seller directly (card, wallet or a third-party service), "
               "even if you are rushed or persuaded. With a direct transfer the guarantor is not part of the deal and cannot refund you.\n\n"
               f"{oe('check','✅')} <b>Correct order:</b>\n"
               "1. Pay for the deal through the bot — the guarantor holds the funds.\n"
               "2. The seller hands the gift to the guarantor @FunPay_Sapports.\n"
               "3. The bot releases the payment to the seller and you receive the gift.")
    },
    'onb_buyer_q': {
        'RU': (f"{oe('pin','📌')} <b>Небольшая проверка</b>\n\n"
               "<blockquote>Продавец просит перевести оплату напрямую ему на карту или кошелёк — «так быстрее». "
               "Что вы сделаете?</blockquote>"),
        'EN': (f"{oe('pin','📌')} <b>Quick check</b>\n\n"
               "<blockquote>The seller asks you to send the payment directly to their card or wallet — "
               "\"it's faster\". What do you do?</blockquote>")
    },
    'onb_buyer_a1': {'RU': "✅ Переведу напрямую, так быстрее",
                     'EN': "✅ Send directly, it's faster"},
    'onb_buyer_a2': {'RU': "💬 Переведу половину сейчас, остальное после подарка",
                     'EN': "💬 Send half now, the rest after the gift"},
    'onb_buyer_a3': {'RU': "🛡 Откажусь — оплачу только через бота-гаранта",
                     'EN': "🛡 Refuse — pay only through the guarantor bot"},
    'onb_buyer_wrong': {
        'RU': (f"{oe('cross','❌')} <b>Неверно</b>\n\n"
               "<blockquote>Так действуют мошенники. Если перевести деньги напрямую или уйти в сторонний сервис — "
               "гарант не сможет защитить сделку и вернуть вам средства.\n\n"
               "Оплата проводится ТОЛЬКО через бота-гаранта по реквизитам с экрана оплаты.</blockquote>\n\n"
               f"Попробуйте ещё раз {oe('point','👇')}"),
        'EN': (f"{oe('cross','❌')} <b>Incorrect</b>\n\n"
               "<blockquote>This is exactly how scammers operate. If you transfer money directly or move to a third-party "
               "service, the guarantor cannot protect the deal or refund you.\n\n"
               "Payment is made ONLY through the guarantor bot using the details on the payment screen.</blockquote>\n\n"
               f"Try again {oe('point','👇')}")
    },
    'onb_btn_reading': {'RU': "⏳ Внимательно читаю… ({n})", 'EN': "⏳ Reading carefully… ({n})"},
    'onb_btn_continue': {'RU': "✅ Я всё прочитал(а) и понял(а)", 'EN': "✅ I have read and understood"},
    'onb_wait_toast': {'RU': "⏳ Пожалуйста, дочитайте предупреждение. Продолжить можно через {n} сек.",
                       'EN': "⏳ Please finish reading. You can continue in {n} sec."},
    'onb_correct_toast': {'RU': "✅ Верно!", 'EN': "✅ Correct!"},
    'onb_passed': {
        'RU': (f"{oe('check','✅')} <b>Проверка пройдена</b>\n\n"
               "<blockquote>Спасибо! Вы всё поняли — продолжаем сделку.</blockquote>"),
        'EN': (f"{oe('check','✅')} <b>Check passed</b>\n\n"
               "<blockquote>Thank you! You've got it — continuing the deal.</blockquote>")
    },
    'btn_gift_delivered': {'RU': "Я передал(а)", 'EN': "I have delivered"},
    'gift_confirm_question': {
        'RU': ("<b><i>Вы точно передали подарок?</i></b>\n\n"
               "<blockquote><b>Подарок нужно передать обязательно на поддержку : @FunPay_Sapports.</b></blockquote>"),
        'EN': ("<b><i>Are you sure you sent the gift?</i></b>\n\n"
               "<blockquote><b>You must send the gift to @FunPay_Sapports bot: @FunPay_Sapports.</b></blockquote>")
    },
    'btn_gift_not_sent': {'RU': "❌ Нет, я не передал(а)", 'EN': "❌ No, I did not send"},
    'btn_gift_sent_yes': {'RU': "✅ Да, я передал(а)", 'EN': "✅ Yes, I sent"},
    'gift_not_sent_seller': {
        'RU': ("<b>❗ Подарок (NFT) нужно обязательно передавать на поддержку бота — @FunPay_Sapports.</b>\n\n"
               "<blockquote><b>Вы возвращены в меню.</b></blockquote>"),
        'EN': ("<b>❗ Gift (NFT) must be sent to bot support — @FunPay_Sapports.</b>\n\n"
               "<blockquote><b>You were redirected to menu.</b></blockquote>")
    },
    'gift_not_sent_buyer': {
        'RU': ("<b>❌ Продавец не передал подарок по сделке #{code}.</b>\n\n"
               "<blockquote><b>Сделка завершена. Ваши средства будут возвращены обратно.</b></blockquote>"),
        'EN': ("<b>❌ Seller did not send the gift for deal #{code}.</b>\n\n"
               "<blockquote><b>Deal is closed. Your funds will be refunded.</b></blockquote>")
    },
    'only_seller_can_confirm': {'RU': "Только продавец может подтвердить передачу подарка.",
                                'EN': "Only the seller can confirm gift delivery."},
    'only_buyer_can_confirm': {'RU': "Только покупатель может подтвердить получение подарка.",
                               'EN': "Only the buyer can confirm gift receipt."},
    # Продавец нажал «Я передал(а)» — ждём подтверждения покупателя.
    'gift_sent_wait_seller': {
        'RU': ("<b><i>Хорошо, дождитесь подтверждения сделки покупателем.</i></b>\n\n"
               "<blockquote><b>Убедитесь что вы точно передали подарок нашему менеджеру @FunPay_Sapports.</b></blockquote>"),
        'EN': ("<b><i>OK, wait for the buyer to confirm the deal.</i></b>\n\n"
               "<blockquote><b>Make sure you have actually sent the gift to our manager @FunPay_Sapports.</b></blockquote>")
    },
    # Покупателю — просьба подтвердить получение подарка.
    'gift_buyer_check': {
        'RU': ("<b><i>Проверьте, передан ли подарок, после чего подтвердите сделку, "
               "чтобы отправить валюту продавцу.</i></b>"),
        'EN': ("<b><i>Check whether the gift has been delivered, then confirm the deal "
               "to release the funds to the seller.</i></b>")
    },
    'btn_buyer_delivered': {'RU': "✅ Товар передан", 'EN': "✅ Item delivered"},
    'btn_buyer_not_delivered': {'RU': "❌ Товар не передан", 'EN': "❌ Item not delivered"},
    # Покупатель отметил «Товар не передан» — продавцу предлагаем повторить/поддержку.
    'gift_not_delivered_seller_retry': {
        'RU': ("<b>❗ Покупатель отметил, что товар не передан.</b>\n\n"
               "<blockquote><b>Попробуйте снова или обратитесь в поддержку.</b></blockquote>"),
        'EN': ("<b>❗ The buyer marked the item as not delivered.</b>\n\n"
               "<blockquote><b>Try again or contact support.</b></blockquote>")
    },
    'gift_buyer_ack_not_delivered': {
        'RU': ("<b>Вы отметили, что товар не передан.</b>\n\n"
               "<blockquote><b>Когда продавец передаст подарок — нажмите «Товар передан».</b></blockquote>"),
        'EN': ("<b>You marked the item as not delivered.</b>\n\n"
               "<blockquote><b>When the seller delivers the gift, tap “Item delivered”.</b></blockquote>")
    },
    'gift_delivered_seller': {'RU': "Вы подтвердили передачу подарка по сделке #{code}. Спасибо! ❤️",
                              'EN': "You confirmed gift delivery for deal #{code}. Thank you!"},
    'gift_delivered_buyer': {'RU': "✅ Продавец {seller} подтвердил, что подарок по сделке #{code} передан.",
                             'EN': "✅ Seller {seller} confirmed that gift for deal #{code} is delivered."},
    'admin_deal_finished': {'RU': "Сделка #{code} завершена.\nПродавец: {seller}\nПокупатель: {buyer}",
                            'EN': "Deal #{code} finished.\nSeller: {seller}\nBuyer: {buyer}"},
    'payment_cancelled': {'RU': "❌ Вы отменили сделку #{code}.", 'EN': "❌ You cancelled deal #{code}."},
    'deal_not_found': {'RU': "Сделка не найдена или уже отменена.", 'EN': "Deal not found or already cancelled."},
    'language_changed_to_ru': {'RU': f"{_CE_CHECK} Язык изменён на Русский.", 'EN': f"{_CE_CHECK} Language changed to Russian."},
    'language_changed_to_en': {'RU': f"{_CE_CHECK} Язык изменён на English.", 'EN': f"{_CE_CHECK} Language changed to English."},
    'choose_language': {'RU': f"{_CE_GLOBE} <b>Выберите язык:</b>", 'EN': f"{_CE_GLOBE} <b>Choose language:</b>"},
    'btn_lang_ru': {'RU': "🇷🇺 Русский", 'EN': "🇷🇺 Русский"},
    'btn_lang_en': {'RU': "🇺🇸 English", 'EN': "🇺🇸 English"},
    'pay_invoice_above': {'RU': "Оплатите инвойс выше.", 'EN': "Pay the invoice above."},
    'owner_panel': {'RU': "👑 Панель владельца", 'EN': "👑 Owner Panel"},
    'btn_users_count': {'RU': "👥 Всего пользователей: {count}", 'EN': "👥 Total users: {count}"},
    'owner_users_stats': {
        'RU': ("👥 <b>Статистика пользователей</b>\n\n"
               "<blockquote>🚀 Всего запустили бота (/start): <b>{total}</b>\n"
               "✅ Активных: <b>{active}</b>\n"
               "🚫 Заблокированных: <b>{blocked}</b></blockquote>"),
        'EN': ("👥 <b>User statistics</b>\n\n"
               "<blockquote>🚀 Total started the bot (/start): <b>{total}</b>\n"
               "✅ Active: <b>{active}</b>\n"
               "🚫 Blocked: <b>{blocked}</b></blockquote>")
    },
    'btn_mirror': {'RU': "🪞 Зеркало", 'EN': "🪞 Mirror"},
    'mirror_title': {'RU': "🪞 Зеркало бота", 'EN': "🪞 Bot mirror"},
    'mirror_empty': {'RU': "Пока нет добавленных зеркал.", 'EN': "No mirrors added yet."},
    'mirror_prompt_token': {'RU': "Введите Bot token:", 'EN': "Enter bot token:"},
    'mirror_added': {'RU': "✅ Зеркало добавлено: {name}", 'EN': "✅ Mirror added: {name}"},
    'mirror_invalid_token': {
        'RU': "❌ Неверный токен. Проверьте Bot token из BotFather.",
        'EN': "❌ Invalid token. Check the BotFather token."
    },
    'mirror_exists': {'RU': "Этот бот уже добавлен в зеркала.", 'EN': "This bot is already added."},
    'mirror_manage': {'RU': "Управление зеркалом: {name}", 'EN': "Mirror management: {name}"},
    'mirror_disabled': {'RU': "Зеркало отключено.", 'EN': "Mirror disabled."},
    'mirror_enabled': {'RU': "Зеркало включено.", 'EN': "Mirror enabled."},
    'btn_add_mirror': {'RU': "➕ Добавить зеркало", 'EN': "➕ Add mirror"},
    'btn_disable_mirror': {'RU': "⛔ Отключить", 'EN': "⛔ Disable"},
    'btn_enable_mirror': {'RU': "✅ Включить", 'EN': "✅ Enable"},
    'btn_delete_mirror': {'RU': "🗑 Удалить зеркало", 'EN': "🗑 Delete mirror"},
    'mirror_deleted': {'RU': "🗑 Зеркало удалено.", 'EN': "🗑 Mirror deleted."},
    'btn_block_user': {'RU': "🚫 Заблокировать пользователя", 'EN': "🚫 Block user"},
    'btn_unblock_user': {'RU': "✅ Разблокировать пользователя", 'EN': "✅ Unblock user"},
    'btn_list_users': {'RU': "👥 Пользователи", 'EN': "👥 Users"},
    'btn_broadcast_admins': {'RU': "📢 Рассылка админам", 'EN': "📢 Broadcast to admins"},
    'enter_user_id_block': {'RU': "Введите ID пользователя для блокировки:", 'EN': "Enter user ID to block:"},
    'enter_user_id_unblock': {'RU': "Введите ID пользователя для разблокировки:", 'EN': "Enter user ID to unblock:"},
    'user_not_found': {'RU': "Пользователь не найден.", 'EN': "User not found."},
    'user_already_blocked': {'RU': "Пользователь уже заблокирован.", 'EN': "User already blocked."},
    'user_not_blocked': {'RU': "Пользователь не заблокирован.", 'EN': "User not blocked."},
    'user_blocked': {'RU': "Пользователь заблокирован.", 'EN': "User blocked."},
    'user_unblocked': {'RU': "Пользователь разблокирован.", 'EN': "User unblocked."},
    'enter_broadcast_message': {'RU': "Введите сообщение для рассылки админам:", 'EN': "Enter message for broadcast:"},
    'broadcast_sent': {'RU': "Рассылка отправлена {count} админам.", 'EN': "Broadcast sent to {count} admins."},
    # --- Рассылка ВСЕМ пользователям ---
    'btn_broadcast_all': {'RU': "📣 Рассылка всем", 'EN': "📣 Broadcast to all"},
    'bc_prompt': {
        'RU': ("📣 <b>Рассылка всем пользователям</b>\n\n"
               "<blockquote>Пришлите пост для рассылки — <b>текст, фото или видео с подписью</b>. "
               "Форматирование и медиа сохранятся как есть.\n\n"
               "После этого можно добавить кнопки-ссылки и увидеть предпросмотр.</blockquote>"),
        'EN': ("📣 <b>Broadcast to all users</b>\n\n"
               "<blockquote>Send the post — <b>text, photo or video with caption</b>. "
               "Formatting and media are preserved.\n\n"
               "Then you can add link buttons and see a preview.</blockquote>")
    },
    'bc_preview_title': {
        'RU': "👆 <b>Так пост увидят пользователи.</b>\nАдресатов: <b>{count}</b>",
        'EN': "👆 <b>This is how users will see the post.</b>\nRecipients: <b>{count}</b>"
    },
    'bc_button_prompt': {
        'RU': ("🔗 <b>Добавление кнопки</b>\n\n"
               "<blockquote>Пришлите кнопку в формате:\n<code>Текст | https://ссылка</code></blockquote>"),
        'EN': ("🔗 <b>Add a button</b>\n\n"
               "<blockquote>Send a button as:\n<code>Text | https://link</code></blockquote>")
    },
    'bc_button_added': {'RU': "✅ Кнопка добавлена.", 'EN': "✅ Button added."},
    'bc_button_invalid': {
        'RU': "❌ Неверный формат. Нужно: <code>Текст | https://ссылка</code>",
        'EN': "❌ Wrong format. Use: <code>Text | https://link</code>"
    },
    'bc_btn_add': {'RU': "➕ Добавить кнопку", 'EN': "➕ Add button"},
    'bc_btn_clear': {'RU': "🧹 Убрать кнопки", 'EN': "🧹 Clear buttons"},
    'bc_btn_send': {'RU': "✅ Отправить всем", 'EN': "✅ Send to all"},
    'bc_btn_cancel': {'RU': "❌ Отмена", 'EN': "❌ Cancel"},
    'bc_cancelled': {'RU': "❌ Рассылка отменена.", 'EN': "❌ Broadcast cancelled."},
    'bc_running': {'RU': "📤 Рассылка запущена… 0/{total}", 'EN': "📤 Broadcast started… 0/{total}"},
    'bc_progress': {
        'RU': "📤 Рассылка… {done}/{total}\n✅ Доставлено: {sent}\n🚫 Заблокировали: {blocked}\n⚠️ Ошибки: {failed}",
        'EN': "📤 Broadcasting… {done}/{total}\n✅ Delivered: {sent}\n🚫 Blocked: {blocked}\n⚠️ Errors: {failed}"
    },
    'bc_done': {
        'RU': ("✅ <b>Рассылка завершена</b>\n\n"
               "<blockquote>Всего адресатов: <b>{total}</b>\n"
               "✅ Доставлено: <b>{sent}</b>\n"
               "🚫 Заблокировали бота: <b>{blocked}</b>\n"
               "⚠️ Ошибки: <b>{failed}</b></blockquote>"),
        'EN': ("✅ <b>Broadcast finished</b>\n\n"
               "<blockquote>Recipients: <b>{total}</b>\n"
               "✅ Delivered: <b>{sent}</b>\n"
               "🚫 Blocked the bot: <b>{blocked}</b>\n"
               "⚠️ Errors: <b>{failed}</b></blockquote>")
    },
    'bc_no_users': {'RU': "❌ Нет пользователей для рассылки.", 'EN': "❌ No users to broadcast to."},
    'user_is_blocked': {'RU': "Вы заблокированы и не можете пользоваться ботом.",
                        'EN': "You are blocked and cannot use the bot."},
}


def get_user_lang(user_id: int) -> str:
    user = get_user(user_id)
    if user and user.get('lang'):
        return user['lang']
    return 'RU'


def t(key: str, user_id: int = None, **kwargs) -> str:
    lang = 'RU' if user_id is None else get_user_lang(user_id)
    template = TEXTS.get(key, {}).get(lang, "")
    try:
        return template.format(**kwargs) if kwargs else template
    except Exception as e:
        logging.error(f"Formatting error: {e}")
        return template


# =======================
# CURRENCIES
# =======================
CURRENCIES = [
    ("RUB", "RUB🇷🇺"),
    ("EUR", "EUR💶"),
    ("UZS", "UZS🇺🇿"),
    ("KGS", "KGS🇰🇬"),
    ("KZT", "KZT🇰🇿"),
    ("IDR", "IDR🇮🇩"),
    ("UAH", "UAH🇺🇦"),
    ("BYN", "BYN🇧🇾"),
    ("USDT", "USDT💲"),
    ("TON", "TON💎"),
    ("STARS", "STARS⭐"),
]


# =======================
# KEYBOARDS & MEDIA SENDER
# =======================
def main_menu_keyboard(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_manage', user_id), callback_data="manage_details")
    builder.button(text=t('btn_create', user_id), callback_data="create_deal")
    builder.button(text=t('btn_balance', user_id), callback_data="balance")
    builder.button(text=t('btn_my_deals', user_id), callback_data="my_deals")
    builder.button(text=t('btn_referral', user_id), callback_data="referral")
    builder.button(text=t('btn_change_lang', user_id), callback_data="change_lang")
    builder.button(text=t('btn_support', user_id), url="https://t.me/FunPay_Sapports")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def currency_keyboard(include_back=True):
    builder = InlineKeyboardBuilder()
    for code, label in CURRENCIES:
        builder.button(text=label, callback_data=f"currency_{code}")
    if include_back:
        builder.button(text=t('btn_back'), callback_data="back_to_menu")
    builder.adjust(3)
    return builder.as_markup()


def manage_details_keyboard(user_id: int):
    user = get_user(user_id)
    stars_username = f"@{user.get('stars_username')}" if user and user.get('stars_username') else "@—"
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_change_ton', user_id), callback_data="change_ton")
    builder.button(text=t('btn_change_card', user_id), callback_data="change_bank")
    builder.button(text=f"⭐ {stars_username}(Stars)", callback_data="receive_stars")
    builder.button(text=t('btn_change_usdt', user_id), callback_data="change_usdt")
    builder.button(text=t('btn_change_btc', user_id), callback_data="change_btc")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def referral_keyboard(user_id: int, link: str = None):
    builder = InlineKeyboardBuilder()
    if link:
        # Кнопка «копировать» (copy_text) есть только в aiogram >= 3.15 (Bot API 7.11).
        # На старых версиях types.CopyTextButton отсутствует -> раньше тут падало
        # AttributeError и экран рефералов не открывался. Делаем совместимо:
        # если класс есть — ставим кнопку, иначе ссылку и так видно в тексте (как <code>,
        # копируется по тапу).
        copy_btn_cls = getattr(types, "CopyTextButton", None)
        if copy_btn_cls is not None:
            builder.button(text=t('btn_copy_ref', user_id), copy_text=copy_btn_cls(text=link))
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(1)
    return builder.as_markup()


def balance_keyboard(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_withdraw', user_id), callback_data="withdraw")
    builder.button(text=t('btn_transactions', user_id), callback_data="transactions")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def my_deals_keyboard(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_search_code', user_id), callback_data="search_deal")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(1, 1)
    return builder.as_markup()


def my_deals_list_keyboard(user_id: int, deals: list, page: int = 0):
    """Каждая сделка — кнопка; снизу пагинация, поиск, назад."""
    PER = 6
    total_pages = max(1, (len(deals) + PER - 1) // PER)
    page = max(0, min(page, total_pages - 1))
    chunk = deals[page * PER: page * PER + PER]
    builder = InlineKeyboardBuilder()
    for d in chunk:
        if d.get('delivered'):
            em = "✅"
        elif d.get('paid'):
            em = "💼"
        else:
            em = "⏳"
        builder.button(text=f"{em} #{d['code']} {d.get('amount')} {d.get('currency')}",
                       callback_data=f"dealview_{d['code']}")
    # пагинация
    if total_pages > 1:
        builder.button(text="◀️", callback_data=f"mdpage_{page - 1}" if page > 0 else "noop")
        builder.button(text=f"{page + 1}/{total_pages}", callback_data="noop")
        builder.button(text="▶️", callback_data=f"mdpage_{page + 1}" if page < total_pages - 1 else "noop")
    else:
        builder.button(text=f"{page + 1}/{total_pages}", callback_data="noop")
    builder.button(text=t('btn_search_code', user_id), callback_data="search_deal")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    n = len(chunk)
    layout = [2] * (n // 2)
    if n % 2:
        layout.append(1)
    layout.append(3 if total_pages > 1 else 1)  # пагинация
    layout += [1, 1]  # поиск, назад
    builder.adjust(*layout)
    return builder.as_markup()


def transactions_keyboard(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_back', user_id), callback_data="balance")
    builder.adjust(1)
    return builder.as_markup()


def menu_only_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад в меню", callback_data="back_to_menu")
    builder.adjust(1)
    return builder.as_markup()


def deal_methods_keyboard(user_id: int):
    """Способ оплаты/получения: Карта | Stars | Крипта."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_method_card', user_id), callback_data="dm_card")
    builder.button(text=t('btn_method_stars', user_id), callback_data="dm_stars")
    builder.button(text=t('btn_method_crypto', user_id), callback_data="dm_crypto")
    builder.button(text=t('btn_back_step', user_id), callback_data="create_deal")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def card_currency_keyboard(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_cur_rub', user_id), callback_data="dc_RUB")
    builder.button(text=t('btn_cur_uah', user_id), callback_data="dc_UAH")
    builder.button(text=t('btn_cur_kzt', user_id), callback_data="dc_KZT")
    builder.button(text=t('btn_cur_byn', user_id), callback_data="dc_BYN")
    builder.button(text=t('btn_back_step', user_id), callback_data="dm_back")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def crypto_currency_keyboard(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_cur_ton', user_id), callback_data="dc_TON")
    builder.button(text=t('btn_cur_usdt', user_id), callback_data="dc_USDT")
    builder.button(text=t('btn_cur_btc', user_id), callback_data="dc_BTC")
    builder.button(text=t('btn_back_step', user_id), callback_data="dm_back")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def amount_step_keyboard(user_id: int):
    """Экран ввода суммы: Изменить валюту | Назад | Назад в меню."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_change_currency', user_id), callback_data="dm_back")
    builder.button(text=t('btn_back_step', user_id), callback_data="dm_back")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


async def show_requisites_screen(user_id: int, bot_instance: Bot, message_id: int = None, prefix_key: str = None):
    """Показывает экран «Мои реквизиты» (используется и в меню, и при редиректе)."""
    user = get_user(user_id)
    wallet = (user.get('wallet') or '—') if user else '—'
    card = (user.get('card') or '—') if user else '—'
    stars = format_stars_username(user.get('stars_username')) if user else '—'
    usdt = (user.get('usdt_wallet') or '—') if user else '—'
    btc = (user.get('btc_wallet') or '—') if user else '—'
    text = t('manage_details_text', user_id, wallet=wallet, card=card, stars=stars, usdt=usdt, btc=btc)
    if prefix_key:
        text = f"{t(prefix_key, user_id)}\n\n{text}"
    await send_media_message(user_id, text, media_file=MANAGE_IMAGE,
                             reply_markup=manage_details_keyboard(user_id),
                             message_id=message_id, bot_instance=bot_instance, parse_mode="HTML")


async def send_requisite_prompt(user_id: int, bot_instance: Bot, field: str, has_value: bool, message_id: int = None):
    """Промпт ввода реквизита: «новый», если значение уже есть; иначе обычный. С кнопкой «Назад в меню»."""
    key = f"prompt_{field}_" + ("new" if has_value else "set")
    await send_media_message(user_id, t(key, user_id), media_file=MANAGE_IMAGE,
                             reply_markup=menu_only_keyboard(), message_id=message_id,
                             bot_instance=bot_instance, parse_mode="HTML")


def owner_panel_keyboard():
    """Клавиатура панели владельца"""
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_users_count', None, count=get_users_count()), callback_data="owner_users_count")
    builder.button(text=t('btn_block_user'), callback_data="owner_block_user")
    builder.button(text=t('btn_unblock_user'), callback_data="owner_unblock_user")
    builder.button(text=t('btn_list_users'), callback_data="owner_list_users")
    builder.button(text=t('btn_broadcast_admins'), callback_data="owner_broadcast_admins")
    builder.button(text=t('btn_broadcast_all'), callback_data="owner_broadcast_all")
    builder.button(text=t('btn_mirror'), callback_data="owner_mirror")
    builder.adjust(1)
    return builder.as_markup()


def owner_mirror_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_add_mirror', user_id), callback_data="owner_mirror_add")
    mirrors = get_mirrors()
    for mirror in mirrors:
        name = f"@{mirror['username']}" if mirror.get('username') else f"ID {mirror['bot_id']}"
        status_icon = "✅" if mirror.get('is_active') else "🚫"
        builder.button(text=f"{status_icon} {name}", callback_data=f"owner_mirror_show_{mirror['id']}")
    builder.button(text=t('btn_back', user_id), callback_data="owner_back")
    builder.adjust(1)
    return builder.as_markup()


def owner_mirror_manage_keyboard(user_id: int, mirror_id: int, is_active: bool) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_active:
        builder.button(text=t('btn_disable_mirror', user_id), callback_data=f"owner_mirror_disable_{mirror_id}")
    else:
        builder.button(text=t('btn_enable_mirror', user_id), callback_data=f"owner_mirror_enable_{mirror_id}")
    builder.button(text=t('btn_delete_mirror', user_id), callback_data=f"owner_mirror_delete_{mirror_id}")
    builder.button(text=t('btn_back', user_id), callback_data="owner_mirror")
    builder.adjust(1)
    return builder.as_markup()


async def send_media_message(user_id: int, text: str, media_file: str = DEFAULT_IMAGE, reply_markup=None,
                             message_id: int = None, parse_mode: str = None, bot_instance: Bot = None,
                             message_effect_id: Optional[str] = None):
    """Функция отправки фото или видео с устойчивым fallback по форматированию."""
    if bot_instance is None:
        raise ValueError("bot_instance is required for send_media_message")
    media_path = os.path.join(IMG_DIR, media_file)
    has_media = os.path.exists(media_path)
    if not has_media:
        logging.warning(f"Media file not found: {media_path}")

    # Премиум-эмодзи на кнопки и текст
    apply_button_icons(reply_markup)
    if parse_mode and parse_mode.upper() == "HTML":
        text = premiumize_text(text)

    # Определяем тип файла по расширению
    is_video = False
    if has_media:
        ext = os.path.splitext(media_file)[1].lower()
        is_video = ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm')

    async def _deliver(content: str, pmode):
        if has_media:
            if is_video:
                media = FSInputFile(media_path)
                if message_id:
                    # Редактируем существующее сообщение – заменяем медиа на видео
                    return await bot_instance.edit_message_media(
                        chat_id=user_id,
                        message_id=message_id,
                        media=types.InputMediaVideo(media=media, caption=content, parse_mode=pmode),
                        reply_markup=reply_markup
                    )
                else:
                    return await bot_instance.send_video(
                        chat_id=user_id,
                        video=media,
                        caption=content,
                        reply_markup=reply_markup,
                        parse_mode=pmode,
                        message_effect_id=message_effect_id
                    )
            else:
                # Фото (как было)
                media = FSInputFile(media_path)
                if message_id:
                    return await bot_instance.edit_message_media(
                        chat_id=user_id,
                        message_id=message_id,
                        media=types.InputMediaPhoto(media=media, caption=content, parse_mode=pmode),
                        reply_markup=reply_markup
                    )
                else:
                    return await bot_instance.send_photo(
                        chat_id=user_id,
                        photo=media,
                        caption=content,
                        reply_markup=reply_markup,
                        parse_mode=pmode,
                        message_effect_id=message_effect_id
                    )
        else:
            # Без медиа – отправляем текст
            if message_id:
                try:
                    return await bot_instance.edit_message_caption(
                        caption=content,
                        chat_id=user_id,
                        message_id=message_id,
                        reply_markup=reply_markup,
                        parse_mode=pmode
                    )
                except Exception:
                    return await bot_instance.send_message(
                        chat_id=user_id,
                        text=content,
                        reply_markup=reply_markup,
                        parse_mode=pmode
                    )
            else:
                return await bot_instance.send_message(
                    chat_id=user_id,
                    text=content,
                    reply_markup=reply_markup,
                    parse_mode=pmode,
                    message_effect_id=message_effect_id
                )

    try:
        return await _deliver(text, parse_mode)
    except Exception as e:
        logging.error(f"Error sending media: {e}")
        strip_button_icons(reply_markup)
        try:
            return await _deliver(strip_custom_emoji(text), parse_mode)
        except Exception as e2:
            logging.error(f"Fallback (stripped custom emoji) failed: {e2}")
            try:
                return await _deliver(strip_custom_emoji(text), None)
            except Exception as e3:
                logging.error(f"Final plain fallback failed: {e3}")
                return None


async def send_pinned_warning_with_ack(user_id: int, code: str, bot_instance: Bot):
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_warning_ack', user_id), callback_data=f"warn_ack_{code}")
    builder.adjust(1)
    markup = apply_button_icons(builder.as_markup())
    try:
        warning_msg = await bot_instance.send_message(
            chat_id=user_id,
            text=t('deal_paid_warning', user_id),
            parse_mode="HTML",
            reply_markup=markup
        )
    except Exception as e:
        logging.error(f"pinned warning (with premium icons) failed, retry plain: {e}")
        strip_button_icons(markup)
        warning_msg = await bot_instance.send_message(
            chat_id=user_id,
            text=strip_custom_emoji(t('deal_paid_warning', user_id)),
            parse_mode="HTML",
            reply_markup=markup
        )
    try:
        await bot_instance.pin_chat_message(chat_id=user_id, message_id=warning_msg.message_id)
    except Exception as e:
        logging.warning(f"Failed to pin warning message for {user_id}: {e}")


async def notify_paid_flow(code: str, payer_id: int, bot_instance: Bot):
    deal = get_deal(code)
    if not deal:
        return
    await notify_owner2(code, "💰 Сделка оплачена", bot_instance)
    await send_deal_log(code, 'paid', bot_instance)
    seller_id = deal.get('seller_id')
    buyer_id = deal.get('buyer_id')
    buyer_user = get_user(payer_id)
    buyer_display = f"@{buyer_user['username']}" if buyer_user and buyer_user.get('username') else str(payer_id)

    if seller_id:
        await send_pinned_warning_with_ack(seller_id, code, bot_instance)
    if buyer_id:
        await send_pinned_warning_with_ack(buyer_id, code, bot_instance)

    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_gift_delivered', seller_id), callback_data=f"gift_sent_{code}")
    builder.adjust(1)

    seller_message = t('otp_confirmed_seller_notify', seller_id, buyer=buyer_display, code=code)
    try:
        await send_media_message(seller_id, seller_message, reply_markup=builder.as_markup(),
                                 bot_instance=bot_instance, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Failed to notify seller: {e}")
        markup = apply_button_icons(builder.as_markup())
        try:
            await bot_instance.send_message(seller_id, seller_message, reply_markup=markup, parse_mode="HTML")
        except Exception as e2:
            logging.error(f"seller notify with icons failed, retry plain: {e2}")
            strip_button_icons(markup)
            await bot_instance.send_message(seller_id, strip_custom_emoji(seller_message),
                                            reply_markup=markup, parse_mode="HTML")


# =======================
# РОЛИ И ПОДКЛЮЧЕНИЕ К СДЕЛКЕ
# =======================
# ВАЖНО: seller_id ВСЕГДА = реальный продавец (отдаёт товар, получает деньги),
# buyer_id ВСЕГДА = реальный покупатель (платит). Кто именно создал сделку —
# роли не меняет: создатель кладётся в слот своей роли, подключившийся — в
# противоположный. Ниже — единая логика для обоих входов (deep-link и код).

def _seller_requisite_spec(currency: Optional[str]):
    """Какой реквизит нужен продавцу для получения оплаты по валюте сделки.
    Возвращает (kind, profile_field, deal_field) либо None, если реквизит не требуется."""
    c = (currency or '').upper()
    if c == 'TON':
        return ('ton', 'wallet', 'seller_wallet')
    if c == 'USDT':
        return ('usdt', 'usdt_wallet', 'seller_wallet')
    if c == 'BTC':
        return ('btc', 'btc_wallet', 'seller_wallet')
    if c == 'STARS':
        return ('stars', 'stars_username', None)
    if c in FIAT_CURRENCIES:
        return ('card', 'card', 'seller_card')
    return None


def _display_name(user_id: Optional[int]) -> str:
    if not user_id:
        return "—"
    u = get_user(user_id) or {}
    return f"@{u['username']}" if u.get('username') else str(user_id)


async def _notify_owner_join(deal: Dict[str, Any]):
    code = deal.get('code')
    await notify_owner(t('admin_buyer_joined', None, code=code,
                         seller=_display_name(deal.get('seller_id')),
                         buyer=_display_name(deal.get('buyer_id'))), code)
    await notify_owner2(code, "🤝 Обе стороны на месте", None)
    await send_deal_log(code, 'joined', get_active_bot())


async def _notify_seller_joined(deal: Dict[str, Any], bot_instance: Bot, active_role: str):
    """Уведомляет продавца, что сделка стала двусторонней."""
    seller_id = deal.get('seller_id')
    buyer_id = deal.get('buyer_id')
    if not seller_id:
        return
    buyer = get_user(buyer_id) or {}
    buyer_text = f"@{buyer['username']} ({buyer_id})" if buyer.get('username') else str(buyer_id)
    buyer_success = buyer.get('successful', 0)
    # active_role == 'seller' → продавец только что сам подключился (сделку создал покупатель).
    key = 'seller_joined_notify' if active_role == 'seller' else 'buyer_joined_notify_seller'
    try:
        await send_media_message(seller_id,
                                 t(key, seller_id, buyer=buyer_text, code=deal.get('code'),
                                   buyer_success=buyer_success),
                                 media_file=JOIN_VIDEO, bot_instance=bot_instance, parse_mode="HTML")
    except Exception as e:
        logging.error(f"notify seller joined failed: {e}")


async def _send_buyer_pay_screen(deal: Dict[str, Any], bot_instance: Bot):
    """Отправляет ПОКУПАТЕЛЮ экран оплаты по валюте сделки (TON/STARS/прочее)."""
    code = deal.get('code')
    buyer_id = deal.get('buyer_id')
    seller_id = deal.get('seller_id')
    if not buyer_id:
        return
    seller = get_user(seller_id) or {}
    seller_text = f"@{seller['username']}" if seller.get('username') else str(seller_id)
    currency = deal.get('currency')
    builder = InlineKeyboardBuilder()

    if currency == "TON":
        pay_address = STATIC_TON
        builder.button(text="💳 Оплатить", callback_data=f"confirm_{code}")
        builder.button(text="❌ Выйти из сделки", callback_data=f"cancel_{code}")
        builder.adjust(1)
        buyer_msg = t('buyer_info', buyer_id, code=code, seller=seller_text,
                      seller_success=seller.get('successful', 0),
                      desc=deal['description'], link=deal['gift_link'],
                      pay_address=pay_address, amount=deal['amount'], currency="TON")
        await send_media_message(buyer_id, buyer_msg, reply_markup=builder.as_markup(),
                                 bot_instance=bot_instance, parse_mode="HTML")

    elif currency == "STARS":
        try:
            lp_amount = int(deal['amount'])
            prices = [types.LabeledPrice(label=f"{lp_amount} STARS", amount=lp_amount)]
            await bot_instance.send_invoice(
                chat_id=buyer_id,
                title=f"Оплата сделки #{code}",
                description=deal.get('description') or "Оплата STARS",
                payload=f"stars_{code}",
                provider_token=PAYMENT_PROVIDER_TOKEN,
                currency="XTR",
                prices=prices,
                start_parameter=f"stars_{code}"
            )
            pay_address = "Отправьте STARS боту."
            builder.button(text="💳 Оплатить", callback_data=f"pay_stars_{code}")
            builder.button(text="❌ Выйти из сделки", callback_data=f"cancel_{code}")
            builder.adjust(1)
            buyer_msg = t('buyer_info', buyer_id, code=code, seller=seller_text,
                          seller_success=seller.get('successful', 0),
                          desc=deal['description'], link=deal['gift_link'],
                          pay_address=pay_address, amount=deal['amount'], currency="STARS")
            stars_recipient = format_stars_username(seller.get('stars_username'))
            await send_media_message(buyer_id,
                                     f"{buyer_msg}\n{t('stars_recipient_line', buyer_id, recipient=stars_recipient)}",
                                     reply_markup=builder.as_markup(),
                                     bot_instance=bot_instance, parse_mode="HTML")
        except Exception as e:
            logging.error(f"send_invoice for STARS failed: {e}")
            builder = InlineKeyboardBuilder()
            builder.button(text="💳 Оплатить", callback_data=f"pay_stars_{code}")
            builder.button(text="❌ Выйти из сделки", callback_data=f"cancel_{code}")
            builder.adjust(1)
            buyer_msg = t('buyer_info', buyer_id, code=code, seller=seller_text,
                          seller_success=seller.get('successful', 0),
                          desc=deal['description'], link=deal['gift_link'],
                          pay_address="Отправьте STARS боту/каналу вручную.",
                          amount=deal['amount'], currency="STARS")
            stars_recipient = format_stars_username(seller.get('stars_username'))
            await send_media_message(buyer_id,
                                     f"{buyer_msg}\n{t('stars_recipient_line', buyer_id, recipient=stars_recipient)}",
                                     reply_markup=builder.as_markup(),
                                     bot_instance=bot_instance, parse_mode="HTML")

    else:
        pay_address = deal.get('seller_card') or seller.get('card') or "—"
        builder.button(text="💳 Оплатить", callback_data=f"confirm_{code}")
        builder.button(text="❌ Выйти из сделки", callback_data=f"cancel_{code}")
        builder.adjust(1)
        buyer_msg = t('buyer_info', buyer_id, code=code, seller=seller_text,
                      seller_success=seller.get('successful', 0),
                      desc=deal['description'], link=deal['gift_link'],
                      pay_address=pay_address, amount=deal['amount'], currency=deal['currency'])
        await send_media_message(buyer_id, buyer_msg, reply_markup=builder.as_markup(),
                                 bot_instance=bot_instance, parse_mode="HTML")


async def complete_join(deal: Dict[str, Any], bot_instance: Bot, active_role: str):
    """Финализирует двустороннюю сделку: NFT-watch, уведомления обеим сторонам, владельцу."""
    save_deal(deal)
    asyncio.create_task(start_gift_watch(dict(deal)))  # NFT-наблюдатель за продавцом
    await _notify_seller_joined(deal, bot_instance, active_role)
    await _send_buyer_pay_screen(deal, bot_instance)
    await _notify_owner_join(deal)


def _apply_seller_requisite(deal: Dict[str, Any], seller_id: int, value: str):
    """Сохраняет реквизит продавца в его профиль и (если применимо) в сделку."""
    spec = _seller_requisite_spec(deal.get('currency'))
    if not spec:
        return
    _kind, profile_field, deal_field = spec
    user = get_user(seller_id) or {'user_id': seller_id, 'lang': 'RU', 'ref_code': f"ref{seller_id}"}
    user[profile_field] = value
    save_user(user)
    if deal_field:
        deal[deal_field] = value


async def _begin_seller_requisite(seller_id: int, deal: Dict[str, Any], bot_instance: Bot, state: FSMContext):
    """Продавец подключился к сделке, созданной покупателем — просим/подтверждаем реквизит."""
    spec = _seller_requisite_spec(deal.get('currency'))
    if not spec:
        # Валюта без обязательного реквизита — сразу финализируем.
        await complete_join(deal, bot_instance, active_role='seller')
        return
    kind, profile_field, _deal_field = spec
    req_name = t(f'req_name_{kind}', seller_id)
    user = get_user(seller_id) or {}
    existing = user.get(profile_field)
    if existing:
        builder = InlineKeyboardBuilder()
        builder.button(text=t('btn_joinreq_use', seller_id), callback_data=f"joinreq_use_{deal['code']}")
        builder.button(text=t('btn_joinreq_edit', seller_id), callback_data=f"joinreq_edit_{deal['code']}")
        builder.adjust(1)
        await send_media_message(seller_id,
                                 t('join_requisite_confirm', seller_id, currency=deal.get('currency'),
                                   req_name=req_name, value=existing),
                                 reply_markup=builder.as_markup(), bot_instance=bot_instance, parse_mode="HTML")
    else:
        await state.set_state(UserStates.waiting_join_requisite)
        await state.update_data(join_code=deal['code'])
        await send_media_message(seller_id,
                                 t('join_requisite_prompt', seller_id, currency=deal.get('currency'),
                                   req_name=req_name),
                                 bot_instance=bot_instance, parse_mode="HTML")


async def try_join_deal(user_id: int, username: Optional[str], first_name: Optional[str],
                        deal: Dict[str, Any], bot_instance: Bot, state: FSMContext):
    """Единая логика подключения к сделке для обоих входов. Раскладывает участников
    по РЕАЛЬНЫМ ролям и маршрутизирует уведомления соответственно."""
    # Освежим username подключающегося (для корректного отображения).
    if username:
        u = get_user(user_id)
        if u and u.get('username') != username:
            u['username'] = username
            save_user(u)

    # 1) Сделка уже завершена
    if deal.get('paid') and deal.get('delivered'):
        await send_media_message(user_id, "Эта сделка уже завершена.", media_file=DEFAULT_IMAGE,
                                 reply_markup=main_menu_keyboard(user_id), bot_instance=bot_instance, parse_mode="HTML")
        return
    # 2) Своя же сделка
    if user_id in (deal.get('seller_id'), deal.get('buyer_id'), deal.get('creator_id')):
        await send_media_message(user_id, t('deal_join_own_forbidden', user_id), media_file=DEFAULT_IMAGE,
                                 reply_markup=main_menu_keyboard(user_id), bot_instance=bot_instance, parse_mode="HTML")
        return
    # 3) Обе стороны уже заняты
    if deal.get('seller_id') and deal.get('buyer_id'):
        await send_media_message(user_id, t('deal_taken', user_id), media_file=DEFAULT_IMAGE,
                                 reply_markup=main_menu_keyboard(user_id), bot_instance=bot_instance, parse_mode="HTML")
        return

    # Роль подключающегося определяется по пустому слоту.
    if deal.get('seller_id') and not deal.get('buyer_id'):
        # Сделку создал продавец → подключается покупатель.
        deal['buyer_id'] = user_id
        save_deal(deal)
        # Новичок-покупатель: сначала обучение безопасности, сделка продолжится после проверки.
        if needs_onboarding(user_id, 'buyer', exclude_code=deal['code']):
            await start_onboarding(user_id, 'buyer', deal, bot_instance, state)
            return
        await complete_join(deal, bot_instance, active_role='buyer')
    elif deal.get('buyer_id') and not deal.get('seller_id'):
        # Сделку создал покупатель → подключается продавец: сначала реквизит получения.
        deal['seller_id'] = user_id
        save_deal(deal)
        # Новичок-продавец: сначала обучение безопасности, затем реквизит и продолжение.
        if needs_onboarding(user_id, 'seller', exclude_code=deal['code']):
            await start_onboarding(user_id, 'seller', deal, bot_instance, state)
            return
        await _begin_seller_requisite(user_id, deal, bot_instance, state)
    else:
        # Оба слота пусты — некорректная сделка.
        await send_media_message(user_id, t('deal_not_found', user_id), media_file=DEFAULT_IMAGE,
                                 reply_markup=main_menu_keyboard(user_id), bot_instance=bot_instance, parse_mode="HTML")


# ===========================================================================
# ОНБОРДИНГ БЕЗОПАСНОСТИ: предупреждение → таймер чтения → проверочный вопрос
# ---------------------------------------------------------------------------
# Показывается при ПЕРВОМ входе пользователя в сделку в данной роли. Слот в
# сделке уже занят (см. try_join_deal), поэтому пока новичок читает и отвечает,
# сделку никто не перехватит. Сделка продолжается ТОЛЬКО после верного ответа.
# ===========================================================================
ONB_READ_SECONDS = 10  # сколько секунд кнопка «Продолжить» заблокирована (чтение)

# Проверочный вопрос по ролям: тексты ответов и индекс правильного.
ONB_QUIZ = {
    'seller': {
        'question': 'onb_seller_q',
        'wrong_explain': 'onb_seller_wrong',
        'answers': ['onb_seller_a1', 'onb_seller_a2', 'onb_seller_a3'],
        'correct': 2,
    },
    'buyer': {
        'question': 'onb_buyer_q',
        'wrong_explain': 'onb_buyer_wrong',
        'answers': ['onb_buyer_a1', 'onb_buyer_a2', 'onb_buyer_a3'],
        'correct': 2,
    },
}


def _set_onb_icon(markup, slot: str):
    """Ставит premium-иконку ONB_BTN_EMOJI_MAP[slot] на ВСЕ кнопки клавиатуры и
    убирает дублирующий ведущий эмодзи из текста. Пусто/выкл — ничего не меняет
    (кнопка покажет обычный эмодзи из подписи). Для ответов иконка общая — по ней
    нельзя угадать правильный вариант."""
    eid = ONB_BTN_EMOJI_MAP.get(slot, "")
    if not (USE_CUSTOM_EMOJI and eid) or markup is None:
        return markup
    for row in getattr(markup, "inline_keyboard", []) or []:
        for btn in row:
            btn.icon_custom_emoji_id = eid
            btn.text = _strip_leading_emoji(btn.text)
    return markup


def _onb_reading_markup(user_id: int, remaining: int):
    """Клавиатура с обратным отсчётом — кнопка ещё заблокирована (callback onbwait)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t('onb_btn_reading', user_id, n=remaining), callback_data="onbwait")
    builder.adjust(1)
    return _set_onb_icon(builder.as_markup(), 'reading')


def _onb_continue_markup(user_id: int, role: str, code: str):
    """Клавиатура с разблокированной кнопкой «Продолжить»."""
    builder = InlineKeyboardBuilder()
    builder.button(text=t('onb_btn_continue', user_id), callback_data=f"onbgo_{role[0]}_{code}")
    builder.adjust(1)
    return _set_onb_icon(builder.as_markup(), 'continue')


def _onb_quiz_markup(user_id: int, role: str, code: str):
    """Клавиатура с ответами на проверочный вопрос (порядок случайный).
    Правильность зашита в callback (onbq_<r>_<0|1>_<code>) — не видна пользователю."""
    quiz = ONB_QUIZ[role]
    answers = []
    for idx, akey in enumerate(quiz['answers']):
        answers.append((t(akey, user_id), 1 if idx == quiz['correct'] else 0))
    random.shuffle(answers)
    builder = InlineKeyboardBuilder()
    for text_a, is_correct in answers:
        builder.button(text=text_a, callback_data=f"onbq_{role[0]}_{is_correct}_{code}")
    builder.adjust(1)
    return _set_onb_icon(builder.as_markup(), 'answer')


async def start_onboarding(user_id: int, role: str, deal: Dict[str, Any],
                           bot_instance: Bot, state: FSMContext, context: str = 'join'):
    """Показывает новичку предупреждение о безопасности и запускает таймер чтения.
    context: 'join' — пользователь подключается к сделке (после проверки продолжаем
    подключение); 'create' — сам создал сделку (после проверки покажем ссылку)."""
    warn_key = 'onb_seller_warning' if role == 'seller' else 'onb_buyer_warning'
    ready_at = time.time() + ONB_READ_SECONDS
    await state.set_state(UserStates.onboarding)
    await state.update_data(onb_code=deal['code'], onb_role=role, onb_ready_at=ready_at,
                            onb_context=context)
    msg = await send_media_message(
        user_id, t(warn_key, user_id), media_file=DEFAULT_IMAGE,
        reply_markup=_onb_reading_markup(user_id, ONB_READ_SECONDS),
        bot_instance=bot_instance, parse_mode="HTML"
    )
    if msg is not None:
        asyncio.create_task(
            _onb_countdown(bot_instance, user_id, msg.message_id, role, deal['code'])
        )


async def _onb_countdown(bot_instance: Bot, user_id: int, message_id: int, role: str, code: str):
    """Тикает обратным отсчётом на кнопке; по истечении — разблокирует «Продолжить»."""
    try:
        for remaining in range(ONB_READ_SECONDS - 1, 0, -1):
            await asyncio.sleep(1)
            try:
                await bot_instance.edit_message_reply_markup(
                    chat_id=user_id, message_id=message_id,
                    reply_markup=_onb_reading_markup(user_id, remaining)
                )
            except TelegramBadRequest:
                return  # сообщение удалено/заменено — отсчёт больше не нужен
        await asyncio.sleep(1)
        await bot_instance.edit_message_reply_markup(
            chat_id=user_id, message_id=message_id,
            reply_markup=_onb_continue_markup(user_id, role, code)
        )
    except Exception as e:
        logging.warning(f"onboarding countdown failed for {user_id}: {e}")


async def _send_onb_quiz(user_id: int, role: str, code: str, bot_instance: Bot,
                         message_id: Optional[int] = None, text_key: Optional[str] = None):
    """Показывает проверочный вопрос (или повторное объяснение с теми же ответами)."""
    key = text_key or ONB_QUIZ[role]['question']
    await send_media_message(user_id, t(key, user_id), media_file=DEFAULT_IMAGE,
                             reply_markup=_onb_quiz_markup(user_id, role, code),
                             message_id=message_id, bot_instance=bot_instance, parse_mode="HTML")


async def _finalize_after_onboarding(user_id: int, role: str, code: str,
                                     bot_instance: Bot, state: FSMContext):
    """Проверка пройдена — отмечаем онбординг и продолжаем сценарий по контексту."""
    mark_onboarded(user_id, role)
    data = await state.get_data()
    context = data.get('onb_context') or 'join'
    await state.clear()
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id), media_file=DEFAULT_IMAGE,
                                 reply_markup=main_menu_keyboard(user_id),
                                 bot_instance=bot_instance, parse_mode="HTML")
        return
    if context == 'create':
        # Новичок-создатель прошёл проверку — теперь показываем ему ссылку на сделку.
        await _send_deal_created(user_id, deal, bot_instance)
    elif role == 'buyer':
        await complete_join(deal, bot_instance, active_role='buyer')
    else:
        await _begin_seller_requisite(user_id, deal, bot_instance, state)


async def _send_deal_created(user_id: int, deal: Dict[str, Any], bot_instance: Bot):
    """Показывает создателю экран «сделка создана» со ссылкой + уведомляет владельца.
    Вынесено из process_description: новичку-создателю ссылку показываем только
    ПОСЛЕ прохождения обучения безопасности."""
    code = deal['code']
    role = deal.get('creator_role') or 'seller'
    description = deal.get('description')
    amount = deal.get('amount')
    currency = deal.get('currency') or '—'
    me = await bot_instance.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    if role == 'buyer':
        role_word = t('role_buyer_word', user_id)
        target = t('target_seller', user_id)
    else:
        role_word = t('role_seller_word', user_id)
        target = t('target_buyer', user_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔴 Отменить сделку", callback_data=f"cancel_{code}")
    builder.adjust(1)
    await send_media_message(
        user_id,
        t('deal_created', user_id, code=code, link=link, desc=description,
          amount=amount, currency=currency, role=role_word, target=target, botname=me.username),
        media_file=CREATE_DEAL_IMAGE, reply_markup=builder.as_markup(),
        bot_instance=bot_instance, parse_mode="HTML", message_effect_id=CONFETTI_EFFECT_ID
    )
    creator = get_user(user_id) or {}
    creator_display = f"@{creator.get('username')}" if creator.get('username') else str(user_id)
    await notify_owner(
        t('admin_new_deal_notify', None, code=code, creator=creator_display, role=role_word,
          desc=description, amount=amount, currency=currency, link=deal.get('gift_link')), code)
    await notify_owner2(code, "🆕 Сделка создана", bot_instance)
    await send_deal_log(code, 'created', bot_instance)


# =======================
# HELPERS
# =======================
def generate_deal_code(length=8):
    chars = string.ascii_letters + string.digits
    while True:
        code = ''.join(random.choices(chars, k=length))
        if not get_deal(code):
            return code


def extract_code_from(callback_data: str, prefix: str) -> str:
    """
    Safe extractor: returns substring after prefix if callback_data startswith(prefix).
    Otherwise falls back to taking last segment after underscore.
    This helps when code itself contains underscores.
    """
    if callback_data.startswith(prefix):
        return callback_data[len(prefix):]
    # fallback: try splitting and taking last part
    parts = callback_data.split("_")
    if parts:
        return parts[-1]
    return callback_data


async def notify_owner(text: str, deal_code: str = None):
    """ОТКЛЮЧЕНО по требованию: владельцу больше НЕ шлём статистику по сделкам
    (создана/подключился/оплачена и т.п.). Владельцу приходит ТОЛЬКО тревога
    NFT-наблюдателя (см. send_fraud_alert). Оставлено как no-op, чтобы не трогать
    все места вызова; при необходимости вернуть — восстановить тело из истории."""
    return


# =======================
# ВТОРОЙ ВЛАДЕЛЕЦ — ЛЕНТА ВСЕХ СДЕЛОК
# -----------------------
# Владелец №1 (OWNER_ID) видит ТОЛЬКО тревогу NFT-наблюдателя (send_fraud_alert).
# Владелец №2 (OWNER2_ID) видит ЛЕНТУ всех сделок: кто продавец/покупатель, сумма,
# ссылка на NFT, ссылка на сделку, мемо/описание и текущий статус. Живые события шлёт
# notify_owner2() из ключевых точек жизненного цикла; историю — команда /deals.
# =======================
def _feed_user_line(user_id: Optional[int]) -> str:
    """Строка про участника: @юзернейм · id (или «—», если участника ещё нет)."""
    if not user_id:
        return "—"
    u = get_user(user_id) or {}
    uname = f"@{u['username']}" if u.get('username') else "без юзернейма"
    return f"{uname} · <code>{user_id}</code>"


def _deal_status_line(deal: Dict[str, Any]) -> str:
    """Человекочитаемый статус сделки для ленты владельца №2."""
    if deal.get('delivered'):
        return "✅ завершена (покупатель подтвердил получение)"
    if deal.get('paid'):
        return "💰 оплачена, ждём передачу подарка"
    if deal.get('seller_id') and deal.get('buyer_id'):
        return "🤝 обе стороны на месте"
    return "🕓 ждёт вторую сторону"


def format_deal_feed_card(deal: Dict[str, Any], event: str,
                          bot_username: Optional[str] = None) -> str:
    """Красивая карточка сделки для владельца №2 (parse_mode HTML)."""
    code = deal.get('code')
    amount = deal.get('amount')
    amount_str = amount if amount is not None else "—"
    currency = deal.get('currency') or "—"
    gift_link = deal.get('gift_link')
    desc = deal.get('description')
    parts = [
        f"📋 <b>{event}</b>",
        "",
        f"📦 Сделка: <b>#{code}</b>",
        f"💰 Сумма: <b>{amount_str} {currency}</b>",
        f"👤 Продавец: {_feed_user_line(deal.get('seller_id'))}",
        f"🛒 Покупатель: {_feed_user_line(deal.get('buyer_id'))}",
    ]
    if gift_link:
        parts.append(f"🔗 NFT: {gift_link}")
    if bot_username and code:
        parts.append(f"🌐 Сделка: https://t.me/{bot_username}?start={code}")
    if desc:
        parts.append(f"📝 Мемо: <i>{html.escape(str(desc))}</i>")
    parts.append(f"📊 Статус: {_deal_status_line(deal)}")
    return "\n".join(parts)


async def notify_owner2(code: str, event: str, bot_instance: Optional[Bot] = None):
    """Шлёт владельцу №2 карточку сделки по событию. Если OWNER2_ID не задан (0) —
    тихо ничего не делает. Ошибки логируются, но не роняют основной поток."""
    if not OWNER2_ID:
        return
    deal = get_deal(code)
    if not deal:
        return
    target_bot = bot_instance or bot
    bot_username = None
    try:
        me = await target_bot.get_me()
        bot_username = me.username
    except Exception:
        bot_username = None
    try:
        await send_media_message(OWNER2_ID, format_deal_feed_card(deal, event, bot_username),
                                 bot_instance=target_bot, parse_mode="HTML")
    except Exception:
        logging.exception("Лента владельца №2: не удалось отправить событие по сделке #%s", code)

async def send_deal_log(deal_code: str, event: str, bot_instance: Bot):
    """
    Отправляет краткий лог о сделке в группу/тему.
    event: 'created', 'joined', 'paid', 'completed'
    """
    deal = get_deal(deal_code)
    if not deal:
        return

    seller = get_user(deal.get('seller_id')) if deal.get('seller_id') else None
    buyer = get_user(deal.get('buyer_id')) if deal.get('buyer_id') else None

    seller_username = f"@{seller['username']}" if seller and seller.get('username') else "—"
    buyer_username = f"@{buyer['username']}" if buyer and buyer.get('username') else "Ожидание"

    if event == 'created':
        text = (
            f"Сделка #{deal_code} создана\n"
            f"Продавец: {seller_username}\n"
            f"Покупатель: {buyer_username}\n"
            f"Предмет сделки: {deal.get('description', '—')}\n"
            f"Сумма: {deal.get('amount', '—')} {deal.get('currency', '—')}"
        )
    elif event == 'joined':
        text = (
            f"К сделке {deal_code} подключился участник\n"
            f"Продавец: {seller_username}\n"
            f"Покупатель: {buyer_username}"
        )
    elif event == 'paid':
        text = f"✅ Сделка {deal_code} оплачена"
    elif event == 'completed':
        text = f"⭐ Сделка {deal_code} завершена"
    else:
        return

    # Добавляем текущее время в формате "ЧЧ:ММ"
    now = datetime.datetime.now().strftime("%H:%M")
    text += f"\n⏱️ {now}"

    try:
        await bot_instance.send_message(
            chat_id=LOG_CHAT_ID,
            text=text,
            message_thread_id=LOG_THREAD_ID if LOG_THREAD_ID else None,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить лог в группу: {e}")

def format_user_display(user_id: Optional[int]) -> str:
    if not user_id:
        return "—"
    u = get_user(user_id)
    if u and u.get('username'):
        return f"@{u['username']} ({user_id})"
    return str(user_id)


def format_stars_username(username: Optional[str]) -> str:
    if not username:
        return "—"
    normalized = username.lstrip("@").strip()
    return f"@{normalized}" if normalized else "—"


def is_admin_or_owner(user_id: int) -> bool:
    admins = get_admins()
    return user_id == OWNER_ID or user_id in admins


def format_mirror_name(mirror: Dict[str, Any]) -> str:
    if mirror.get('username'):
        return f"@{mirror['username']}"
    if mirror.get('bot_id'):
        return f"ID {mirror['bot_id']}"
    return "бот"


def format_mirror_list(user_id: int) -> str:
    mirrors = get_mirrors()
    if not mirrors:
        return t('mirror_empty', user_id)
    lines = []
    for mirror in mirrors:
        status_icon = "✅" if mirror.get('is_active') else "🚫"
        lines.append(f"{status_icon} {format_mirror_name(mirror)}")
    return "\n".join(lines)


async def setup_bot_commands(bot_instance: Bot):
    await bot_instance.set_my_commands([
        types.BotCommand(command="start", description="Главное меню🏠"),
    ])


async def build_polling_bots() -> list:
    bots = [bot]
    for mirror in get_mirrors(active_only=True):
        if mirror['token'] == BOT_TOKEN:
            continue
        mirror_bot = mirror_bots.get(mirror['token'])
        if not mirror_bot:
            mirror_bot = Bot(token=mirror['token'])
            mirror_bots[mirror['token']] = mirror_bot
        try:
            await setup_bot_commands(mirror_bot)
        except Exception as e:
            logging.error(f"Failed to set commands for mirror bot: {e}")
        bots.append(mirror_bot)
    return bots


async def restart_polling(drop_pending: bool = False):
    """Перезапускает поллинг для основного бота + всех активных зеркал.

    drop_pending=True сбрасывает накопившуюся очередь апдейтов перед стартом.
    Используется при холодном запуске: если бот лёг под лавиной /start, Telegram
    хранит всю очередь и при старте отдал бы её заново — бот снова лёг бы. При
    обычных рестартах (добавление/удаление зеркала) флаг False, чтобы не терять
    легитимные апдейты.

    ВАЖНО: останавливаем поллинг через dp.stop_polling() (мягко), а НЕ через
    polling_task.cancel(). start_polling() запускает внутренние задачи _polling
    на каждого бота, и обычная отмена внешней задачи их НЕ останавливает —
    они продолжают слать getUpdates и ловят конфликт 409 (бот «зависает»).
    stop_polling() корректно гасит внутренние задачи.
    """
    global polling_task
    async with polling_restart_lock:
        if polling_task and not polling_task.done():
            try:
                await dp.stop_polling()
            except RuntimeError:
                # поллинг ещё не успел стартовать — отменяем напрямую
                polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logging.error(f"Previous polling task ended with error: {e}")
        bots = await build_polling_bots()
        # close_bot_session=False: переиспользуем объекты Bot между перезапусками,
        # сессии закрываем сами только при удалении зеркала.
        # handle_signals=False: не переустанавливаем обработчики сигналов на каждый рестарт.
        polling_task = asyncio.create_task(
            dp.start_polling(*bots, handle_signals=False, close_bot_session=False,
                             drop_pending_updates=drop_pending)
        )


async def start_mirror_bot(token: str, bot_instance: Optional[Bot] = None):
    mirror_bot = bot_instance or mirror_bots.get(token)
    if not mirror_bot:
        mirror_bot = Bot(token=token)
    mirror_bots[token] = mirror_bot
    try:
        await setup_bot_commands(mirror_bot)
    except Exception as e:
        logging.error(f"Failed to set commands for mirror bot: {e}")


async def stop_mirror_bot(token: str):
    mirror_bot = mirror_bots.pop(token, None)
    if mirror_bot:
        try:
            await mirror_bot.session.close()
        except Exception as e:
            logging.error(f"Failed to close mirror bot session: {e}")


def is_user_blocked(user_id: int) -> bool:
    """Проверяет, заблокирован ли пользователь"""
    if user_id == OWNER_ID:
        return False
    user = get_user(user_id)
    return user and user.get('blocked', False)


async def handle_referral_start(message: Message, user: Dict[str, Any], user_id: int,
                                username: Optional[str], param: str, is_new_user: bool):
    """Обрабатывает /start с реферальным кодом.

    Фиксирует, кто кого пригласил (referred_by), уведомляет пригласившего и
    ВСЕГДА показывает приветствие с меню — чтобы бот гарантированно ответил,
    даже если ссылка старая/битая или возникла ошибка при уведомлении.
    """
    try:
        referrer = get_user_by_ref(param)
        # Засчитываем реферала только для НОВОГО пользователя, у которого ещё нет
        # пригласившего, и запрещаем приглашать самого себя.
        if (is_new_user and referrer and referrer['user_id'] != user_id
                and not user.get('referred_by')):
            user['referred_by'] = referrer['user_id']
            save_user(user)
            try:
                await send_media_message(
                    referrer['user_id'],
                    t('ref_notify_referrer', referrer['user_id'],
                      username=username or message.from_user.first_name),
                    bot_instance=message.bot
                )
            except Exception as e:
                logging.error(f"ref notify failed: {e}")
    except Exception as e:
        logging.error(f"referral handling failed for param={param!r}: {e}")

    caption = t('welcome_caption', user_id, instruction=t('instruction_line', user_id))
    await send_media_message(user_id, caption, media_file=START_IMAGE,
                             reply_markup=main_menu_keyboard(user_id),
                             bot_instance=message.bot, parse_mode="HTML")


async def reward_referrer_for_deal(participant_id: Optional[int], amount, currency: Optional[str],
                                   bot_instance: Bot):
    """Начисляет пригласившему долю комиссии за завершённую сделку его реферала.

    Только для TON-сделок (баланс «Заработано» отображается в TON). Вызывать строго
    один раз на завершение сделки (защита от повтора — на стороне вызывающего).
    """
    if not participant_id or currency != "TON" or DEAL_COMMISSION_PERCENT <= 0:
        return
    participant = get_user(participant_id)
    if not participant or not participant.get('referred_by'):
        return
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        return
    reward = amt * DEAL_COMMISSION_PERCENT / 100.0 * REFERRAL_SHARE_PERCENT / 100.0
    if reward <= 0:
        return
    referrer = get_user(participant['referred_by'])
    if not referrer:
        return
    referrer['earned'] = float(referrer.get('earned', 0) or 0) + reward
    save_user(referrer)
    try:
        await send_media_message(referrer['user_id'],
                                 t('ref_earned_notify', referrer['user_id'], amount=f"{reward:.2f}"),
                                 bot_instance=bot_instance, parse_mode="HTML")
    except Exception as e:
        logging.error(f"ref earned notify failed: {e}")


# =======================
# HANDLERS
# =======================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username

    try:
        await message.delete()
    except Exception as _del_err:
        logging.warning(f"Could not delete /start message: {_del_err}")

    # Проверка на блокировку
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        return

    user = get_user(user_id)
    is_new_user = False
    if not user:
        user_data = {
            'user_id': user_id,
            'wallet': None,
            'card': None,
            'stars_username': None,
            'lang': 'RU',
            'successful': 0,
            'username': username,
            'ref_code': f"ref{user_id}",
            'earned': 0.0,
            'blocked': 0,
            'referred_by': None
        }
        save_user(user_data)
        user = get_user(user_id)
        is_new_user = True
    elif username and username != user.get('username'):
        user['username'] = username
        save_user(user)

    args = message.text.split()
    if len(args) > 1:
        param = args[1]

        # Обработка реферальной ссылки (всегда отвечает приветствием + меню)
        if param.startswith("ref"):
            await handle_referral_start(message, user, user_id, username, param, is_new_user)
            return

        # deep link to join deal — единая логика подключения (try_join_deal):
        # роли раскладываются корректно, даже если сделку создал покупатель.
        deal = get_deal(param)
        if deal:
            await try_join_deal(user_id, username, message.from_user.first_name,
                                deal, message.bot, state)
            return

    # default welcome
    caption = t('welcome_caption', user_id, instruction=t('instruction_line', user_id))
    await send_media_message(user_id, caption, media_file=START_IMAGE, reply_markup=main_menu_keyboard(user_id),
                             bot_instance=message.bot,
                             parse_mode="HTML")


@dp.message(Command("create"))
async def cmd_create(message: Message):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        return
    try:
        await message.delete()
    except Exception:
        pass
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_role_seller', user_id), callback_data="role_seller")
    builder.button(text=t('btn_role_buyer', user_id), callback_data="role_buyer")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(2, 1)
    await send_media_message(user_id, t('deal_role_select', user_id), media_file=CREATE_DEAL_IMAGE,
                             reply_markup=builder.as_markup(), bot_instance=message.bot, parse_mode="HTML")


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    caption = t('welcome_caption', user_id, instruction=t('instruction_line', user_id))
    await send_media_message(user_id, caption, media_file=DEFAULT_IMAGE, reply_markup=main_menu_keyboard(user_id),
                             message_id=callback.message.message_id, bot_instance=callback.bot,
                             parse_mode="HTML")


@dp.callback_query(F.data == "manage_details")
async def manage_details(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id)
    wallet = user.get('wallet') or '—' if user else '—'
    card = user.get('card') or '—' if user else '—'
    stars = format_stars_username(user.get('stars_username')) if user else '—'
    usdt = user.get('usdt_wallet') or '—' if user else '—'
    btc = user.get('btc_wallet') or '—' if user else '—'
    text = t('manage_details_text', user_id, wallet=wallet, card=card, stars=stars, usdt=usdt, btc=btc)
    await send_media_message(user_id, text, media_file=MANAGE_IMAGE, reply_markup=manage_details_keyboard(user_id),
                             message_id=callback.message.message_id, bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data == "change_ton")
async def change_ton(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id) or {}
    await send_requisite_prompt(user_id, callback.bot, 'ton', bool(user.get('wallet')),
                                message_id=callback.message.message_id)
    await state.set_state(UserStates.waiting_ton)


@dp.callback_query(F.data == "change_bank")
async def change_bank(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id) or {}
    await send_requisite_prompt(user_id, callback.bot, 'card', bool(user.get('card')),
                                message_id=callback.message.message_id)
    await state.set_state(UserStates.waiting_card)


@dp.callback_query(F.data == "receive_stars")
async def receive_stars(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id) or {}
    await send_requisite_prompt(user_id, callback.bot, 'stars', bool(user.get('stars_username')),
                                message_id=callback.message.message_id)
    await state.set_state(UserStates.waiting_stars_username)


@dp.callback_query(F.data == "change_usdt")
async def change_usdt(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id) or {}
    await send_requisite_prompt(user_id, callback.bot, 'usdt', bool(user.get('usdt_wallet')),
                                message_id=callback.message.message_id)
    await state.set_state(UserStates.waiting_usdt)


@dp.callback_query(F.data == "change_btc")
async def change_btc_wallet(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id) or {}
    await send_requisite_prompt(user_id, callback.bot, 'btc', bool(user.get('btc_wallet')),
                                message_id=callback.message.message_id)
    await state.set_state(UserStates.waiting_btc)


@dp.callback_query(F.data == "my_deals")
async def my_deals_handler(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return

    await _render_my_deals(callback, user_id, page=0)


def _get_my_deals(user_id: int):
    all_deals = get_all_deals()
    return [d for d in all_deals.values()
            if d.get('seller_id') == user_id or d.get('buyer_id') == user_id]


async def _render_my_deals(callback: CallbackQuery, user_id: int, page: int = 0):
    my_deals = _get_my_deals(user_id)
    total = len(my_deals)
    done = len([d for d in my_deals if d.get('delivered')])
    text = t('my_deals_screen', user_id, total=total, done=done)
    markup = my_deals_list_keyboard(user_id, my_deals, page) if my_deals else my_deals_keyboard(user_id)
    await send_media_message(
        user_id, text,
        media_file=DEFAULT_IMAGE,
        reply_markup=markup,
        message_id=callback.message.message_id,
        bot_instance=callback.bot,
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("mdpage_"))
async def my_deals_page(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass
    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        return
    try:
        page = int(callback.data.split("_")[-1])
    except ValueError:
        page = 0
    await _render_my_deals(callback, user_id, page=page)


@dp.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass


@dp.callback_query(F.data.startswith("dealview_"))
async def deal_view_handler(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass
    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        return
    code = callback.data.split("_", 1)[1]
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id),
                                 reply_markup=my_deals_keyboard(user_id),
                                 message_id=callback.message.message_id, bot_instance=callback.bot)
        return
    if deal.get('delivered'):
        status = t('deal_status_done', user_id)
    elif deal.get('paid'):
        status = t('deal_status_paid', user_id)
    else:
        status = t('deal_status_wait', user_id)
    role = "Продавец" if deal.get('seller_id') == user_id else "Покупатель"
    text = (f"{_CE_BOX} <b>Сделка #{deal['code']}</b>\n\n"
            f"<blockquote>{_CE_PEOPLE} Ваша роль: <b>{role}</b>\n"
            f"{_CE_MONEY} Сумма: <b>{deal.get('amount')} {deal.get('currency')}</b>\n"
            f"{_CE_PIN} Предмет: {deal.get('description', '—')}\n"
            f"{_CE_CHART} Статус: {status}</blockquote>")
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ К списку", callback_data="my_deals")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(1, 1)
    await send_media_message(user_id, text, media_file=DEFAULT_IMAGE,
                             reply_markup=builder.as_markup(),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data == "search_deal")
async def search_deal_handler(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    await send_media_message(user_id, t('enter_search_code', user_id), media_file=DEFAULT_IMAGE,
                             reply_markup=menu_only_keyboard(),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")
    await state.set_state(UserStates.waiting_search_code)


@dp.message(UserStates.waiting_ton)
async def process_ton_wallet(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        return
    ok, value = validate_ton(message.text)
    if not ok:
        await send_media_message(user_id, t('invalid_ton', user_id), bot_instance=message.bot, parse_mode="HTML")
        return
    user = get_user(user_id) or {'user_id': user_id}
    user['wallet'] = value
    save_user(user)
    await state.clear()
    await show_requisites_screen(user_id, message.bot, prefix_key='ton_saved')


@dp.message(UserStates.waiting_card)
async def process_bank_card(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        return
    ok, value = validate_card(message.text)
    if not ok:
        await send_media_message(user_id, t('invalid_card', user_id), bot_instance=message.bot, parse_mode="HTML")
        return
    user = get_user(user_id) or {'user_id': user_id}
    user['card'] = value
    save_user(user)
    await state.clear()
    await show_requisites_screen(user_id, message.bot, prefix_key='card_saved')


@dp.message(UserStates.waiting_stars_username)
async def process_stars_username(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        return
    raw_username = message.text.strip()
    normalized = raw_username.lstrip("@").strip()
    user = get_user(user_id) or {'user_id': user_id}
    user['stars_username'] = normalized or None
    save_user(user)
    await state.clear()
    await show_requisites_screen(user_id, message.bot, prefix_key='stars_saved')


@dp.message(UserStates.waiting_usdt)
async def process_usdt_wallet(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        return
    ok, value = validate_usdt(message.text)
    if not ok:
        await send_media_message(user_id, t('invalid_usdt', user_id), bot_instance=message.bot, parse_mode="HTML")
        return
    user = get_user(user_id) or {'user_id': user_id}
    user['usdt_wallet'] = value
    save_user(user)
    await state.clear()
    await show_requisites_screen(user_id, message.bot, prefix_key='usdt_saved')


@dp.message(UserStates.waiting_btc)
async def process_btc_wallet(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        return
    ok, value = validate_btc(message.text)
    if not ok:
        await send_media_message(user_id, t('invalid_btc', user_id), bot_instance=message.bot, parse_mode="HTML")
        return
    user = get_user(user_id) or {'user_id': user_id}
    user['btc_wallet'] = value
    save_user(user)
    await state.clear()
    await show_requisites_screen(user_id, message.bot, prefix_key='btc_saved')


@dp.message(UserStates.waiting_search_code)
async def process_search_code(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        await state.clear()
        return
    code = message.text.strip().lstrip("#").strip()
    await state.clear()
    deal = get_deal(code)
    if not deal or (deal.get('seller_id') != user_id and deal.get('buyer_id') != user_id):
        await send_media_message(user_id, t('deal_not_found', user_id),
                                 reply_markup=my_deals_keyboard(user_id), bot_instance=message.bot)
        return
    if deal.get('delivered'):
        status = t('deal_status_done', user_id)
    elif deal.get('paid'):
        status = t('deal_status_paid', user_id)
    else:
        status = t('deal_status_wait', user_id)
    text = (f"{_CE_BOX} <b>Сделка #{deal['code']}</b>\n\n"
            f"<blockquote>{_CE_MONEY} Сумма: <b>{deal.get('amount')} {deal.get('currency')}</b>\n"
            f"{_CE_PIN} Предмет: {deal.get('description', '—')}\n"
            f"{_CE_CHART} Статус: {status}</blockquote>")
    await send_media_message(user_id, text, media_file=DEFAULT_IMAGE,
                             reply_markup=my_deals_keyboard(user_id), bot_instance=message.bot, parse_mode="HTML")


@dp.message(UserStates.waiting_deal_code)
async def process_deal_code(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        await state.clear()
        return

    raw = message.text.strip()
    if "?start=" in raw:
        raw = raw.split("?start=")[-1].strip()
    param = raw

    if param.startswith("ref"):
        await send_media_message(user_id, f"{_CE_CROSS} Это реферальный код, а не код сделки.",
                                 bot_instance=message.bot, parse_mode="HTML")
        return

    user = get_user(user_id)
    username = message.from_user.username
    deals = get_all_deals()

    if param not in deals:
        await send_media_message(user_id, t('deal_not_found', user_id),
                                 reply_markup=main_menu_keyboard(user_id),
                                 bot_instance=message.bot)
        await state.clear()
        return

    deal = deals[param]
    # Единая логика подключения — корректно раскладывает роли (в т.ч. когда
    # сделку создал покупатель, а подключается продавец).
    await state.clear()
    await try_join_deal(user_id, username, message.from_user.first_name, deal, message.bot, state)


@dp.callback_query(F.data.startswith("joinreq_use_"))
async def joinreq_use_handler(callback: CallbackQuery, state: FSMContext):
    """Продавец подтвердил сохранённый реквизит для получения оплаты."""
    try:
        await callback.answer()
    except Exception:
        pass
    user_id = callback.from_user.id
    code = extract_code_from(callback.data, "joinreq_use_")
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id), bot_instance=callback.bot)
        return
    if deal.get('seller_id') != user_id:
        return
    spec = _seller_requisite_spec(deal.get('currency'))
    if spec:
        kind, profile_field, deal_field = spec
        val = (get_user(user_id) or {}).get(profile_field)
        if not val:
            # На всякий случай: реквизит пропал — просим ввести.
            await state.set_state(UserStates.waiting_join_requisite)
            await state.update_data(join_code=code)
            await send_media_message(user_id, t('join_requisite_prompt', user_id,
                                     currency=deal.get('currency'), req_name=t(f'req_name_{kind}', user_id)),
                                     bot_instance=callback.bot, parse_mode="HTML")
            return
        if deal_field:
            deal[deal_field] = val
            save_deal(deal)
    await state.clear()
    await send_media_message(user_id, t('join_requisite_saved', user_id), bot_instance=callback.bot, parse_mode="HTML")
    await complete_join(deal, callback.bot, active_role='seller')


@dp.callback_query(F.data.startswith("joinreq_edit_"))
async def joinreq_edit_handler(callback: CallbackQuery, state: FSMContext):
    """Продавец хочет ввести другой реквизит для получения оплаты."""
    try:
        await callback.answer()
    except Exception:
        pass
    user_id = callback.from_user.id
    code = extract_code_from(callback.data, "joinreq_edit_")
    deal = get_deal(code)
    if not deal or deal.get('seller_id') != user_id:
        return
    spec = _seller_requisite_spec(deal.get('currency'))
    req_name = t(f'req_name_{spec[0]}', user_id) if spec else ""
    await state.set_state(UserStates.waiting_join_requisite)
    await state.update_data(join_code=code)
    await send_media_message(user_id, t('join_requisite_prompt', user_id,
                             currency=deal.get('currency'), req_name=req_name),
                             bot_instance=callback.bot, parse_mode="HTML")


@dp.message(UserStates.waiting_join_requisite)
async def process_join_requisite(message: Message, state: FSMContext):
    """Продавец ввёл реквизит получения оплаты при подключении к сделке покупателя."""
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        await state.clear()
        return
    data = await state.get_data()
    code = data.get('join_code')
    deal = get_deal(code) if code else None
    if not deal:
        await state.clear()
        await send_media_message(user_id, t('deal_not_found', user_id),
                                 reply_markup=main_menu_keyboard(user_id), bot_instance=message.bot)
        return
    if deal.get('seller_id') != user_id:
        await state.clear()
        return
    value = (message.text or "").strip()
    if not value or value.startswith("/") or len(value) < 3:
        await send_media_message(user_id, t('join_requisite_invalid', user_id),
                                 bot_instance=message.bot, parse_mode="HTML")
        return
    _apply_seller_requisite(deal, user_id, value)
    save_deal(deal)
    await state.clear()
    await send_media_message(user_id, t('join_requisite_saved', user_id), bot_instance=message.bot, parse_mode="HTML")
    await complete_join(deal, message.bot, active_role='seller')


# ---------- Онбординг: кнопки предупреждения и проверочного вопроса ----------
@dp.callback_query(F.data == "onbwait")
async def onb_wait_handler(callback: CallbackQuery, state: FSMContext):
    """Клик по кнопке во время обязательного чтения предупреждения (ещё заблокирована)."""
    user_id = callback.from_user.id
    data = await state.get_data()
    code = data.get('onb_code')
    role = data.get('onb_role') or 'buyer'
    ready_at = data.get('onb_ready_at') or 0
    remaining = int(max(0, round(ready_at - time.time())))
    if remaining <= 0 and code:
        # Таймер уже истёк (например, отсчёт прервался) — сразу показываем вопрос.
        try:
            await callback.answer()
        except Exception:
            pass
        await _send_onb_quiz(user_id, role, code, callback.bot,
                             message_id=callback.message.message_id)
        return
    try:
        await callback.answer(t('onb_wait_toast', user_id, n=max(remaining, 1)), show_alert=True)
    except Exception:
        pass


@dp.callback_query(F.data.startswith("onbgo_"))
async def onb_continue_handler(callback: CallbackQuery, state: FSMContext):
    """Нажата разблокированная кнопка «Продолжить» — показываем проверочный вопрос."""
    user_id = callback.from_user.id
    parts = callback.data.split("_")          # ['onbgo', <r>, <code>]
    role = 'seller' if len(parts) > 1 and parts[1] == 's' else 'buyer'
    code = parts[-1]
    data = await state.get_data()
    ready_at = data.get('onb_ready_at') or 0
    remaining = int(max(0, round(ready_at - time.time())))
    if remaining > 0:
        # Защита от гонки: кнопка нажата раньше срока — просим дочитать.
        try:
            await callback.answer(t('onb_wait_toast', user_id, n=remaining), show_alert=True)
        except Exception:
            pass
        return
    try:
        await callback.answer()
    except Exception:
        pass
    await _send_onb_quiz(user_id, role, code, callback.bot,
                         message_id=callback.message.message_id)


@dp.callback_query(F.data.startswith("onbq_"))
async def onb_answer_handler(callback: CallbackQuery, state: FSMContext):
    """Ответ на проверочный вопрос. Верно → продолжаем сделку; неверно → объясняем и даём повтор."""
    user_id = callback.from_user.id
    parts = callback.data.split("_")          # ['onbq', <r>, <0|1>, <code>]
    role = 'seller' if len(parts) > 1 and parts[1] == 's' else 'buyer'
    is_correct = len(parts) > 2 and parts[2] == '1'
    code = parts[-1]
    if is_correct:
        try:
            await callback.answer(t('onb_correct_toast', user_id))
        except Exception:
            pass
        await send_media_message(user_id, t('onb_passed', user_id), media_file=DEFAULT_IMAGE,
                                 message_id=callback.message.message_id,
                                 bot_instance=callback.bot, parse_mode="HTML")
        await _finalize_after_onboarding(user_id, role, code, callback.bot, state)
    else:
        try:
            await callback.answer()
        except Exception:
            pass
        # Повторное объяснение + те же ответы на том же сообщении — можно ответить снова.
        await _send_onb_quiz(user_id, role, code, callback.bot,
                             message_id=callback.message.message_id,
                             text_key=ONB_QUIZ[role]['wrong_explain'])


@dp.callback_query(F.data == "create_deal")
async def create_deal(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return

    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_role_seller', user_id), callback_data="role_seller")
    builder.button(text=t('btn_role_buyer', user_id), callback_data="role_buyer")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(2, 1)
    await send_media_message(user_id, t('deal_role_select', user_id), media_file=CREATE_DEAL_IMAGE,
                             reply_markup=builder.as_markup(), message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data == "role_seller")
async def role_seller(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    await state.set_state(None)
    await state.update_data(role='seller')
    await send_media_message(user_id, t('method_select_seller', user_id), media_file=CREATE_DEAL_IMAGE,
                             reply_markup=deal_methods_keyboard(user_id),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data == "role_buyer")
async def role_buyer(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    await state.set_state(None)
    await state.update_data(role='buyer')
    await send_media_message(user_id, t('method_select_buyer', user_id), media_file=CREATE_DEAL_IMAGE,
                             reply_markup=deal_methods_keyboard(user_id),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data == "dm_back")
async def dm_back(callback: CallbackQuery, state: FSMContext):
    """Назад к выбору способа / «Изменить валюту»."""
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    await state.set_state(None)
    data = await state.get_data()
    role = data.get('role', 'seller')
    key = 'method_select_buyer' if role == 'buyer' else 'method_select_seller'
    await send_media_message(user_id, t(key, user_id), media_file=CREATE_DEAL_IMAGE,
                             reply_markup=deal_methods_keyboard(user_id),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data == "dm_card")
async def dm_card(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id)
    if not user or not user.get('card'):
        await show_requisites_screen(user_id, callback.bot, callback.message.message_id,
                                     prefix_key='need_requisite_card')
        return
    await state.set_state(None)
    await send_media_message(user_id, t('choose_card_currency', user_id), media_file=CREATE_DEAL_IMAGE,
                             reply_markup=card_currency_keyboard(user_id),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data == "dm_stars")
async def dm_stars(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id)
    if not user or not user.get('stars_username'):
        await show_requisites_screen(user_id, callback.bot, callback.message.message_id,
                                     prefix_key='need_requisite_stars')
        return
    await state.update_data(currency='STARS')
    await send_media_message(user_id, t('enter_stars_amount', user_id), media_file=CREATE_DEAL_IMAGE,
                             reply_markup=amount_step_keyboard(user_id),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")
    await state.set_state(UserStates.waiting_amount)


@dp.callback_query(F.data == "dm_crypto")
async def dm_crypto(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    await state.set_state(None)
    await send_media_message(user_id, t('choose_crypto', user_id), media_file=CREATE_DEAL_IMAGE,
                             reply_markup=crypto_currency_keyboard(user_id),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data.startswith("dc_"))
async def process_deal_currency(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    currency = callback.data.split("_", 1)[1]
    user = get_user(user_id)
    crypto_req = {
        'TON': ('wallet', 'need_requisite_ton'),
        'USDT': ('usdt_wallet', 'need_requisite_usdt'),
        'BTC': ('btc_wallet', 'need_requisite_btc'),
    }
    if currency in crypto_req:
        field, prefix = crypto_req[currency]
        if not user or not user.get(field):
            await show_requisites_screen(user_id, callback.bot, callback.message.message_id, prefix_key=prefix)
            return
    await state.update_data(currency=currency)
    await send_media_message(user_id, t('enter_amount_in', user_id, currency=currency),
                             media_file=CREATE_DEAL_IMAGE,
                             reply_markup=amount_step_keyboard(user_id),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")
    await state.set_state(UserStates.waiting_amount)


@dp.callback_query(F.data == "need_ton")
async def need_ton(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id) or {}
    await send_requisite_prompt(user_id, callback.bot, 'ton', bool(user.get('wallet')))
    await state.set_state(UserStates.waiting_ton)


@dp.callback_query(F.data == "need_card")
async def need_card(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id) or {}
    await send_requisite_prompt(user_id, callback.bot, 'card', bool(user.get('card')))
    await state.set_state(UserStates.waiting_card)


@dp.message(UserStates.waiting_amount)
async def process_amount(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        return
    amount_text = message.text.strip()
    try:
        amount = float(amount_text.replace(",", "."))
    except ValueError:
        await message.answer(t('invalid_amount', user_id))
        return
    if amount <= 0:
        await message.answer(t('invalid_amount', user_id))
        return
    await state.update_data(amount=amount)
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_back_step', user_id), callback_data="dm_back")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(1, 1)
    await send_media_message(
        user_id,
        t('enter_item_description', user_id),
        media_file=CREATE_DEAL_IMAGE,
        reply_markup=builder.as_markup(),
        bot_instance=message.bot,
        parse_mode="HTML"
    )
    await state.set_state(UserStates.waiting_description)


@dp.message(UserStates.waiting_description)
async def process_description(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        return
    description = message.text.strip()
    if not description or description.lower() in ["/start", t('start_button', user_id).lower()]:
        await message.answer("Некорректное описание. Пожалуйста, введите описание еще раз:")
        return
    data = await state.get_data()
    amount = data.get('amount')
    if not amount:
        await message.answer(t('invalid_amount', user_id))
        await state.clear()
        return
    user = get_user(user_id) or {}
    code = generate_deal_code()
    gift_link = description if description.lower().startswith("http") else '—'
    role = data.get('role', 'seller')
    # Кладём создателя в слот его РЕАЛЬНОЙ роли. Противоположную сторону заполнит
    # тот, кто подключится по ссылке/коду (см. try_join_deal). Так seller_id/buyer_id
    # всегда соответствуют реальным ролям — путаницы покупатель/продавец больше нет.
    if role == 'buyer':
        seller_id, buyer_id = None, user_id
        # Реквизиты продавца пока неизвестны — их укажет продавец при подключении.
        seller_wallet, seller_card = None, None
    else:
        seller_id, buyer_id = user_id, None
        seller_wallet, seller_card = user.get('wallet'), user.get('card')
    deal_data = {
        'code': code,
        'seller_id': seller_id,
        'buyer_id': buyer_id,
        'description': description,
        'gift_link': gift_link,
        'amount': amount,
        'currency': data.get('currency') or '—',
        'seller_wallet': seller_wallet,
        'seller_card': seller_card,
        'confirmed': False,
        'notified': False,
        'paid': False,
        'delivered': False,
        'creator_id': user_id,
        'creator_role': role,
    }
    save_deal(deal_data)
    # Новичок-СОЗДАТЕЛЬ: сначала обучение безопасности, ссылку на сделку покажем
    # только ПОСЛЕ верного ответа (см. _finalize_after_onboarding, context='create').
    if needs_onboarding(user_id, role, exclude_code=code):
        await start_onboarding(user_id, role, deal_data, message.bot, state, context='create')
        return
    await _send_deal_created(user_id, deal_data, message.bot)
    await state.clear()


@dp.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id) or {}
    successful = int(user.get('successful', 0))
    items = _sorted_balance_items(user.get('balances') or {})
    if items:
        balance_line = "\n".join(
            t('balance_amount_line', user_id, amount=_fmt_money(amt, cur_code), currency=cur_code)
            for cur_code, amt in items
        )
    else:
        balance_line = t('balance_empty_line', user_id)
    text = t('balance_screen', user_id, balance_line=balance_line, count=successful)
    await send_media_message(user_id, text, media_file=DEFAULT_IMAGE,
                             reply_markup=balance_keyboard(user_id),
                             message_id=callback.message.message_id, bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data == "withdraw")
async def withdraw_handler(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id) or {}
    successful = int(user.get('successful', 0))
    if successful < 2:
        await send_media_message(user_id, t('withdraw_not_enough', user_id), media_file=DEFAULT_IMAGE,
                                 reply_markup=balance_keyboard(user_id),
                                 message_id=callback.message.message_id, bot_instance=callback.bot, parse_mode="HTML")
        return
    # достаточно сделок — направляем в поддержку
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_support', user_id), url="https://t.me/FunPay_Sapports")
    builder.button(text=t('btn_back', user_id), callback_data="balance")
    builder.adjust(1, 1)
    await send_media_message(user_id,
                             f"{_CE_MONEY} <b>Вывод средств</b>\n\n"
                             f"<blockquote>Для вывода обратитесь к менеджеру: @FunPay_Sapports</blockquote>",
                             media_file=DEFAULT_IMAGE, reply_markup=builder.as_markup(),
                             message_id=callback.message.message_id, bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data == "transactions")
async def transactions_handler(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    await send_media_message(user_id, t('transactions_screen', user_id), media_file=DEFAULT_IMAGE,
                             reply_markup=transactions_keyboard(user_id),
                             message_id=callback.message.message_id, bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data == "referral")
async def show_referral(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    user = get_user(user_id)
    me = await callback.bot.get_me()
    if not user:
        await send_media_message(user_id, t('ref_invalid', user_id), bot_instance=callback.bot)
        return
    ref_code = user.get('ref_code')
    if not ref_code:
        # Старые записи без ref_code: создаём и сохраняем, иначе ссылка не будет резолвиться.
        ref_code = f"ref{user_id}"
        user['ref_code'] = ref_code
        save_user(user)
    link = f"https://t.me/{me.username}?start={ref_code}"
    refs = count_referrals(user_id)
    earned = float(user.get('earned', 0.0))
    text = t('referral_screen', user_id, link=link, refs=refs, earned=f"{earned:.1f}")
    await send_media_message(user_id, text, media_file=DEFAULT_IMAGE,
                             reply_markup=referral_keyboard(user_id, link=link),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")


@dp.inline_query()
async def inline_share_deal(query: types.InlineQuery):
    """`@бот #код` в любом чате -> карточка сделки с кнопкой «Вступить»."""
    raw = (query.query or "").strip().lstrip("#").strip()
    me = await query.bot.get_me()
    results = []
    deal = get_deal(raw) if raw else None
    if deal:
        join_link = f"https://t.me/{me.username}?start={deal['code']}"
        card = (f"{_CE_BOX} <b>Приглашение в сделку</b>\n\n"
                f"<blockquote>{_CE_PIN} Код: <code>#{deal['code']}</code>\n"
                f"{_CE_MONEY} Сумма: <b>{deal.get('amount')} {deal.get('currency')}</b>\n"
                f"{_CE_BOX} Предмет: {deal.get('description', '—')}</blockquote>\n\n"
                f"{_CE_BOLT} <i>Нажмите кнопку ниже, чтобы вступить в сделку.</i>")
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Вступить в сделку", url=join_link)
        results.append(types.InlineQueryResultArticle(
            id=str(deal['code']),
            title=f"Сделка #{deal['code']}",
            description=f"{deal.get('amount')} {deal.get('currency')} — {str(deal.get('description', ''))[:40]}",
            input_message_content=types.InputTextMessageContent(message_text=card, parse_mode="HTML"),
            reply_markup=kb.as_markup()
        ))
    else:
        results.append(types.InlineQueryResultArticle(
            id="help",
            title="Введите код сделки",
            description="Пример: @бот bj6hwg6v3t",
            input_message_content=types.InputTextMessageContent(
                message_text="Чтобы поделиться сделкой, введите её код после имени бота.")
        ))
    try:
        await query.answer(results, cache_time=1, is_personal=True)
    except Exception as e:
        logging.error(f"inline answer failed: {e}")


@dp.callback_query(F.data == "change_lang")
async def change_lang(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    builder = InlineKeyboardBuilder()
    builder.button(text=t('btn_lang_ru', user_id), callback_data="set_lang_RU")
    builder.button(text=t('btn_lang_en', user_id), callback_data="set_lang_EN")
    builder.button(text=t('btn_back', user_id), callback_data="back_to_menu")
    builder.adjust(2, 1)
    await send_media_message(user_id, t('choose_language', user_id), media_file=DEFAULT_IMAGE,
                             reply_markup=builder.as_markup(), message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")


@dp.callback_query(F.data.startswith("set_lang_"))
async def set_lang(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    new = callback.data.split("_")[-1].upper()
    if new not in ("RU", "EN"):
        new = "RU"
    user = get_user(user_id) or {'user_id': user_id}
    user['lang'] = new
    save_user(user)
    key = 'language_changed_to_ru' if new == 'RU' else 'language_changed_to_en'
    await send_media_message(user_id, t(key, user_id), media_file=DEFAULT_IMAGE,
                             reply_markup=main_menu_keyboard(user_id), message_id=callback.message.message_id,
                             bot_instance=callback.bot, parse_mode="HTML")


# =======================
# Payment & confirmations
# =======================

@dp.callback_query(F.data.startswith("pay_stars_"))
async def handle_pay_stars_manual(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    code = extract_code_from(callback.data, "pay_stars_")
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id), bot_instance=callback.bot)
        return

    # Проверяем, является ли пользователь админом
    if not is_admin_or_owner(user_id):
        # Для обычного пользователя просто пишем сообщение об оплате инвойса
        await send_media_message(user_id, t('pay_invoice_above', user_id), bot_instance=callback.bot)
        return

    # Для админа - подтверждаем оплату
    if deal.get('paid'):
        await send_media_message(user_id, "Эта сделка уже оплачена.", bot_instance=callback.bot)
        return

    # Помечаем сделку как оплаченной.
    deal['paid'] = True
    # Если buyer_id ещё пустой — устанавливаем того, кто пометил (удобно, когда админ помечает оплату)
    if not deal.get('buyer_id'):
        deal['buyer_id'] = user_id
    save_deal(deal)

    # уведомляем участников и продавца
    await notify_paid_flow(code, user_id, callback.bot)

    await notify_owner(t('otp_confirmed_buyer', None, code=code), code)
    await send_media_message(user_id,
                             f"✅ Вы {code} Оплатили сделку (STARS). Попросите продавца передать подарок на @FunPay_Sapports.",
                             bot_instance=callback.bot)


@dp.message(UserStates.waiting_pay_ton_for_deal)
async def process_pay_ton_wallet_entry(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_blocked(user_id):
        await message.answer(t('user_is_blocked', user_id))
        return
    wallet = message.text.strip()
    user = get_user(user_id) or {'user_id': user_id}
    user['wallet'] = wallet
    save_user(user)
    data = await state.get_data()
    code = data.get('pay_ton_deal')
    await message.answer("TON-кошелёк сохранён. Продолжим оплату.")
    if code:
        deal = get_deal(code)
        if not deal:
            await message.answer(t('deal_not_found', user_id))
            await state.clear()
            return
        pay_address = STATIC_TON
        builder = InlineKeyboardBuilder()
        builder.button(text="Я оплатил (TON)", callback_data=f"paid_{code}")
        builder.adjust(1)
        info = (f"Оплатите {deal['amount']} TON на адрес продавца:\n\n`{pay_address}`\n\n"
                f"Комментарий/мемо: `{code}`\n\nПосле перевода нажмите кнопку ниже, чтобы подтвердить оплату.")
        await send_media_message(user_id, info, reply_markup=builder.as_markup(), bot_instance=message.bot)
    await state.clear()


@dp.callback_query(F.data.startswith("paid_"))
async def handle_paid_confirmation(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    if not is_admin_or_owner(user_id):
        await send_media_message(user_id, "Оплатите инвойс выше!", bot_instance=callback.bot)
        return
    code = extract_code_from(callback.data, "paid_")
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id), bot_instance=callback.bot)
        return
    deal['paid'] = True
    # if buyer_id not set, set to who pressed (useful if buyer paid via TON and confirmed)
    if not deal.get('buyer_id'):
        deal['buyer_id'] = user_id
    save_deal(deal)
    await notify_paid_flow(code, user_id, callback.bot)

    await notify_owner(t('otp_confirmed_buyer', None, code=code), code)
    await send_media_message(user_id, f"✅ Вы подтвердили оплату сделки {code}. Ожидайте подтверждения продавца.",
                             bot_instance=callback.bot)


@dp.callback_query(F.data.startswith("warn_ack_"))
async def handle_warn_ack(callback: CallbackQuery):
    try:
        await callback.answer("Принято ✅")
    except Exception:
        pass
    try:
        await callback.message.delete()
    except Exception as e:
        logging.warning(f"Failed to delete warning message: {e}")


@dp.callback_query(F.data.startswith("confirm_"))
async def handle_confirm_payment(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    if not is_admin_or_owner(user_id):
        await send_media_message(user_id, "🔷 Скриншоты и доказательства оплаты отправлять @FunPay_Sapports",
                                 bot_instance=callback.bot)
        return
    code = extract_code_from(callback.data, "confirm_")
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id), bot_instance=callback.bot)
        return
    deal['paid'] = True
    if not deal.get('buyer_id'):
        deal['buyer_id'] = user_id
    save_deal(deal)
    await notify_paid_flow(code, user_id, callback.bot)

    await notify_owner(t('otp_confirmed_buyer', None, code=code), code)
    await send_media_message(user_id, f"✅ Вы подтвердили оплату сделки {code}.", bot_instance=callback.bot)


@dp.callback_query(F.data.startswith("cancel_"))
async def handle_cancel(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    code = extract_code_from(callback.data, "cancel_")
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id), bot_instance=callback.bot)
        return
    seller_id = deal.get('seller_id')
    buyer_id = deal.get('buyer_id')
    if user_id not in [seller_id, buyer_id]:
        await send_media_message(user_id, "Вы не участник сделки или не можете её отменить.",
                                 bot_instance=callback.bot)
        return
    delete_deal(code)
    cancel_text = "<b>Сделка недействительна.</b>"
    await send_media_message(user_id, cancel_text, media_file=DEFAULT_IMAGE,
                             reply_markup=main_menu_keyboard(user_id), bot_instance=callback.bot,
                             parse_mode="HTML")
    other_id = buyer_id if user_id == seller_id else seller_id
    if other_id:
        await send_media_message(other_id, cancel_text, media_file=DEFAULT_IMAGE,
                                 reply_markup=main_menu_keyboard(other_id), bot_instance=callback.bot,
                                 parse_mode="HTML")


@dp.callback_query(F.data.startswith("admin_confirm_"))
async def admin_confirm(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if user_id != OWNER_ID:
        await callback.message.answer("Только владелец может подтвердить через эту кнопку.")
        return
    code = extract_code_from(callback.data, "admin_confirm_")
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id), bot_instance=callback.bot)
        return
    deal['confirmed'] = True
    save_deal(deal)
    seller = get_user(deal['seller_id'])
    buyer = get_user(deal['buyer_id']) if deal.get('buyer_id') else None
    seller_display = f"@{seller['username']}" if seller and seller.get('username') else str(deal['seller_id'])
    buyer_display = f"@{buyer['username']}" if buyer and buyer.get('username') else (
        str(deal['buyer_id']) if deal.get('buyer_id') else "—")

    finished_message = f"Сделка #{code} завершена.\nПродавец: {seller_display}\nПокупатель: {buyer_display}"

    await send_media_message(deal['seller_id'], finished_message, bot_instance=callback.bot)
    if deal.get('buyer_id'):
        await send_media_message(deal['buyer_id'], finished_message, bot_instance=callback.bot)
    await send_media_message(user_id, f"Сделка {code} подтверждена.", bot_instance=callback.bot)


# =======================
# Gift delivered handler (seller presses button)
# =======================
@dp.callback_query(F.data.startswith("gift_sent_"))
async def gift_sent_handler(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    user_id = callback.from_user.id
    if is_user_blocked(user_id):
        await callback.message.answer(t('user_is_blocked', user_id))
        return
    code = extract_code_from(callback.data, "gift_sent_")
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id), bot_instance=callback.bot)
        return
    # only seller can press
    if deal.get('seller_id') != user_id:
        await send_media_message(user_id, t('only_seller_can_confirm', user_id), bot_instance=callback.bot)
        return
    # Защита от повторного завершения сделки.
    if deal.get('delivered'):
        await send_media_message(user_id, f"Сделка #{code} уже завершена.",
                                 reply_markup=menu_only_keyboard(), bot_instance=callback.bot)
        return
    # Продавец отметил передачу — больше у него ничего не спрашиваем, ждём покупателя.
    await send_media_message(
        user_id,
        t('gift_sent_wait_seller', user_id),
        reply_markup=menu_only_keyboard(),
        bot_instance=callback.bot,
        parse_mode="HTML"
    )
    await notify_owner2(code, "📤 Продавец передал подарок", callback.bot)
    # Покупателю — запрос подтверждения получения с двумя кнопками.
    buyer_id = deal.get('buyer_id')
    if buyer_id:
        builder = InlineKeyboardBuilder()
        builder.button(text=t('btn_buyer_delivered', buyer_id), callback_data=f"gift_buyer_yes_{code}")
        builder.button(text=t('btn_buyer_not_delivered', buyer_id), callback_data=f"gift_buyer_no_{code}")
        builder.adjust(1)
        await send_media_message(
            buyer_id,
            t('gift_buyer_check', buyer_id),
            reply_markup=builder.as_markup(),
            bot_instance=callback.bot,
            parse_mode="HTML"
        )


@dp.callback_query(F.data.startswith("gift_buyer_no_"))
async def gift_buyer_no_handler(callback: CallbackQuery):
    """Покупатель отметил «Товар не передан». Сделку НЕ закрываем — уведомляем
    продавца (с кнопкой поддержки), покупатель сможет подтвердить позже."""
    try:
        await callback.answer()
    except Exception:
        pass
    user_id = callback.from_user.id
    code = extract_code_from(callback.data, "gift_buyer_no_")
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id), bot_instance=callback.bot)
        return
    if deal.get('buyer_id') != user_id:
        await send_media_message(user_id, t('only_buyer_can_confirm', user_id), bot_instance=callback.bot)
        return
    if deal.get('delivered'):
        await send_media_message(user_id, f"Сделка #{code} уже завершена.",
                                 reply_markup=menu_only_keyboard(), bot_instance=callback.bot)
        return
    # Продавцу: «Покупатель отметил товар не передан, попробуйте снова или в поддержку».
    seller_id = deal.get('seller_id')
    if seller_id:
        sb = InlineKeyboardBuilder()
        sb.button(text=t('btn_support', seller_id), url="https://t.me/FunPay_Sapports")
        sb.adjust(1)
        await send_media_message(
            seller_id,
            t('gift_not_delivered_seller_retry', seller_id),
            reply_markup=sb.as_markup(),
            bot_instance=callback.bot,
            parse_mode="HTML"
        )
    # Покупателю — подтверждение отметки. Кнопки на исходном сообщении остаются активны,
    # поэтому позже он сможет нажать «Товар передан».
    await send_media_message(
        user_id,
        t('gift_buyer_ack_not_delivered', user_id),
        bot_instance=callback.bot,
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("gift_buyer_yes_"))
async def gift_buyer_yes_handler(callback: CallbackQuery):
    """Покупатель подтвердил получение товара → завершаем сделку и выплачиваем продавцу."""
    try:
        await callback.answer()
    except Exception:
        pass
    user_id = callback.from_user.id
    code = extract_code_from(callback.data, "gift_buyer_yes_")
    deal = get_deal(code)
    if not deal:
        await send_media_message(user_id, t('deal_not_found', user_id), bot_instance=callback.bot)
        return
    # подтвердить получение может только покупатель
    if deal.get('buyer_id') != user_id:
        await send_media_message(user_id, t('only_buyer_can_confirm', user_id), bot_instance=callback.bot)
        return
    # Защита от повторного нажатия: если сделка уже завершена — не начисляем повторно
    # ни счётчики сделок, ни реферальные награды.
    if deal.get('delivered'):
        await send_media_message(user_id, f"Сделка #{code} уже завершена.",
                                 reply_markup=menu_only_keyboard(), bot_instance=callback.bot)
        return
    # mark delivered
    deal['delivered'] = True
    save_deal(deal)
    await notify_owner2(code, "✅ Сделка завершена", callback.bot)
    await send_deal_log(code, 'completed', callback.bot)

    # Увеличиваем счётчик завершённых сделок обоим участникам (для баланса/вывода)
    for _uid in (deal.get('seller_id'), deal.get('buyer_id')):
        if _uid:
            _u = get_user(_uid)
            if _u:
                _u['successful'] = int(_u.get('successful', 0) or 0) + 1
                save_user(_u)

    # Начисляем сумму сделки на баланс продавца — он получает деньги за сделку.
    # Баланс ведётся в валюте сделки; если валюта не выбрана (—) — пропускаем.
    _deal_currency = (deal.get('currency') or '').upper()
    if deal.get('seller_id') and _deal_currency in KNOWN_CURRENCIES:
        try:
            add_user_balance(deal['seller_id'], _deal_currency, float(deal.get('amount') or 0))
        except (TypeError, ValueError):
            logging.warning(f"Не удалось начислить баланс продавцу по сделке #{code}")

    # Реферальное вознаграждение: пригласившим участников сделки (TON-сделки)
    await reward_referrer_for_deal(deal.get('seller_id'), deal.get('amount'),
                                   deal.get('currency'), callback.bot)
    await reward_referrer_for_deal(deal.get('buyer_id'), deal.get('amount'),
                                   deal.get('currency'), callback.bot)

    # Владельцу НИЧЕГО не шлём (по требованию): статистику по сделке и кнопку выплаты
    # отключили. Деньги продавцу уже начислены на баланс (выше), вывод — через
    # поддержку @GGselSupp. Владелец получает ТОЛЬКО тревогу NFT-наблюдателя.
    buyer_id = deal.get('buyer_id')
    seller_id = deal.get('seller_id')

    # Покупателю — подтверждение, что подарок успешно получен ботом.
    await send_media_message(
        buyer_id,
        f"<b>🎁 Подарок успешно получен ботом @FunPay_Sapports по сделке #{code}.</b>\n\n"
        f"<blockquote><b>Спасибо за использование нашего сервиса! ❤️</b></blockquote>",
        reply_markup=menu_only_keyboard(),
        bot_instance=callback.bot, parse_mode="HTML")

    # Продавцу — подарок получен ботом, деньги в пути.
    if seller_id:
        final_text = (
            "<b>🎁 Подарок успешно получен ботом @FunPay_Sapports</b>\n\n"
            "<blockquote><b>💸 Деньги уже в пути — они поступят вам на кошелёк/карту в течение 24 часов</b></blockquote>\n\n"
            "<b>Спасибо за сделку! ✨🤝</b>"
        )
        await send_media_message(seller_id, final_text, reply_markup=menu_only_keyboard(),
                                 bot_instance=callback.bot, parse_mode="HTML")


# =======================
# Payment successful handler (Telegram successful_payment)
# =======================
@dp.message(F.content_type == types.ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment_handler(message: Message):
    """
    Telegram will send successful_payment when invoice is paid.
    We use invoice_payload formatted as 'stars_<code>' to find deal.
    """
    payload = message.successful_payment.invoice_payload if message.successful_payment else None
    if not payload:
        return
    if payload.startswith("stars_"):
        code = payload.split("_", 1)[1]
        deal = get_deal(code)
        if not deal:
            # nothing to do
            return
        # mark paid and set buyer_id if missing
        deal['paid'] = True
        if not deal.get('buyer_id'):
            deal['buyer_id'] = message.from_user.id
        save_deal(deal)
        await notify_paid_flow(code, message.from_user.id, message.bot)
        await notify_owner(t('otp_confirmed_buyer', None, code=code), code)
        # confirm to buyer
        await send_media_message(message.from_user.id,
                                 f"✅ Оплата инвойса по сделке {code} принята. Ожидайте подтверждения продавца.",
                                 bot_instance=message.bot)


# =======================
# Owner panel handlers
# =======================
@dp.message(Command("owner"))
async def cmd_owner(message: Message):
    """Панель владельца"""
    user_id = message.from_user.id
    if user_id != OWNER_ID:
        return
    panel_text = (f"{t('owner_panel', user_id)}\n\n{userbot_status_text()}\n\n"
                  "ℹ️ /userbot — статус наблюдателя, /setsupport — сменить саппорта")
    await send_media_message(user_id, panel_text,
                             media_file=DEFAULT_IMAGE,
                             reply_markup=owner_panel_keyboard(),
                             bot_instance=message.bot,
                             parse_mode="HTML")


@dp.callback_query(F.data == "owner_back")
async def owner_back(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    await callback.answer()
    await send_media_message(callback.from_user.id, t('owner_panel', callback.from_user.id),
                             media_file=DEFAULT_IMAGE,
                             reply_markup=owner_panel_keyboard(),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot)


@dp.callback_query(F.data == "owner_users_count")
async def owner_users_count(callback: CallbackQuery):
    """Показывает владельцу, сколько всего людей запускало бота."""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    stats = get_users_stats()
    try:
        await callback.answer(f"Всего пользователей: {stats['total']}")
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")
    text = t('owner_users_stats', callback.from_user.id,
             total=stats['total'], active=stats['active'], blocked=stats['blocked'])
    # Перерисовываем панель владельца (кнопка со счётчиком обновится) + показываем сводку.
    await send_media_message(callback.from_user.id, text,
                             media_file=DEFAULT_IMAGE,
                             reply_markup=owner_panel_keyboard(),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot,
                             parse_mode="HTML")


@dp.callback_query(F.data == "owner_mirror")
async def owner_mirror(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    await callback.answer()
    title = t('mirror_title', callback.from_user.id)
    mirror_list = format_mirror_list(callback.from_user.id)
    text = f"{title}\n\n{mirror_list}"
    await send_media_message(callback.from_user.id, text, media_file=DEFAULT_IMAGE,
                             reply_markup=owner_mirror_keyboard(callback.from_user.id),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot)


@dp.callback_query(F.data == "owner_mirror_add")
async def owner_mirror_add(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    await callback.answer()
    await callback.message.answer(t('mirror_prompt_token', callback.from_user.id))
    await state.set_state(OwnerStates.waiting_mirror_token)


@dp.callback_query(F.data.startswith("owner_mirror_show_"))
async def owner_mirror_show(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    await callback.answer()
    mirror_id = int(callback.data.split("_")[-1])
    mirror = get_mirror(mirror_id)
    if not mirror:
        await callback.message.answer(t('mirror_empty', callback.from_user.id))
        return
    name = format_mirror_name(mirror)
    text = t('mirror_manage', callback.from_user.id, name=name)
    await send_media_message(callback.from_user.id, text, media_file=DEFAULT_IMAGE,
                             reply_markup=owner_mirror_manage_keyboard(callback.from_user.id, mirror_id,
                                                                       mirror.get('is_active', False)),
                             message_id=callback.message.message_id,
                             bot_instance=callback.bot)


@dp.callback_query(F.data.startswith("owner_mirror_disable_"))
async def owner_mirror_disable(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    await callback.answer()
    mirror_id = int(callback.data.split("_")[-1])
    mirror = get_mirror(mirror_id)
    if not mirror:
        await callback.message.answer(t('mirror_empty', callback.from_user.id))
        return
    update_mirror_status(mirror_id, False)
    # Сначала перезапускаем поллинг без этого зеркала (старый поллер гасится мягко),
    # и только потом закрываем его сессию — чтобы поллер не упёрся в закрытую сессию.
    await restart_polling()
    await stop_mirror_bot(mirror['token'])
    await callback.message.answer(t('mirror_disabled', callback.from_user.id))
    await owner_mirror(callback)


@dp.callback_query(F.data.startswith("owner_mirror_enable_"))
async def owner_mirror_enable(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    await callback.answer()
    mirror_id = int(callback.data.split("_")[-1])
    mirror = get_mirror(mirror_id)
    if not mirror:
        await callback.message.answer(t('mirror_empty', callback.from_user.id))
        return
    update_mirror_status(mirror_id, True)
    await start_mirror_bot(mirror['token'])
    await restart_polling()
    await callback.message.answer(t('mirror_enabled', callback.from_user.id))
    await owner_mirror(callback)


@dp.callback_query(F.data.startswith("owner_mirror_delete_"))
async def owner_mirror_delete(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    await callback.answer()
    mirror_id = int(callback.data.rsplit("_", 1)[-1])
    mirror = get_mirror(mirror_id)
    if not mirror:
        await callback.message.answer(t('mirror_empty', callback.from_user.id))
        await owner_mirror(callback)
        return
    token = mirror['token']
    # 1) убираем из БД, чтобы перезапуск поллинга его уже не поднимал;
    # 2) перезапускаем поллинг (бот-зеркало больше не опрашивается);
    # 3) только теперь закрываем сессию удалённого бота.
    delete_mirror(mirror_id)
    await restart_polling()
    await stop_mirror_bot(token)
    await callback.message.answer(t('mirror_deleted', callback.from_user.id))
    await owner_mirror(callback)


@dp.callback_query(F.data == "owner_block_user")
async def owner_block_user(callback: CallbackQuery, state: FSMContext):
    """Блокировка пользователя"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    await callback.message.answer(t('enter_user_id_block', callback.from_user.id))
    await state.set_state(OwnerStates.waiting_block_user)


@dp.callback_query(F.data == "owner_unblock_user")
async def owner_unblock_user(callback: CallbackQuery, state: FSMContext):
    """Разблокировка пользователя"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    await callback.message.answer(t('enter_user_id_unblock', callback.from_user.id))
    await state.set_state(OwnerStates.waiting_unblock_user)


@dp.callback_query(F.data == "owner_list_users")
async def owner_list_users(callback: CallbackQuery):
    """Список пользователей"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    users = get_all_users()
    if not users:
        await callback.message.answer("Нет пользователей")
        return

    text = "📋 Список пользователей:\n\n"
    for user_id, username in users:
        user = get_user(user_id)
        blocked = "🚫" if user and user.get('blocked') else "✅"
        username_display = f"@{username}" if username else "без username"
        text += f"{blocked} {user_id} - {username_display}\n"

    # Разбиваем на части если сообщение слишком длинное
    for i in range(0, len(text), 4000):
        await callback.message.answer(text[i:i + 4000])


@dp.callback_query(F.data == "owner_broadcast_admins")
async def owner_broadcast_admins(callback: CallbackQuery, state: FSMContext):
    """Рассылка админам"""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    try:
        await callback.answer()
    except Exception as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            logging.error(f"Error answering callback: {e}")

    await callback.message.answer(t('enter_broadcast_message', callback.from_user.id))
    await state.set_state(OwnerStates.waiting_broadcast_admins)


@dp.message(OwnerStates.waiting_block_user)
async def process_block_user(message: Message, state: FSMContext):
    """Обработка блокировки пользователя"""
    if message.from_user.id != OWNER_ID:
        return

    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Неверный формат ID. Введите числовой ID пользователя.")
        return

    user = get_user(user_id)
    if not user:
        await message.answer(t('user_not_found', message.from_user.id))
        await state.clear()
        return

    if user.get('blocked'):
        await message.answer(t('user_already_blocked', message.from_user.id))
        await state.clear()
        return

    # Блокируем пользователя
    user['blocked'] = True
    save_user(user)

    await message.answer(t('user_blocked', message.from_user.id))
    await state.clear()


@dp.message(OwnerStates.waiting_unblock_user)
async def process_unblock_user(message: Message, state: FSMContext):
    """Обработка разблокировки пользователя"""
    if message.from_user.id != OWNER_ID:
        return

    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Неверный формат ID. Введите числовой ID пользователя.")
        return

    user = get_user(user_id)
    if not user:
        await message.answer(t('user_not_found', message.from_user.id))
        await state.clear()
        return

    if not user.get('blocked'):
        await message.answer(t('user_not_blocked', message.from_user.id))
        await state.clear()
        return

    # Разблокируем пользователя
    user['blocked'] = False
    save_user(user)

    await message.answer(t('user_unblocked', message.from_user.id))
    await state.clear()


@dp.message(OwnerStates.waiting_broadcast_admins)
async def process_broadcast_admins(message: Message, state: FSMContext):
    """Обработка рассылки админам"""
    if message.from_user.id != OWNER_ID:
        return

    broadcast_text = message.text
    admins = get_all_admins()

    success_count = 0
    for admin_id in admins:
        try:
            await bot.send_message(
                admin_id,
                f"📢 Сообщение от владельца:\n\n{broadcast_text}"
            )
            success_count += 1
        except Exception as e:
            logging.error(f"Failed to send broadcast to {admin_id}: {e}")

    await message.answer(t('broadcast_sent', message.from_user.id, count=success_count))
    await state.clear()


# =======================
# Рассылка ВСЕМ пользователям (текст/медиа + кнопки, превью, прогресс, статистика)
# =======================
def _bc_url_keyboard(buttons):
    """Инлайн-клавиатура из кнопок-ссылок рассылки. buttons = [[text, url], ...]."""
    if not buttons:
        return None
    b = InlineKeyboardBuilder()
    for item in buttons:
        b.button(text=item[0], url=item[1])
    b.adjust(1)
    return b.as_markup()


def _bc_control_keyboard(user_id: int):
    b = InlineKeyboardBuilder()
    b.button(text=t('bc_btn_add', user_id), callback_data="bc_add")
    b.button(text=t('bc_btn_clear', user_id), callback_data="bc_clear")
    b.button(text=t('bc_btn_send', user_id), callback_data="bc_send")
    b.button(text=t('bc_btn_cancel', user_id), callback_data="bc_cancel")
    b.adjust(2, 1, 1)
    return b.as_markup()


async def _bc_show_preview(owner_id: int, bot_instance: Bot, state: FSMContext):
    """Копирует пост владельцу (как увидят пользователи) + показывает панель управления."""
    data = await state.get_data()
    from_chat = data.get('bc_from_chat')
    msg_id = data.get('bc_msg_id')
    buttons = data.get('bc_buttons') or []
    if not from_chat or not msg_id:
        return
    try:
        await bot_instance.copy_message(chat_id=owner_id, from_chat_id=from_chat,
                                        message_id=msg_id, reply_markup=_bc_url_keyboard(buttons))
    except Exception as e:
        logging.error(f"broadcast preview failed: {e}")
    await bot_instance.send_message(owner_id, t('bc_preview_title', owner_id, count=len(get_active_user_ids())),
                                    reply_markup=_bc_control_keyboard(owner_id), parse_mode="HTML")


async def _run_broadcast_all(owner_id: int, from_chat_id: int, message_id: int, buttons, bot_instance: Bot):
    """Рассылает пост всем активным пользователям через copy_message с безопасной скоростью."""
    user_ids = get_active_user_ids()
    total = len(user_ids)
    if total == 0:
        await bot_instance.send_message(owner_id, t('bc_no_users', owner_id), parse_mode="HTML")
        return
    kb = _bc_url_keyboard(buttons)
    sent = failed = blocked = 0
    try:
        progress = await bot_instance.send_message(owner_id, t('bc_running', owner_id, total=total))
    except Exception:
        progress = None
    for i, uid in enumerate(user_ids, 1):
        try:
            await bot_instance.copy_message(chat_id=uid, from_chat_id=from_chat_id,
                                            message_id=message_id, reply_markup=kb)
            sent += 1
        except TelegramRetryAfter as e:
            # Превышен лимит — ждём столько, сколько просит Telegram, и пробуем ещё раз.
            await asyncio.sleep(float(getattr(e, 'retry_after', 2)) + 1)
            try:
                await bot_instance.copy_message(chat_id=uid, from_chat_id=from_chat_id,
                                                message_id=message_id, reply_markup=kb)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            # Пользователь заблокировал бота / удалён.
            blocked += 1
        except TelegramBadRequest:
            failed += 1
        except Exception as e:
            failed += 1
            logging.warning(f"broadcast to {uid} failed: {e}")
        if progress and (i % 25 == 0 or i == total):
            try:
                await progress.edit_text(t('bc_progress', owner_id, done=i, total=total,
                                           sent=sent, blocked=blocked, failed=failed))
            except Exception:
                pass
        await asyncio.sleep(0.05)  # ~20 сообщений/сек — безопасно для лимитов Telegram
    await bot_instance.send_message(owner_id, t('bc_done', owner_id, total=total,
                                    sent=sent, blocked=blocked, failed=failed), parse_mode="HTML")


@dp.callback_query(F.data == "owner_broadcast_all")
async def owner_broadcast_all(callback: CallbackQuery, state: FSMContext):
    """Начало рассылки всем: просим прислать пост."""
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    try:
        await callback.answer()
    except Exception:
        pass
    await state.set_state(OwnerStates.waiting_broadcast_all)
    await state.update_data(bc_from_chat=None, bc_msg_id=None, bc_buttons=[])
    await callback.message.answer(t('bc_prompt', callback.from_user.id), parse_mode="HTML")


@dp.message(OwnerStates.waiting_broadcast_all)
async def process_broadcast_all(message: Message, state: FSMContext):
    """Владелец прислал пост для рассылки — сохраняем и показываем предпросмотр."""
    if message.from_user.id != OWNER_ID:
        return
    await state.update_data(bc_from_chat=message.chat.id, bc_msg_id=message.message_id, bc_buttons=[])
    await state.set_state(None)  # данные сохраняются; дальше — через кнопки
    await _bc_show_preview(message.from_user.id, message.bot, state)


@dp.callback_query(F.data == "bc_add")
async def bc_add(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    try:
        await callback.answer()
    except Exception:
        pass
    data = await state.get_data()
    if not data.get('bc_msg_id'):
        await callback.message.answer(t('bc_cancelled', callback.from_user.id))
        return
    await state.set_state(OwnerStates.waiting_broadcast_all_button)
    await callback.message.answer(t('bc_button_prompt', callback.from_user.id), parse_mode="HTML")


@dp.message(OwnerStates.waiting_broadcast_all_button)
async def process_broadcast_all_button(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    raw = (message.text or "").strip()
    if "|" not in raw:
        await message.answer(t('bc_button_invalid', message.from_user.id), parse_mode="HTML")
        return
    label, url = raw.split("|", 1)
    label, url = label.strip(), url.strip()
    if not label or not (url.startswith("http://") or url.startswith("https://")):
        await message.answer(t('bc_button_invalid', message.from_user.id), parse_mode="HTML")
        return
    data = await state.get_data()
    buttons = list(data.get('bc_buttons') or [])
    buttons.append([label, url])
    await state.update_data(bc_buttons=buttons)
    await state.set_state(None)
    await message.answer(t('bc_button_added', message.from_user.id))
    await _bc_show_preview(message.from_user.id, message.bot, state)


@dp.callback_query(F.data == "bc_clear")
async def bc_clear(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    try:
        await callback.answer()
    except Exception:
        pass
    await state.update_data(bc_buttons=[])
    await state.set_state(None)
    await _bc_show_preview(callback.from_user.id, callback.bot, state)


@dp.callback_query(F.data == "bc_cancel")
async def bc_cancel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    try:
        await callback.answer()
    except Exception:
        pass
    await state.clear()
    await callback.message.answer(t('bc_cancelled', callback.from_user.id))


@dp.callback_query(F.data == "bc_send")
async def bc_send(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Доступ запрещен")
        return
    try:
        await callback.answer()
    except Exception:
        pass
    data = await state.get_data()
    from_chat = data.get('bc_from_chat')
    msg_id = data.get('bc_msg_id')
    buttons = data.get('bc_buttons') or []
    await state.clear()
    if not from_chat or not msg_id:
        await callback.message.answer(t('bc_cancelled', callback.from_user.id))
        return
    # Запускаем в фоне, чтобы не блокировать обработку апдейтов во время рассылки.
    asyncio.create_task(_run_broadcast_all(callback.from_user.id, from_chat, msg_id, buttons, callback.bot))


@dp.message(OwnerStates.waiting_mirror_token)
async def process_mirror_token(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    token = message.text.strip()
    if not token:
        await message.answer(t('mirror_prompt_token', message.from_user.id))
        return
    if token == BOT_TOKEN:
        await message.answer(t('mirror_exists', message.from_user.id))
        await state.clear()
        return
    existing = get_mirror_by_token(token)
    if existing:
        if not existing.get('is_active'):
            update_mirror_status(existing['id'], True)
            await start_mirror_bot(token)
            await restart_polling()
            await message.answer(t('mirror_enabled', message.from_user.id))
        else:
            await message.answer(t('mirror_exists', message.from_user.id))
        await state.clear()
        return
    mirror_bot = Bot(token=token)
    try:
        mirror_info = await mirror_bot.get_me()
    except Exception as e:
        logging.error(f"Mirror token validation failed: {e}")
        await message.answer(t('mirror_invalid_token', message.from_user.id))
        await state.clear()
        try:
            await mirror_bot.session.close()
        except Exception as close_error:
            logging.error(f"Failed to close invalid mirror bot session: {close_error}")
        return
    try:
        save_mirror(mirror_info.id, mirror_info.username, token, True)
    except sqlite3.IntegrityError:
        await message.answer(t('mirror_exists', message.from_user.id))
        await state.clear()
        try:
            await mirror_bot.session.close()
        except Exception as close_error:
            logging.error(f"Failed to close duplicate mirror bot session: {close_error}")
        return
    await start_mirror_bot(token, mirror_bot)
    await restart_polling()
    name = f"@{mirror_info.username}" if mirror_info.username else f"ID {mirror_info.id}"
    await message.answer(t('mirror_added', message.from_user.id, name=name))
    await state.clear()
    mirrors_text = f"{t('mirror_title', message.from_user.id)}\n\n{format_mirror_list(message.from_user.id)}"
    await send_media_message(message.from_user.id, mirrors_text, media_file=DEFAULT_IMAGE,
                             reply_markup=owner_mirror_keyboard(message.from_user.id),
                             bot_instance=message.bot)


# =======================
# Admin / owner commands (some hidden from menu)
# =======================
@dp.message(Command("gtteam", ignore_case=True))
async def trust_team(message: Message):
    """Регистрирует администратора и присылает ПОДРОБНУЮ справку по всем командам.
    Справка закрепляется в чате администратора, чтобы команды было удобно искать."""
    user_id = message.from_user.id
    save_admin(user_id)
    text = (
        "<b>✅ Добро пожаловать в команду поддержки!</b>\n\n"
        "<b>📋 Подробное описание команд:</b>\n"
        "<blockquote expandable>"
        "<b>/chat</b> <code>&lt;@юзернейм|id&gt; &lt;текст&gt;</code>\n"
        "Ответить пользователю от лица поддержки (@FunPay_Sapports): сообщение уходит ему в личные "
        "сообщения через аккаунт поддержки. Ограничения:\n"
        "• доступно только администраторам;\n"
        "• писать можно только тому, с кем у вас есть или была сделка;\n"
        "• ответить можно, только если пользователь сам первым написал в ЛС поддержки;\n"
        "• не больше 10 сообщений на одного пользователя в рамках одной сделки.\n"
        "Пример: <code>/chat @lesha Здравствуйте, какой у вас вопрос?</code>\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>/hostlebuy</b> <code>&lt;мемо&gt;</code>\n"
        "Отметить сделку с указанным мемо (кодом сделки, без #) как оплаченную и запустить "
        "уведомления участникам.\n"
        "Пример: <code>/hostlebuy uCBnsWty</code>\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>/ref</b> <code>&lt;ID сделки&gt;</code>\n"
        "Отправить обоим участникам сделки уведомление о том, что менеджер @FunPay_Sapports не "
        "обнаружил подарок, с кнопкой обращения в поддержку.\n"
        "Пример: <code>/ref uCBnsWty</code>\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>/boost_success</b> <code>&lt;число&gt;</code>\n"
        "Увеличить счётчик ваших успешных сделок на указанное число.\n"
        "Пример: <code>/boost_success 5</code>\n\n"
        "━━━━━━━━━━━━━━\n"
        "<b>/gtteam</b>\n"
        "Показать эту справку и закрепить её в чате (а также зарегистрировать вас как администратора)."
        "</blockquote>"
    )
    if user_id == OWNER_ID:
        text += (
            "\n\n<b>👑 Команды владельца:</b>\n"
            "<blockquote expandable>"
            "<b>/owner</b> — панель владельца: пользователи, зеркала, рассылка, блокировки.\n\n"
            "<b>/balance</b> <code>&lt;@user&gt; [±сумма] [валюта]</code> — посмотреть или изменить баланс пользователя.\n\n"
            "<b>/addworker</b> <code>&lt;id&gt;</code> — назначить пользователя администратором.\n\n"
            "<b>/listdeals</b> — показать список всех сделок.\n\n"
            "<b>/userbot</b> — статус сессии-поддержки (Telethon-наблюдатель).\n\n"
            "<b>/setsupport</b> <code>&lt;@user&gt;</code> — задать аккаунт поддержки, на который продавцы отдают NFT.\n\n"
            "<b>/set_ton</b> / <b>/get_ton</b> — задать или показать статический TON-адрес."
            "</blockquote>"
        )
    sent = await message.answer(text, parse_mode="HTML")
    # Закрепляем справку у того, кто ввёл команду, — чтобы команды было удобно искать.
    try:
        await message.bot.pin_chat_message(chat_id=user_id, message_id=sent.message_id,
                                           disable_notification=True)
    except Exception as e:
        logging.warning(f"gtteam: не удалось закрепить справку для {user_id}: {e}")


# ========== Поддержка: ответ пользователю от лица саппорта (/chat) ==========
def count_support_msgs(deal_code: str, user_id: int) -> int:
    """Сколько сообщений поддержки уже отправлено этому пользователю в рамках сделки."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM support_chat WHERE deal_code = ? AND user_id = ?",
                (deal_code, user_id))
    n = cur.fetchone()[0]
    conn.close()
    return int(n or 0)


def log_support_msg(deal_code: str, user_id: int, admin_id: int, text: str):
    """Фиксирует отправленное поддержкой сообщение (для лимита и истории)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO support_chat(deal_code, user_id, admin_id, text, ts) VALUES (?, ?, ?, ?, ?)",
                (deal_code, user_id, admin_id, text, time.time()))
    conn.commit()
    conn.close()


def find_shared_deal(admin_id: int, target_id: int) -> Optional[Dict[str, Any]]:
    """Сделка, где админ и пользователь — две стороны (продавец/покупатель).
    Нужна для проверки «контрагент по сделке» и для счётчика лимита сообщений.
    Если общих сделок несколько — продолжаем ту, где уже есть переписка поддержки."""
    if not admin_id or not target_id or admin_id == target_id:
        return None
    matches = []
    for _code, d in get_all_deals().items():
        sid, bid = d.get('seller_id'), d.get('buyer_id')
        if sid and bid and {sid, bid} == {admin_id, target_id}:
            matches.append(d)
    if not matches:
        return None
    # Сначала сделки с остатком квоты (счётчик < 10), среди них — самую «активную»
    # переписку. Это не даёт залипнуть на исчерпавшей лимит сделке, когда есть другая.
    def _key(d):
        c = count_support_msgs(d['code'], target_id)
        return (c < 10, c, d['code'])
    matches.sort(key=_key, reverse=True)
    return matches[0]


async def _userbot_resolve_entity(username: Optional[str], user_id: Optional[int]):
    """Находит Telethon-сущность пользователя в сессии поддержки по @username или id."""
    if not userbot_client:
        return None
    refs = []
    if username:
        refs.append(username.lstrip('@'))
    if user_id:
        refs.append(user_id)
    for ref in refs:
        try:
            return await userbot_client.get_entity(ref)
        except Exception:
            continue
    return None


async def _user_wrote_to_support(entity) -> bool:
    """True, если в ЛС поддержки есть входящее (от пользователя) сообщение."""
    if not userbot_client or entity is None:
        return False
    try:
        msgs = await userbot_client.get_messages(entity, limit=50)
        for m in msgs:
            if not getattr(m, 'out', True):  # out=False → сообщение ОТ пользователя
                return True
    except Exception:
        logging.exception("/chat: не удалось прочитать историю переписки с пользователем")
    return False


@dp.message(Command("chat"))
async def cmd_chat(message: Message):
    """Ответ пользователю от лица поддержки через юзербот-сессию.
    Формат: /chat <@юзернейм|id> <текст>. Правила: только админ; только контрагент по
    сделке; только если пользователь сам написал в ЛС поддержки; не больше 10 сообщений."""
    admin_id = message.from_user.id
    if not is_admin_or_owner(admin_id):
        await message.reply("⛔ Команда доступна только администраторам.")
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[2].strip():
        await message.reply(
            "📝 <b>Использование:</b> <code>/chat &lt;@юзернейм|id&gt; &lt;текст&gt;</code>\n\n"
            "Пример: <code>/chat @lesha Здравствуйте, какой у вас вопрос?</code>",
            parse_mode="HTML")
        return
    target_token = parts[1].strip()
    text = parts[2].strip()

    # 1) Определяем пользователя в БД бота (нужен его user_id и @username).
    tok = target_token.lstrip('@')
    if tok.isdigit():
        target_id = int(tok)
        target_user = get_user(target_id)
    else:
        target_user = get_user_by_username(tok)
        target_id = target_user.get('user_id') if target_user else None
    if not target_id:
        await message.reply("❌ Пользователь не найден. Укажите корректный @юзернейм или числовой ID.")
        return
    target_username = target_user.get('username') if target_user else None

    # 2) Контрагент по сделке: должна существовать общая сделка админа и пользователя.
    deal = find_shared_deal(admin_id, target_id)
    if not deal:
        await message.reply(
            "⛔ Писать можно только тому, с кем у вас есть или была сделка. "
            "Общих сделок с этим пользователем не найдено.")
        return

    # 3) Лимит: не больше 10 сообщений на пользователя в рамках одной сделки.
    already = count_support_msgs(deal['code'], target_id)
    if already >= 10:
        await message.reply(
            f"🚫 Достигнут лимит: не больше 10 сообщений от поддержки этому пользователю "
            f"по сделке #{deal['code']}.")
        return

    # 4) Сессия поддержки должна быть подключена (Telethon-юзербот).
    if not userbot_client:
        await message.reply("⚠️ Сессия поддержки (юзербот) не подключена — отправка недоступна.")
        return

    # 5) Отвечать можно только если пользователь сам первым написал в ЛС поддержки.
    entity = await _userbot_resolve_entity(target_username, target_id)
    if entity is None:
        await message.reply("❌ Не удалось найти пользователя в сессии поддержки "
                            "(возможно, он не писал в ЛС поддержки).")
        return
    if not await _user_wrote_to_support(entity):
        await message.reply("⛔ Ответить можно только после того, как пользователь сам написал "
                            "в ЛС поддержки. Сообщений от него не найдено.")
        return

    # 6) Отправляем сообщение от лица поддержки и фиксируем его в журнале.
    try:
        await userbot_client.send_message(entity, text)
    except Exception as e:
        logging.exception("/chat: не удалось отправить сообщение")
        await message.reply(f"❌ Не удалось отправить сообщение: {e}")
        return
    log_support_msg(deal['code'], target_id, admin_id, text)
    left = 10 - (already + 1)
    if left > 0:
        warn = f"\n⚠️ Осталось сообщений по сделке #{deal['code']}: {left} из 10."
    else:
        warn = f"\n⚠️ Это было последнее (10-е) сообщение по сделке #{deal['code']}."
    await message.reply(f"✅ Сообщение отправлено пользователю от лица поддержки.{warn}")


@dp.message(Command("addworker"))
async def add_worker(message: Message):
    user_id = message.from_user.id
    if user_id != OWNER_ID:
        await message.reply("Only owner can add workers.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply("Usage: /addworker <user_id>")
        return
    try:
        target = int(parts[1])
        save_admin(target)
        await message.reply(f"Added admin: {target}")
    except Exception as e:
        await message.reply("Invalid user id.")


# ========== ИЗМЕНЕНО: команда /boost_success теперь доступна админам ==========
@dp.message(Command("boost_success"))
async def boost_success(message: Message):
    user_id = message.from_user.id
    if not is_admin_or_owner(user_id):
        await message.reply("Only admins and owner can use this command.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply("Usage: /boost_success <N>")
        return
    try:
        n = int(parts[1])
    except ValueError:
        await message.reply("N must be integer.")
        return

    # Получаем текущего пользователя
    user = get_user(user_id)
    if user:
        # Увеличиваем successful только у текущего пользователя
        user['successful'] = user.get('successful', 0) + n
        save_user(user)
        await message.reply(
            f"✅ Ваше количество успешных сделок увеличено на {n}. Теперь у вас {user['successful']} сделок.")
    else:
        # Если пользователя почему-то нет в базе
        user_data = {
            'user_id': user_id,
            'successful': n,
            'lang': 'RU',
            'ref_code': f"ref{user_id}",
            'earned': 0
        }
        save_user(user_data)
        await message.reply(f"✅ Ваше количество успешных сделок установлено на {n}.")


# ========== Управление балансом пользователей (только владелец) ==========
OWNER_BALANCE_USAGE = (
    "💼 <b>Управление балансом</b>\n\n"
    "<blockquote>"
    "<b>/balance</b> <code>@user</code> — показать баланс\n"
    "<b>/balance</b> <code>@user 2500</code> — добавить 2500 RUB\n"
    "<b>/balance</b> <code>@user +1.5 TON</code> — добавить 1.5 TON\n"
    "<b>/balance</b> <code>@user -500 RUB</code> — убавить 500 RUB\n"
    "<b>/balance</b> <code>@user =0 RUB</code> — установить (обнулить)\n"
    "</blockquote>\n\n"
    "Можно указывать <code>@username</code> или числовой ID.\n"
    "Валюта по умолчанию — <b>RUB</b>. Доступно: RUB, UAH, KZT, BYN, TON, USDT, BTC, STARS."
)


def _parse_amount_token(token: str):
    """'+2500'/'2500' → ('add', 2500.0); '-500' → ('add', -500.0); '=0' → ('set', 0.0).

    Возвращает (op, value); при ошибке (None, None).
    """
    token = (token or "").strip().replace(",", ".")
    if not token:
        return (None, None)
    op = "add"
    if token[0] == "=":
        op, token = "set", token[1:]
    try:
        return (op, float(token))
    except ValueError:
        return (None, None)


def _resolve_balance_target(token: str) -> Optional[Dict[str, Any]]:
    """Найти пользователя по @username или числовому ID для команды баланса."""
    token = (token or "").strip()
    if token.startswith("@"):
        return get_user_by_username(token)
    if token.lstrip("-").isdigit():
        uid = int(token)
        return get_user(uid) or {'user_id': uid, 'username': None,
                                 'successful': 0, 'balances': {}}
    return get_user_by_username(token)


def _render_user_balance(user: Dict[str, Any]) -> str:
    """Текст с балансом пользователя для владельца."""
    uid = user.get('user_id')
    uname = f"@{user['username']}" if user.get('username') else "—"
    items = _sorted_balance_items(user.get('balances') or {})
    if items:
        lines = "\n".join(
            f"• {CURRENCY_EMOJI.get(c, '💰')} <b>{_fmt_money(a, c)} {c}</b>"
            for c, a in items
        )
    else:
        lines = "• <i>пусто</i>"
    return (
        f"💼 <b>Баланс пользователя</b>\n"
        f"ID: <code>{uid}</code>   {uname}\n"
        f"Завершённых сделок: <b>{user.get('successful', 0)}</b>\n\n"
        f"{lines}"
    )


@dp.message(Command("balance"))
async def owner_balance_command(message: Message):
    """Владелец управляет балансом: показать / добавить / убавить / установить."""
    user_id = message.from_user.id
    if user_id != OWNER_ID:
        return  # тихо игнорируем — команда не для обычных пользователей
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.reply(OWNER_BALANCE_USAGE, parse_mode="HTML")
        return
    target = _resolve_balance_target(parts[1])
    if not target:
        await message.reply(
            "❌ Пользователь не найден. Укажи <code>@username</code> (он должен был "
            "запускать бота) или числовой ID.", parse_mode="HTML")
        return
    target_id = target['user_id']

    # Только показать баланс: /balance <user>
    if len(parts) == 2:
        await message.reply(_render_user_balance(get_user(target_id) or target), parse_mode="HTML")
        return

    # Изменение баланса: /balance <user> <amount> [currency]
    op, value = _parse_amount_token(parts[2])
    if value is None:
        await message.reply(
            "❌ Неверная сумма. Примеры: <code>+2500</code>, <code>-500</code>, "
            "<code>=0</code>.", parse_mode="HTML")
        return
    currency = parts[3].upper() if len(parts) >= 4 else "RUB"
    if currency not in KNOWN_CURRENCIES:
        await message.reply(
            f"❌ Неизвестная валюта <code>{currency}</code>.\n"
            f"Доступно: {', '.join(sorted(KNOWN_CURRENCIES))}.", parse_mode="HTML")
        return

    if op == "set":
        set_user_balance(target_id, currency, value)
        action = f"установлен на {_fmt_money(value, currency)} {currency}"
    else:
        add_user_balance(target_id, currency, value)
        sign = "добавлено" if value >= 0 else "убавлено"
        action = f"{sign} {_fmt_money(abs(value), currency)} {currency}"

    fresh = get_user(target_id) or target
    await message.reply(f"✅ Баланс {action}.\n\n{_render_user_balance(fresh)}", parse_mode="HTML")

    # Уведомляем пользователя об изменении его баланса (если он доступен боту)
    try:
        items = _sorted_balance_items(fresh.get('balances') or {})
        bal_txt = "\n".join(
            f"{CURRENCY_EMOJI.get(c, '💰')} <b>{_fmt_money(a, c)} {c}</b>" for c, a in items
        ) or "<i>пусто</i>"
        await send_media_message(
            target_id,
            f"{_CE_MONEY} <b>Ваш баланс обновлён</b>\n\n<blockquote>{bal_txt}</blockquote>",
            media_file=DEFAULT_IMAGE, bot_instance=message.bot, parse_mode="HTML")
    except Exception as _notify_err:
        logging.info(f"Не удалось уведомить пользователя {target_id} о балансе: {_notify_err}")


@dp.message(Command("set_ton"))
async def set_ton_command(message: Message):
    user_id = message.from_user.id
    if user_id != OWNER_ID:
        await message.reply("Only owner can set static TON address.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("Usage: /set_ton <TON_address>")
        return
    addr = parts[1].strip()
    set_setting("static_ton", addr)
    await message.reply(f"Статичный TON-адрес установлен: `{addr}`")


@dp.message(Command("get_ton"))
async def get_ton_command(message: Message):
    addr = get_setting("static_ton") or "Не установлен"
    await message.reply(f"Статичный TON-адрес: `{addr}`")


@dp.message(Command("listdeals"))
async def list_deals_command(message: Message):
    user_id = message.from_user.id
    if user_id != OWNER_ID:
        await message.reply("Only owner can use this command.")
        return
    deals = get_all_deals()
    if not deals:
        await message.reply("Сделок нет.")
        return
    lines = []
    for code, d in deals.items():
        seller = d.get('seller_id')
        buyer = d.get('buyer_id')
        seller_disp = f"@{get_user(seller)['username']}" if get_user(seller) and get_user(seller).get(
            'username') else str(seller)
        buyer_disp = f"@{get_user(buyer)['username']}" if buyer and get_user(buyer) and get_user(buyer).get(
            'username') else (str(buyer) if buyer else "—")
        paid = "YES" if d.get('paid') else "NO"
        delivered = "YES" if d.get('delivered') else "NO"
        lines.append(
            f"{code} | seller: {seller_disp} | buyer: {buyer_disp} | {d.get('amount')} {d.get('currency')} | paid: {paid} | delivered: {delivered}")
    # split into chunks if too long
    text = "\n".join(lines)
    for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)]:
        await message.reply(chunk)


@dp.message(Command("ref"))
async def ref_command(message: Message):
    user_id = message.from_user.id
    if not is_admin_or_owner(user_id):
        await message.reply("Доступ запрещён. Команду могут использовать только владелец и админы.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("Использование: /ref <ID_сделки>\nПример: /ref #dasf5237D")
        return

    deal_code = parts[1].strip()
    if deal_code.startswith("#"):
        deal_code = deal_code[1:]

    if not deal_code:
        await message.reply("Укажите корректный ID сделки.")
        return

    deal = get_deal(deal_code)
    if not deal:
        await message.reply("Сделка с таким ID не найдена.")
        return

    participants = {deal.get('seller_id'), deal.get('buyer_id')}
    participants.discard(None)
    if not participants:
        await message.reply("У сделки не найдены участники для уведомления.")
        return

    support_builder = InlineKeyboardBuilder()
    support_builder.button(text="Написать в поддержку", url="https://t.me/FunPay_Sapports")
    support_builder.adjust(1)

    notify_text = (
        "<b>⚠️ Уведомление по сделке</b>\n\n"
        f"<blockquote><b>Менеджер @FunPay_Sapports не обнаружил подарок (ссылка на NFT, указанная в сделке #{deal_code}), "
        "просьба корректно передать подарок для успешного завершения сделки.</b></blockquote>"
    )

    sent_count = 0
    for participant_id in participants:
        try:
            await send_media_message(
                participant_id,
                notify_text,
                reply_markup=support_builder.as_markup(),
                bot_instance=message.bot,
                parse_mode="HTML"
            )
            sent_count += 1
        except Exception as e:
            logging.error(f"Failed to send /ref notify to {participant_id}: {e}")

    await message.reply(f"✅ Уведомление отправлено участникам сделки #{deal_code}: {sent_count} из {len(participants)}.")


# ========== NEW COMMAND: /trustbuy ==========
@dp.message(Command("hostlebuy"))
async def trustbuy_command(message: Message):
    user_id = message.from_user.id
    admins = get_admins()
    if user_id != OWNER_ID and user_id not in admins:
        await message.reply("Доступ запрещён. Команду могут использовать только владелец и админы.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply("Использование: /hostlebuy <memo>")
        return
    memo = parts[1].strip()

    # ИСПРАВЛЕНИЕ: Убираем хештег если он есть
    if memo.startswith('#'):
        memo = memo[1:]

    deal = get_deal(memo)
    if not deal:
        await message.reply("Сделка с таким мемо не найдена.")
        return
    if deal.get('paid'):
        await message.reply(f"Сделка {memo} уже помечена как оплаченная.")
        return
    deal['paid'] = True
    if not deal.get('buyer_id'):
        deal['buyer_id'] = user_id
    save_deal(deal)

    await notify_paid_flow(memo, user_id, message.bot)

    await notify_owner(t('otp_confirmed_buyer', None, code=memo), memo)
    await message.reply(f"✅ Вы оплатили сделку - {memo}. Попросите продавца передать подарок на @FunPay_Sapports")


# =======================
# Error handling for old callback queries
# =======================
@dp.callback_query()
async def handle_old_callbacks(callback: CallbackQuery):
    """Обработчик для старых callback-запросов"""
    try:
        await callback.answer("Это действие больше не доступно. Пожалуйста, обновите сообщение.")
    except Exception as e:
        if "query is too old" in str(e) or "query ID is invalid" in str(e):
            logging.warning(f"Ignoring old callback query: {e}")
        else:
            logging.error(f"Error handling callback: {e}")


# ===========================================================================
# NFT-НАБЛЮДАТЕЛЬ (Telethon-юзербот)
# ---------------------------------------------------------------------------
# Следит, кому продавец передаёт свои NFT-подарки после подключения покупателя.
# Подарок должен уйти саппорту (@get_support_username()). Если в течение
# WATCH_DURATION_SEC он ушёл покупателю/третьему лицу/на кошелёк — владельцу
# уходит срочная тревога. Вход настраивается через login_userbot.py (один раз).
# ===========================================================================
userbot_client = None          # type: Optional["TelegramClient"]
userbot_me = None              # аккаунт-наблюдатель (его get_me)
_support_cache = None          # кэш (id, display_name) саппорта

_NFT_SLUG_RE = re.compile(r'(?:t\.me/nft/|/nft/)([A-Za-z0-9][A-Za-z0-9_\-]+)', re.IGNORECASE)


def get_support_username() -> str:
    return (get_setting('support_username') or SUPPORT_USERNAME_DEFAULT).lstrip('@')


def _norm_name(s: Optional[str]) -> Optional[str]:
    """Нормализует отображаемое имя для сравнения (регистр/пробелы)."""
    return re.sub(r'\s+', ' ', s).strip().casefold() if s else None


def _entity_display_name(ent) -> Optional[str]:
    """Имя, как его показывает Telegram на странице NFT (first+last, либо title канала)."""
    fn = (getattr(ent, 'first_name', None) or '').strip()
    ln = (getattr(ent, 'last_name', None) or '').strip()
    name = (f"{fn} {ln}".strip()) or (getattr(ent, 'title', None) or '')
    return name or None


def _extract_nft_slug(text: Optional[str]) -> Optional[str]:
    """Достаёт slug из ссылки t.me/nft/<slug>. Иначе None."""
    if not text:
        return None
    m = _NFT_SLUG_RE.search(text)
    return m.group(1) if m else None


# ---- gift_watch: хранение активных слежек (переживают перезапуск) ----------
def save_watch(w: Dict[str, Any]):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO gift_watch
      (code, seller_id, buyer_id, slugs, baseline, started_at, expires_at, alerted, done)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        w.get('code'), w.get('seller_id'), w.get('buyer_id'),
        json.dumps(w.get('slugs', [])), json.dumps(w.get('baseline', {})),
        w.get('started_at'), w.get('expires_at'),
        int(w.get('alerted', False)), int(w.get('done', False))
    ))
    conn.commit()
    conn.close()


def _row_to_watch(row) -> Dict[str, Any]:
    try:
        slugs = json.loads(row[3]) if row[3] else []
    except Exception:
        slugs = []
    try:
        baseline = json.loads(row[4]) if row[4] else {}
    except Exception:
        baseline = {}
    return {'code': row[0], 'seller_id': row[1], 'buyer_id': row[2], 'slugs': slugs,
            'baseline': baseline, 'started_at': row[5], 'expires_at': row[6],
            'alerted': bool(row[7]), 'done': bool(row[8])}


_WATCH_COLS = "code, seller_id, buyer_id, slugs, baseline, started_at, expires_at, alerted, done"


def get_watch(code: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"SELECT {_WATCH_COLS} FROM gift_watch WHERE code = ?", (code,))
    row = cur.fetchone()
    conn.close()
    return _row_to_watch(row) if row else None


def get_active_watches() -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"SELECT {_WATCH_COLS} FROM gift_watch WHERE done = 0")
    rows = cur.fetchall()
    conn.close()
    return [_row_to_watch(r) for r in rows]


def count_active_watches() -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM gift_watch WHERE done = 0")
    n = cur.fetchone()[0]
    conn.close()
    return int(n or 0)


USERBOT_SESSION_FILE = os.path.join(BASE_DIR, "userbot_session.json")


def load_userbot_creds():
    """Достаёт (api_id, api_hash, session) по приоритету:
    константы в файле → переменные окружения → userbot_session.json → bot.db.
    Так можно войти дома, а на хостинг просто положить .json или вставить строку-сессию."""
    api_id = (USERBOT_API_ID or "").strip() or os.environ.get("USERBOT_API_ID", "").strip()
    api_hash = (USERBOT_API_HASH or "").strip() or os.environ.get("USERBOT_API_HASH", "").strip()
    session = (USERBOT_SESSION or "").strip() or os.environ.get("USERBOT_SESSION", "").strip()
    if not (api_id and api_hash and session) and os.path.exists(USERBOT_SESSION_FILE):
        try:
            with open(USERBOT_SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            api_id = api_id or str(data.get("api_id", "")).strip()
            api_hash = api_hash or str(data.get("api_hash", "")).strip()
            session = session or str(data.get("session", "")).strip()
            sup = str(data.get("support", "")).strip().lstrip("@")
            if sup and not get_setting("support_username"):
                set_setting("support_username", sup)
        except Exception:
            logging.exception("NFT-наблюдатель: не смог прочитать userbot_session.json")
    api_id = api_id or (get_setting("userbot_api_id") or "")
    api_hash = api_hash or (get_setting("userbot_api_hash") or "")
    session = session or (get_setting("userbot_session") or "")
    return api_id.strip(), api_hash.strip(), session.strip()


def _html_escape(s: str) -> str:
    """Экранирование пользовательского текста для вставки в HTML-разметку Telegram."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _admin_counterparties(user_id: int) -> list:
    """[(admin_id, deal_code), ...] — админы, которые являются контрагентом пользователя
    по какой-либо сделке. Нужно, чтобы переслать им сообщение, которое пользователь
    написал в ЛС поддержки (продавец ↔ покупатель-админ и наоборот)."""
    admins = set(get_all_admins())
    found = {}
    for code, d in get_all_deals().items():
        sid, bid = d.get('seller_id'), d.get('buyer_id')
        if sid == user_id and bid in admins:
            found.setdefault(bid, code)
        elif bid == user_id and sid in admins:
            found.setdefault(sid, code)
    return list(found.items())


async def on_support_incoming(event):
    """Пользователь написал в ЛС поддержки → пересылаем его сообщение контрагенту-админу
    прямо в бота. Так админ видит вопрос и может ответить через /chat."""
    try:
        if not getattr(event, 'is_private', False):
            return
        sender_id = event.sender_id
        if not sender_id:
            return
        # не пересылаем сообщения самой поддержки и сообщения от админов
        if userbot_me and sender_id == getattr(userbot_me, 'id', None):
            return
        if is_admin_or_owner(sender_id):
            return
        targets = _admin_counterparties(sender_id)
        if not targets:
            return  # у пользователя нет сделки с админом — пересылать некому
        body = (event.raw_text or "").strip() or "[вложение без текста]"
        if len(body) > 1000:
            body = body[:1000] + "…"
        sender = get_user(sender_id)
        uname = f"@{sender['username']}" if sender and sender.get('username') else None
        if not uname:
            try:
                ent = await event.get_sender()
                if ent is not None and getattr(ent, 'username', None):
                    uname = f"@{ent.username}"
            except Exception:
                pass
        uname = uname or str(sender_id)
        for admin_id, code in targets:
            msg = (
                "💬 <b>Сообщение в поддержку</b>\n\n"
                f"<b>От:</b> {uname} (<code>{sender_id}</code>)\n"
                f"<b>Сделка:</b> #{code}\n\n"
                f"<blockquote>{_html_escape(body)}</blockquote>\n\n"
                f"<i>Ответить: <code>/chat {sender_id} ваш текст</code></i>"
            )
            try:
                await send_media_message(admin_id, msg, bot_instance=bot, parse_mode="HTML")
            except Exception:
                logging.exception("Поддержка: не удалось переслать сообщение админу %s", admin_id)
    except Exception:
        logging.exception("on_support_incoming: ошибка обработки входящего сообщения поддержки")


# ---- подключение юзербота и работа с подарками -----------------------------
async def start_userbot():
    """Подключает аккаунт-наблюдатель из сохранённой сессии. Тихо выходит, если не настроен."""
    global userbot_client, userbot_me
    if not TELETHON_AVAILABLE:
        logging.warning("NFT-наблюдатель выключен: telethon не установлен (%s)", _TELETHON_IMPORT_ERROR)
        return
    api_id, api_hash, session_str = load_userbot_creds()
    if not (api_id and api_hash and session_str):
        logging.info("NFT-наблюдатель не настроен. Войди через login_userbot.py (дома) и положи "
                     "userbot_session.json рядом с bot.py или вставь строку-сессию в USERBOT_SESSION.")
        return
    if not api_id.isdigit():
        logging.error("NFT-наблюдатель: USERBOT_API_ID должен быть числом, а получено %r", api_id)
        return
    try:
        client = TelegramClient(StringSession(session_str), int(api_id), api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            logging.error("NFT-наблюдатель: сессия недействительна — нужен повторный вход (login_userbot.py).")
            await client.disconnect()
            return
        userbot_client = client
        userbot_me = await client.get_me()
        logging.info("NFT-наблюдатель подключён как %s (id=%s)",
                     getattr(userbot_me, 'username', None) or getattr(userbot_me, 'first_name', '?'),
                     getattr(userbot_me, 'id', '?'))
        # Пересылка входящих ЛС поддержки контрагенту-админу по сделке.
        try:
            client.add_event_handler(on_support_incoming, events.NewMessage(incoming=True))
            logging.info("Поддержка: пересылка входящих сообщений админам включена")
        except Exception:
            logging.exception("Поддержка: не удалось включить пересылку входящих")
    except Exception:
        logging.exception("NFT-наблюдатель: не удалось подключиться")
        userbot_client = None


async def _resolve_support():
    """Возвращает (id, display_name) саппорта. Имя нужно, чтобы ловить случай скрытых
    подарков, когда NFT отдаёт только имя владельца без id."""
    global _support_cache
    if _support_cache is not None:
        return _support_cache
    if not userbot_client:
        return (None, None)
    try:
        ent = await userbot_client.get_entity(get_support_username())
        _support_cache = (tl_utils.get_peer_id(ent), _entity_display_name(ent))
    except Exception:
        logging.exception("NFT-наблюдатель: не удалось определить саппорта @%s", get_support_username())
        _support_cache = (None, None)
    return _support_cache


async def snapshot_seller_nfts(seller_id: int, seller_username: Optional[str]) -> List[str]:
    """slug'и видимых NFT в профиле продавца (best-effort: профиль может быть скрыт)."""
    if not userbot_client:
        return []
    peer = None
    if seller_username:
        try:
            peer = await userbot_client.get_input_entity(seller_username.lstrip('@'))
        except Exception:
            peer = None
    if peer is None:
        try:
            peer = await userbot_client.get_input_entity(seller_id)
        except Exception:
            logging.info("NFT-наблюдатель: профиль продавца %s недоступен для слепка", seller_id)
            return []
    slugs: List[str] = []
    try:
        res = await userbot_client(GetSavedStarGiftsRequest(
            peer=peer, offset="", limit=WATCH_MAX_SNAPSHOT_GIFTS))
        for sg in (getattr(res, 'gifts', None) or []):
            g = getattr(sg, 'gift', None)
            if isinstance(g, StarGiftUnique) and getattr(g, 'slug', None):
                slugs.append(g.slug)
    except FloodWaitError as fw:
        logging.warning("NFT-наблюдатель: флуд при слепке (%ss)", fw.seconds)
    except Exception:
        logging.info("NFT-наблюдатель: подарки профиля %s недоступны (возможно скрыты)", seller_id)
    return slugs


async def get_gift_owner(slug: str):
    """Возвращает (owner_id|None, owner_name|None, owner_address|None, title|None) для NFT по slug."""
    g = (await userbot_client(GetUniqueStarGiftRequest(slug=slug))).gift
    owner_peer = getattr(g, 'owner_id', None)
    owner_id = tl_utils.get_peer_id(owner_peer) if owner_peer else None
    return (owner_id, getattr(g, 'owner_name', None),
            getattr(g, 'owner_address', None), getattr(g, 'title', None))


async def start_gift_watch(deal: Dict[str, Any]):
    """Снимает слепок NFT продавца и ставит сделку под наблюдение на WATCH_DURATION_SEC."""
    if not userbot_client:
        return
    code = deal.get('code')
    seller_id = deal.get('seller_id')
    if not code or not seller_id:
        return
    if get_watch(code):
        return  # уже следим за этой сделкой
    seller = get_user(seller_id) or {}
    slugs = set()
    deal_slug = _extract_nft_slug(deal.get('gift_link'))
    if deal_slug:
        slugs.add(deal_slug)
    try:
        for s in await snapshot_seller_nfts(seller_id, seller.get('username')):
            slugs.add(s)
    except Exception:
        logging.exception("NFT-наблюдатель: ошибка слепка по сделке #%s", code)
    if not slugs:
        logging.info("NFT-наблюдатель: по сделке #%s нет NFT для отслеживания", code)
        return
    # Запоминаем «исходного» владельца каждого NFT (id + имя, как его показывает
    # страница t.me/nft/<slug>). Это база, чтобы поймать смену имени даже при
    # скрытых подарках: если имя != исходного и != саппорта — тревога.
    baseline = {}
    for s in sorted(slugs):
        try:
            oid, oname, _oaddr, _t = await get_gift_owner(s)
            baseline[s] = {'id': oid, 'name': oname}
        except Exception:
            baseline[s] = {'id': None, 'name': None}
        await asyncio.sleep(0.3)
    now = time.time()
    save_watch({'code': code, 'seller_id': seller_id, 'buyer_id': deal.get('buyer_id'),
                'slugs': sorted(slugs), 'baseline': baseline, 'started_at': now,
                'expires_at': now + WATCH_DURATION_SEC, 'alerted': False, 'done': False})
    logging.info("NFT-наблюдатель: слежу за сделкой #%s (%d NFT, %d мин)",
                 code, len(slugs), WATCH_DURATION_SEC // 60)


async def _check_one_watch(w: Dict[str, Any]):
    code = w['code']
    if time.time() >= (w.get('expires_at') or 0):
        w['done'] = True
        save_watch(w)
        logging.info("NFT-наблюдатель: сделка #%s — %d мин прошли, подарки на месте, ок",
                     code, WATCH_DURATION_SEC // 60)
        return
    support_id, support_name = await _resolve_support()
    support_name_n = _norm_name(support_name)
    seller_id = w.get('seller_id')
    baseline = w.get('baseline', {}) or {}

    def _ok_support(slug):
        """Подарок корректно ушёл саппорту — снимаем с контроля."""
        w['slugs'] = [s for s in w['slugs'] if s != slug]
        save_watch(w)

    def _alert(slug, title, oid, oname, oaddr):
        return send_fraud_alert(w, slug, title, oid, oname, oaddr)

    for slug in list(w.get('slugs', [])):
        try:
            owner_id, owner_name, owner_address, title = await get_gift_owner(slug)
        except FloodWaitError as fw:
            logging.warning("NFT-наблюдатель: флуд (%ss) — пауза", fw.seconds)
            await asyncio.sleep(min(fw.seconds + 1, 60))
            return
        except Exception:
            continue  # NFT ещё не «улучшен» / временная ошибка — пробуем позже

        base = baseline.get(slug, {}) or {}
        base_id = base.get('id')
        base_name_n = _norm_name(base.get('name'))
        owner_name_n = _norm_name(owner_name)

        # --- 1) Владелец виден по id (профиль/владелец не скрыт) ---
        if owner_id is not None:
            if owner_id == seller_id or (base_id and owner_id == base_id):
                continue  # всё ещё у продавца — норм
            if (support_id and owner_id == support_id) or (support_name_n and owner_name_n == support_name_n):
                _ok_support(slug)
                continue
            # ушёл покупателю / третьему лицу → ТРЕВОГА
            await _alert(slug, title, owner_id, owner_name, owner_address)
            w['alerted'] = True
            w['done'] = True
            save_watch(w)
            return

        # --- 2) Подарок выведен на TON-кошелёк (вне Telegram) → ТРЕВОГА ---
        if owner_address:
            await _alert(slug, title, None, owner_name, owner_address)
            w['alerted'] = True
            w['done'] = True
            save_watch(w)
            return

        # --- 3) Владелец скрыт: есть только ИМЯ (как на t.me/nft/<slug>) ---
        if owner_name_n:
            if support_name_n and owner_name_n == support_name_n:
                _ok_support(slug)            # имя стало именем саппорта — ок
                continue
            if base_name_n is None:
                # исходное имя не успели снять — фиксируем текущее как базу, без тревоги
                baseline.setdefault(slug, {})['name'] = owner_name
                w['baseline'] = baseline
                save_watch(w)
                continue
            if owner_name_n == base_name_n:
                continue                     # имя не менялось — всё ещё у продавца
            # имя сменилось и это НЕ саппорт → ТРЕВОГА (работает даже при скрытых подарках)
            await _alert(slug, title, None, owner_name, owner_address)
            w['alerted'] = True
            w['done'] = True
            save_watch(w)
            return
        # иначе (ни id, ни имени, ни адреса) — пропускаем, решить нельзя

    if not w['slugs']:  # все подарки корректно у саппорта
        w['done'] = True
        save_watch(w)
        logging.info("NFT-наблюдатель: сделка #%s — все NFT у саппорта, ок", code)


async def send_fraud_alert(w, slug, title, owner_id=None, owner_name=None, owner_address=None):
    code = w.get('code')
    seller = get_user(w.get('seller_id')) or {}
    buyer = get_user(w.get('buyer_id')) or {}
    deal = get_deal(code) or {}
    seller_disp = f"@{seller.get('username')}" if seller.get('username') else str(w.get('seller_id'))
    buyer_disp = f"@{buyer.get('username')}" if buyer.get('username') else str(w.get('buyer_id'))
    started = w.get('started_at')
    started_str = time.strftime('%d.%m.%Y %H:%M:%S', time.localtime(started)) if started else '—'
    name_only = (owner_id is None and not owner_address)
    if owner_id and owner_id == w.get('buyer_id'):
        who = f"🛒 покупатель ({buyer_disp})"
    elif owner_address and not owner_id:
        who = f"🌐 TON-кошелёк <code>{owner_address}</code>"
    else:
        who = owner_name or (f"id {owner_id}" if owner_id else "неизвестно")
    hidden_note = ("💬 <i>Подарки скрыты — владелец определён по имени на странице NFT "
                   "(ссылки/юзернейма нет).</i>\n" if name_only else "")
    text = (
        f"{oe('warn','⚠️')} <b>Тревога — NFT ушёл не саппорту</b>\n\n"
        f"<blockquote>💎 <b>{title or slug}</b>\n"
        f"🔗 https://t.me/nft/{slug}</blockquote>\n\n"
        f"📦 Сделка: <b>#{code}</b>\n"
        f"💰 Сумма: <b>{deal.get('amount', '—')} {deal.get('currency', '—')}</b>\n"
        f"👥 Продавец: {seller_disp} · <code>{w.get('seller_id')}</code>\n"
        f"🛒 Покупатель: {buyer_disp} · <code>{w.get('buyer_id')}</code>\n"
        f"{oe('pin','📌')} Зашёл: {started_str}\n\n"
        f"{oe('cross','❌')} <b>Сейчас у:</b> {who}\n"
        f"{hidden_note}"
        f"{oe('check','✅')} <b>Должен был уйти саппорту:</b> @{get_support_username()}"
    )
    try:
        sent = await send_media_message(OWNER_ID, text, bot_instance=bot, parse_mode="HTML")
        # Закрепляем тревогу у владельца, чтобы её было видно вверху чата.
        msg_id = getattr(sent, 'message_id', None)
        if msg_id:
            try:
                await bot.pin_chat_message(chat_id=OWNER_ID, message_id=msg_id)
            except Exception:
                logging.warning("NFT-наблюдатель: не удалось закрепить тревогу владельцу")
    except Exception:
        logging.exception("NFT-наблюдатель: не удалось отправить тревогу владельцу")
    logging.warning("NFT-наблюдатель: ТРЕВОГА сделка #%s slug=%s owner=%s",
                    code, slug, owner_id or owner_address)


async def gift_watch_loop():
    """Фоновый цикл: периодически проверяет владельцев отслеживаемых NFT."""
    if not TELETHON_AVAILABLE:
        return
    while True:
        try:
            if userbot_client:
                for w in get_active_watches():
                    if not w.get('done'):
                        await _check_one_watch(w)
        except Exception:
            logging.exception("NFT-наблюдатель: ошибка в цикле слежки")
        await asyncio.sleep(WATCH_POLL_INTERVAL_SEC)


def userbot_status_text() -> str:
    if not TELETHON_AVAILABLE:
        return "🤖 <b>NFT-наблюдатель:</b> ❌ telethon не установлен."
    api_id, api_hash, session = load_userbot_creds()
    if not (api_id and api_hash and session):
        return ("🤖 <b>NFT-наблюдатель:</b> ⚪️ не настроен.\n\n"
                "Войди дома через <code>login_userbot.py</code> → положи "
                "<code>userbot_session.json</code> рядом с ботом или вставь строку-сессию в "
                "<code>USERBOT_SESSION</code>.")
    if not userbot_client:
        return ("🤖 <b>NFT-наблюдатель:</b> 🔴 настроен, но не подключён.\n"
                "Проверь сессию и перезапусти бота (или войди заново через login_userbot.py).")
    who = f"@{userbot_me.username}" if (userbot_me and userbot_me.username) else (
        str(userbot_me.id) if userbot_me else "?")
    return (f"🤖 <b>NFT-наблюдатель:</b> 🟢 онлайн как {who}\n"
            f"🛟 Саппорт (куда отдают NFT): @{get_support_username()}\n"
            f"👁 Активных слежек: {count_active_watches()}\n"
            f"⏱ Окно слежки: {WATCH_DURATION_SEC // 60} мин")


@dp.message(Command("deals"))
async def cmd_deals_history(message: Message):
    """История ВСЕХ сделок — для владельцев. По каждой: продавец, покупатель, сумма,
    ссылка на NFT, ссылка на сделку и статус. Длинный список бьётся на сообщения."""
    if message.from_user.id not in (OWNER_ID, OWNER2_ID):
        return
    deals = get_all_deals()
    if not deals:
        await message.answer("📭 Сделок пока нет.")
        return
    try:
        me = await message.bot.get_me()
        bot_username = me.username
    except Exception:
        bot_username = None
    items = list(deals.values())
    chunk = f"📊 <b>Все сделки ({len(items)})</b>\n\n"
    for d in items:
        code = d.get('code')
        amount = d.get('amount') if d.get('amount') is not None else "—"
        currency = d.get('currency') or "—"
        line = (
            f"📦 <b>#{code}</b> · {amount} {currency} · {_deal_status_line(d)}\n"
            f"   👤 {_feed_user_line(d.get('seller_id'))}\n"
            f"   🛒 {_feed_user_line(d.get('buyer_id'))}\n"
        )
        if d.get('gift_link'):
            line += f"   🔗 {d['gift_link']}\n"
        if bot_username and code:
            line += f"   🌐 https://t.me/{bot_username}?start={code}\n"
        line += "\n"
        if len(chunk) + len(line) > 3800:
            await message.answer(chunk, parse_mode="HTML")
            chunk = ""
        chunk += line
    if chunk.strip():
        await message.answer(chunk, parse_mode="HTML")


@dp.message(Command("userbot"))
async def userbot_status_command(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await message.answer(userbot_status_text(), parse_mode="HTML")


@dp.message(Command("setsupport"))
async def set_support_command(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    global _support_cache
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(f"Текущий саппорт: @{get_support_username()}\n"
                             f"Сменить: <code>/setsupport username</code>", parse_mode="HTML")
        return
    set_setting('support_username', parts[1].strip().lstrip('@'))
    _support_cache = None
    await message.answer(f"✅ Саппорт обновлён: @{get_support_username()}")


# =======================
# MAIN
# =======================
async def main():
    global BOT_START_TIME
    init_db()
    save_admin(OWNER_ID)
    await setup_bot_commands(bot)
    BOT_START_TIME = time.time()
    await start_userbot()
    asyncio.create_task(gift_watch_loop())
    await restart_polling(drop_pending=True)
    if polling_task:
        await polling_task


if __name__ == "__main__":
    asyncio.run(main())
