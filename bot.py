"""
Telegram бот для учёта семейных расходов
Финальная версия - все категории работают!
"""

import logging
import asyncio
import asyncpg
from datetime import datetime, timedelta
from collections import defaultdict
import os
from decimal import Decimal

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, Update
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiohttp import web

# ===================== НАСТРОЙКИ =====================

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = "8591130371:AAE68AUESluEA34WjR7Ykm5Yy-WBn34Ryz0"
CURRENCY = "₸"

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://shygynbot_db_user:dYplgcbrQv3RbndkycOEEESEtSDtwPyQ@dpg-d6aktjfpm1nc73ddrj70-a/shygynbot_db')
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://shygynbot-1.onrender.com/')
PORT = int(os.getenv('PORT', 8000))

if not BOT_TOKEN:
    raise ValueError("Нет BOT_TOKEN!")
if not DATABASE_URL:
    raise ValueError("Нет DATABASE_URL!")
if not RENDER_EXTERNAL_URL:
    raise ValueError("Нет RENDER_EXTERNAL_URL!")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

db_pool = None

# ===================== КАТЕГОРИИ (БЕЗ НИЖНИХ ПОДЧЕРКИВАНИЙ) =====================

CATEGORIES = [
    'продукты', 'кафе', 'транспорт', 'здоровье', 'одежда',
    'дом', 'комуслуга', 'ипотека', 'кредит', 'подписка',
    'связь', 'парковка', 'дороги',  # вместо "платная_дорога"
    'техника', 'подарки', 'образование', 'милостыня',
    'развлечения', 'прочее'
]

CATEGORY_EMOJI = {
    'продукты': '🛒', 'кафе': '🍽', 'транспорт': '🚗',
    'здоровье': '💊', 'одежда': '👕', 'дом': '🏠',
    'комуслуга': '💡', 'ипотека': '🏘️', 'кредит': '💳',
    'подписка': '📱', 'связь': '📞', 'парковка': '🅿️',
    'дороги': '🛣️',  # обновлено
    'техника': '💻', 'подарки': '🎁', 'образование': '📚',
    'милостыня': '🤲', 'развлечения': '🎮', 'прочее': '📦'
}

# ===================== СОСТОЯНИЯ =====================

class ExpenseStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_category = State()
    edit_amount = State()
    waiting_for_budget_category = State()
    waiting_for_budget_amount = State()

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================

def to_float(value):
    if isinstance(value, Decimal):
        return float(value)
    return value

# ===================== ПОДКЛЮЧЕНИЕ К БД =====================

async def init_db_pool():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        print("✅ Подключение к PostgreSQL установлено")
        
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS expenses (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    amount DECIMAL(10,2) NOT NULL,
                    category TEXT NOT NULL,
                    date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS pending_expenses (
                    user_id BIGINT PRIMARY KEY,
                    amount DECIMAL(10,2) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS budgets (
                    category TEXT NOT NULL,
                    month INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    limit_amount DECIMAL(10,2) NOT NULL,
                    notified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (category, month, year)
                )
            ''')
            
            print("✅ Таблицы созданы/проверены")
    except Exception as e:
        print(f"❌ Ошибка подключения к PostgreSQL: {e}")
        raise

async def close_db_pool():
    if db_pool:
        await db_pool.close()
        print("✅ Соединения с БД закрыты")

# ===================== РАБОТА С РАСХОДАМИ =====================

async def add_expense(user_id: int, username: str, amount: float, category: str):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO expenses (user_id, username, amount, category)
            VALUES ($1, $2, $3, $4)
        ''', user_id, username, amount, category)

async def get_today_expenses():
    async with db_pool.acquire() as conn:
        today = datetime.now().date()
        rows = await conn.fetch('''
            SELECT amount, category, username, date 
            FROM expenses 
            WHERE DATE(date) = $1
            ORDER BY date DESC
        ''', today)
        return [(to_float(r['amount']), r['category'], r['username'], r['date']) for r in rows]

async def get_week_expenses():
    week_ago = datetime.now() - timedelta(days=7)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT amount, category, username 
            FROM expenses 
            WHERE date >= $1
        ''', week_ago)
        return [(to_float(r['amount']), r['category'], r['username']) for r in rows]

async def get_month_expenses(year: int, month: int):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT amount, category, username, date 
            FROM expenses 
            WHERE EXTRACT(YEAR FROM date) = $1 AND EXTRACT(MONTH FROM date) = $2
            ORDER BY date DESC
        ''', year, month)
        
        expenses = [(to_float(r['amount']), r['category'], r['username'], r['date']) for r in rows]
        total = sum(exp[0] for exp in expenses)
        by_category = defaultdict(float)
        by_user = defaultdict(float)
        
        for exp in expenses:
            by_category[exp[1]] += exp[0]
            by_user[exp[2] or "Аноним"] += exp[0]
        
        return total, dict(by_category), dict(by_user), expenses

async def get_last_expenses(limit: int = 10):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT amount, category, username, date 
            FROM expenses 
            ORDER BY date DESC 
            LIMIT $1
        ''', limit)
        return [(to_float(r['amount']), r['category'], r['username'], r['date']) for r in rows]

async def save_pending_expense(user_id: int, amount: float):
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO pending_expenses (user_id, amount, created_at)
            VALUES ($1, $2, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET amount = $2, created_at = CURRENT_TIMESTAMP
        ''', user_id, amount)

async def get_pending_expense(user_id: int):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT amount FROM pending_expenses 
            WHERE user_id = $1 AND created_at > NOW() - INTERVAL '1 hour'
        ''', user_id)
        return to_float(row['amount']) if row else None

async def clear_pending_expense(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute('DELETE FROM pending_expenses WHERE user_id = $1', user_id)

# ===================== РАБОТА С БЮДЖЕТАМИ =====================

async def set_budget(category: str, amount: float):
    now = datetime.now()
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO budgets (category, month, year, limit_amount, notified)
            VALUES ($1, $2, $3, $4, FALSE)
            ON CONFLICT (category, month, year) 
            DO UPDATE SET limit_amount = $4, notified = FALSE
        ''', category, now.month, now.year, amount)

async def get_budgets():
    now = datetime.now()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT category, limit_amount, notified 
            FROM budgets 
            WHERE month = $1 AND year = $2
        ''', now.month, now.year)
        return {r['category']: (to_float(r['limit_amount']), r['notified']) for r in rows}

async def update_notification_status(category: str):
    now = datetime.now()
    async with db_pool.acquire() as conn:
        await conn.execute('''
            UPDATE budgets 
            SET notified = TRUE 
            WHERE category = $1 AND month = $2 AND year = $3
        ''', category, now.month, now.year)

async def delete_budget(category: str):
    now = datetime.now()
    async with db_pool.acquire() as conn:
        await conn.execute('''
            DELETE FROM budgets 
            WHERE category = $1 AND month = $2 AND year = $3
        ''', category, now.month, now.year)

async def get_all_users():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT DISTINCT user_id FROM expenses')
        return [r['user_id'] for r in rows]

# ===================== ПРОВЕРКА БЮДЖЕТОВ =====================

async def check_budgets():
    while True:
        try:
            now = datetime.now()
            budgets = await get_budgets()
            
            if budgets:
                _, by_category, _, _ = await get_month_expenses(now.year, now.month)
                
                for category, (limit, notified) in budgets.items():
                    spent = by_category.get(category, 0)
                    
                    if spent > limit and not notified:
                        users = await get_all_users()
                        
                        emoji = CATEGORY_EMOJI.get(category, '•')
                        over_amount = spent - limit
                        over_percent = (over_amount / limit) * 100
                        
                        for user_id in users:
                            try:
                                await bot.send_message(
                                    user_id,
                                    f"⚠️ *ПРЕВЫШЕНИЕ БЮДЖЕТА!*\n\n"
                                    f"{emoji} Категория: {category}\n"
                                    f"💰 Лимит: {limit:.0f} {CURRENCY}\n"
                                    f"💳 Потрачено: {spent:.0f} {CURRENCY}\n"
                                    f"📈 Превышение: +{over_amount:.0f} {CURRENCY} ({over_percent:.1f}%)\n\n"
                                    f"📅 {now.strftime('%d.%m.%Y')}",
                                    parse_mode=ParseMode.MARKDOWN
                                )
                            except Exception as e:
                                logging.error(f"Ошибка отправки уведомления: {e}")
                        
                        await update_notification_status(category)
                        
        except Exception as e:
            logging.error(f"Ошибка проверки бюджетов: {e}")
        
        await asyncio.sleep(3600)

# ===================== КЛАВИАТУРЫ =====================

def get_categories_keyboard():
    builder = InlineKeyboardBuilder()
    for cat in CATEGORIES:
        emoji = CATEGORY_EMOJI.get(cat, '•')
        # Используем простое название категории без изменений
        builder.button(text=f"{emoji} {cat}", callback_data=f"cat_{cat}")
    builder.adjust(2)
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(2, 1)
    return builder.as_markup()

async def get_budget_categories_keyboard():
    builder = InlineKeyboardBuilder()
    budgets = await get_budgets()
    
    for cat in CATEGORIES:
        emoji = CATEGORY_EMOJI.get(cat, '•')
        if cat in budgets:
            limit, _ = budgets[cat]
            builder.button(text=f"{emoji} {cat} ({limit:.0f}{CURRENCY})", callback_data=f"budget_{cat}")
        else:
            builder.button(text=f"{emoji} {cat} (не установлен)", callback_data=f"budget_{cat}")
    
    builder.adjust(1)
    builder.button(text="📋 Показать все бюджеты", callback_data="show_budgets")
    builder.button(text="❌ Закрыть", callback_data="cancel")
    builder.adjust(1, 1)
    return builder.as_markup()

def get_confirmation_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data="confirm")
    builder.button(text="✏️ Изменить сумму", callback_data="edit_amount")
    builder.button(text="🔄 Другая категория", callback_data="edit_category")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(2, 2)
    return builder.as_markup()

# ===================== КОМАНДЫ =====================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "👋 *Добро пожаловать в семейный бюджет!*\n\n"
        "📝 *Как записывать расходы:*\n"
        "• Отправьте сумму (например: 1500)\n"
        "• Выберите категорию из списка\n\n"
        "💰 *Управление бюджетом:*\n"
        "/budget — установить лимиты на категории\n"
        "/budgets — посмотреть текущие бюджеты\n\n"
        "📊 *Отчёты:*\n"
        "/today — расходы за сегодня\n"
        "/week — отчёт за неделю\n"
        "/month — отчёт за месяц\n"
        "/last — последние записи\n"
        "/categories — список категорий\n\n"
        "❓ /help — подробная инструкция"
    )
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📚 *Подробная инструкция*\n\n"
        "*Как записывать расход:*\n"
        "1️⃣ Отправьте сумму цифрами (например: 2500)\n"
        "2️⃣ Выберите категорию из списка\n"
        "3️⃣ Подтвердите или измените данные\n\n"
        "*Как установить бюджет:*\n"
        "1️⃣ Введите /budget\n"
        "2️⃣ Выберите категорию\n"
        "3️⃣ Введите сумму лимита на месяц\n"
        "4️⃣ При превышении придет уведомление\n\n"
        "*Категории расходов:*\n"
        "🏠 дом          💡 комуслуга     🏘️ ипотека\n"
        "💳 кредит       📱 подписка      📞 связь\n"
        "🚗 транспорт    🅿️ парковка      🛣️ дороги\n"
        "🛒 продукты     👕 одежда        💻 техника\n"
        "🍽 кафе         🎁 подарки       📚 образование\n"
        "💊 здоровье     🤲 милостыня     🎮 развлечения\n"
        "📦 прочее"
    )
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("categories"))
async def cmd_categories(message: Message):
    text = "📋 *Список категорий:*\n\n"
    for cat in CATEGORIES:
        emoji = CATEGORY_EMOJI.get(cat, '•')
        text += f"{emoji} {cat}\n"
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("budget"))
async def cmd_budget(message: Message, state: FSMContext):
    keyboard = await get_budget_categories_keyboard()
    await message.answer(
        "📌 Выберите категорию для установки бюджета:",
        reply_markup=keyboard
    )
    await state.set_state(ExpenseStates.waiting_for_budget_category)

@dp.message(Command("budgets"))
async def cmd_show_budgets(message: Message):
    budgets = await get_budgets()
    
    if not budgets:
        await message.answer("📊 Бюджеты не установлены")
        return
    
    now = datetime.now()
    _, by_category, _, _ = await get_month_expenses(now.year, now.month)
    
    response = f"💰 *Бюджеты на {now.strftime('%B %Y')}:*\n\n"
    
    for category, (limit, _) in budgets.items():
        spent = by_category.get(category, 0)
        emoji = CATEGORY_EMOJI.get(category, '•')
        
        if spent > limit:
            status = "⚠️ ПРЕВЫШЕН!"
        else:
            remaining = limit - spent
            status = f"✅ Осталось: {remaining:.0f} {CURRENCY}"
        
        response += f"{emoji} {category}:\n"
        response += f"   Лимит: {limit:.0f} {CURRENCY}\n"
        response += f"   Потрачено: {spent:.0f} {CURRENCY}\n"
        response += f"   {status}\n\n"
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("today"))
async def cmd_today(message: Message):
    expenses = await get_today_expenses()
    
    if not expenses:
        await message.answer("✅ За сегодня расходов пока нет")
        return
    
    total = sum(exp[0] for exp in expenses)
    by_category = defaultdict(float)
    by_user = defaultdict(float)
    
    for exp in expenses:
        by_category[exp[1]] += exp[0]
        by_user[exp[2] or "Аноним"] += exp[0]
    
    response = f"📅 *Расходы за сегодня:*\n\n"
    
    for cat, amount in sorted(by_category.items(), key=lambda x: x[1], reverse=True):
        emoji = CATEGORY_EMOJI.get(cat, '•')
        response += f"{emoji} {cat}: {amount:.0f} {CURRENCY}\n"
    
    response += f"\n💳 *ИТОГО: {total:.0f} {CURRENCY}*\n\n"
    
    response += "*По пользователям:*\n"
    for user, amount in sorted(by_user.items(), key=lambda x: x[1], reverse=True):
        percentage = (amount / total) * 100
        response += f"👤 {user}: {amount:.0f} {CURRENCY} ({percentage:.1f}%)\n"
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("week"))
async def cmd_week(message: Message):
    expenses = await get_week_expenses()
    
    if not expenses:
        await message.answer("📊 За последнюю неделю расходов нет")
        return
    
    total = sum(exp[0] for exp in expenses)
    by_category = defaultdict(float)
    
    for exp in expenses:
        by_category[exp[1]] += exp[0]
    
    response = f"📊 *Отчёт за неделю*\n\n"
    response += f"💰 Всего: {total:.0f} {CURRENCY}\n"
    response += f"📊 В день: {total/7:.0f} {CURRENCY}\n\n"
    
    for cat, amount in sorted(by_category.items(), key=lambda x: x[1], reverse=True)[:5]:
        emoji = CATEGORY_EMOJI.get(cat, '•')
        response += f"{emoji} {cat}: {amount:.0f} {CURRENCY}\n"
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("month"))
async def cmd_month(message: Message):
    now = datetime.now()
    total, by_category, by_user, _ = await get_month_expenses(now.year, now.month)
    
    month_names = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
                  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']
    
    if total == 0:
        await message.answer(f"📊 За {month_names[now.month-1]} расходов нет")
        return
    
    response = f"📊 *Отчёт за {month_names[now.month-1]} {now.year}*\n\n"
    
    for cat, amount in sorted(by_category.items(), key=lambda x: x[1], reverse=True):
        percentage = (amount / total) * 100
        emoji = CATEGORY_EMOJI.get(cat, '•')
        response += f"{emoji} {cat}: {amount:.0f} {CURRENCY} ({percentage:.1f}%)\n"
    
    response += f"\n💳 *ВСЕГО: {total:.0f} {CURRENCY}*\n\n"
    
    response += "*По пользователям:*\n"
    for user, amount in sorted(by_user.items(), key=lambda x: x[1], reverse=True):
        percentage = (amount / total) * 100
        response += f"👤 {user}: {amount:.0f} {CURRENCY} ({percentage:.1f}%)\n"
    
    budgets = await get_budgets()
    if budgets:
        response += f"\n💰 *Бюджеты:*\n"
        for cat, (limit, _) in budgets.items():
            spent = by_category.get(cat, 0)
            emoji = CATEGORY_EMOJI.get(cat, '•')
            if spent > limit:
                response += f"{emoji} {cat}: {spent:.0f} / {limit:.0f} ⚠️\n"
            else:
                response += f"{emoji} {cat}: {spent:.0f} / {limit:.0f}\n"
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("last"))
async def cmd_last(message: Message):
    expenses = await get_last_expenses(10)
    
    if not expenses:
        await message.answer("📝 Пока нет записей")
        return
    
    response = "📝 *Последние 10 записей:*\n\n"
    for i, exp in enumerate(expenses, 1):
        amount, category, username, date = exp
        date_obj = datetime.strptime(str(date).split('.')[0], "%Y-%m-%d %H:%M:%S")
        date_str = date_obj.strftime("%d.%m %H:%M")
        emoji = CATEGORY_EMOJI.get(category, '•')
        user_short = username[:15] + "..." if username and len(username) > 15 else username or "Аноним"
        response += f"{i}. {date_str} {emoji} {category}: {amount:.0f} {CURRENCY} ({user_short})\n"
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN)

# ===================== ОБРАБОТКА БЮДЖЕТОВ =====================

@dp.callback_query(F.data == "show_budgets", ExpenseStates.waiting_for_budget_category)
async def show_budgets_from_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    budgets = await get_budgets()
    
    if not budgets:
        keyboard = await get_budget_categories_keyboard()
        await callback.message.edit_text(
            "📊 Бюджеты не установлены",
            reply_markup=keyboard
        )
        return
    
    now = datetime.now()
    _, by_category, _, _ = await get_month_expenses(now.year, now.month)
    
    response = f"💰 *Бюджеты на {now.strftime('%B %Y')}:*\n\n"
    
    for category, (limit, _) in budgets.items():
        spent = by_category.get(category, 0)
        emoji = CATEGORY_EMOJI.get(category, '•')
        
        if spent > limit:
            status = "⚠️ ПРЕВЫШЕН!"
        else:
            remaining = limit - spent
            status = f"✅ Осталось: {remaining:.0f} {CURRENCY}"
        
        response += f"{emoji} {category}:\n"
        response += f"   Лимит: {limit:.0f} {CURRENCY}\n"
        response += f"   Потрачено: {spent:.0f} {CURRENCY}\n"
        response += f"   {status}\n\n"
    
    keyboard = await get_budget_categories_keyboard()
    await callback.message.edit_text(
        response,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith('budget_'), ExpenseStates.waiting_for_budget_category)
async def process_budget_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    category = callback.data.replace('budget_', '')
    await state.update_data(budget_category=category)
    
    budgets = await get_budgets()
    if category in budgets:
        limit, _ = budgets[category]
        await callback.message.edit_text(
            f"📌 Категория: {category}\n"
            f"Текущий бюджет: {limit:.0f} {CURRENCY}\n\n"
            f"Введите новую сумму или 0 для удаления:",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await callback.message.edit_text(
            f"📌 Категория: {category}\n\n"
            f"Введите сумму бюджета на месяц:",
            parse_mode=ParseMode.MARKDOWN
        )
    
    await state.set_state(ExpenseStates.waiting_for_budget_amount)

@dp.message(ExpenseStates.waiting_for_budget_amount)
async def process_budget_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число")
        return
    
    data = await state.get_data()
    category = data.get('budget_category')
    
    if amount <= 0:
        await delete_budget(category)
        await message.answer(
            f"✅ Бюджет для категории {category} удалён",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await set_budget(category, amount)
        emoji = CATEGORY_EMOJI.get(category, '•')
        await message.answer(
            f"✅ Бюджет установлен:\n"
            f"{emoji} {category}: {amount:.0f} {CURRENCY} на текущий месяц",
            parse_mode=ParseMode.MARKDOWN
        )
    
    await state.clear()

# ===================== ОБРАБОТКА РАСХОДОВ =====================

@dp.message(F.text.regexp(r'^-?\d+$'))
async def handle_amount(message: Message, state: FSMContext):
    amount = abs(float(message.text.strip()))
    await save_pending_expense(message.from_user.id, amount)
    await state.update_data(amount=amount)
    
    await message.answer(
        f"💰 Сумма: {amount:.0f} {CURRENCY}\n\n"
        f"📌 Выберите категорию:",
        reply_markup=get_categories_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(ExpenseStates.waiting_for_category)

@dp.callback_query(F.data.startswith('cat_'), ExpenseStates.waiting_for_category)
async def process_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    category = callback.data.replace('cat_', '')
    
    data = await state.get_data()
    amount = data.get('amount')
    
    if not amount:
        amount = await get_pending_expense(callback.from_user.id)
    
    if not amount:
        await callback.message.answer("❌ Ошибка, попробуйте снова")
        await state.clear()
        return
    
    await state.update_data(category=category)
    
    emoji = CATEGORY_EMOJI.get(category, '•')
    await callback.message.edit_text(
        text=(
            f"📝 *Проверьте данные:*\n\n"
            f"💰 Сумма: {amount:.0f} {CURRENCY}\n"
            f"{emoji} Категория: {category}\n\n"
            f"Всё верно?"
        ),
        reply_markup=get_confirmation_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data == 'confirm', ExpenseStates.waiting_for_category)
async def process_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    data = await state.get_data()
    amount = data.get('amount')
    category = data.get('category')
    
    if not amount or not category:
        await callback.message.answer("❌ Ошибка")
        await state.clear()
        return
    
    username = callback.from_user.username or callback.from_user.full_name
    await add_expense(callback.from_user.id, username, amount, category)
    await clear_pending_expense(callback.from_user.id)
    
    budgets = await get_budgets()
    if category in budgets:
        limit, _ = budgets[category]
        _, by_category, _, _ = await get_month_expenses(datetime.now().year, datetime.now().month)
        spent = by_category.get(category, 0)
        
        if spent > limit:
            emoji = CATEGORY_EMOJI.get(category, '•')
            over = spent - limit
            await callback.message.edit_text(
                text=(
                    f"✅ *Расход сохранён!*\n\n"
                    f"{emoji} {category}: {amount:.0f} {CURRENCY}\n"
                    f"👤 {username}\n\n"
                    f"⚠️ *Внимание!* Превышен бюджет!\n"
                    f"Лимит: {limit:.0f} {CURRENCY}\n"
                    f"Превышение: +{over:.0f} {CURRENCY}"
                ),
                parse_mode=ParseMode.MARKDOWN
            )
            await state.clear()
            return
    
    emoji = CATEGORY_EMOJI.get(category, '•')
    await callback.message.edit_text(
        text=(
            f"✅ *Расход сохранён!*\n\n"
            f"{emoji} {category}: {amount:.0f} {CURRENCY}\n"
            f"👤 {username}"
        ),
        parse_mode=ParseMode.MARKDOWN
    )
    await state.clear()

@dp.callback_query(F.data == 'edit_amount', ExpenseStates.waiting_for_category)
async def process_edit_amount(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("✏️ Введите новую сумму:")
    await state.set_state(ExpenseStates.edit_amount)

@dp.callback_query(F.data == 'edit_category', ExpenseStates.waiting_for_category)
async def process_edit_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "📌 Выберите новую категорию:",
        reply_markup=get_categories_keyboard()
    )

@dp.callback_query(F.data == 'cancel')
async def process_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await clear_pending_expense(callback.from_user.id)
    await callback.message.edit_text("❌ Операция отменена")
    await state.clear()

@dp.message(ExpenseStates.edit_amount)
async def process_new_amount(message: Message, state: FSMContext):
    try:
        amount = abs(float(message.text.strip()))
    except ValueError:
        await message.answer("❌ Введите число")
        return
    
    await save_pending_expense(message.from_user.id, amount)
    await state.update_data(amount=amount)
    
    data = await state.get_data()
    category = data.get('category')
    
    if category:
        emoji = CATEGORY_EMOJI.get(category, '•')
        await message.answer(
            text=(
                f"📝 *Проверьте данные:*\n\n"
                f"💰 Сумма: {amount:.0f} {CURRENCY}\n"
                f"{emoji} Категория: {category}\n\n"
                f"Всё верно?"
            ),
            reply_markup=get_confirmation_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
        await state.set_state(ExpenseStates.waiting_for_category)
    else:
        await message.answer(
            f"💰 Сумма: {amount:.0f} {CURRENCY}\n\n"
            f"📌 Выберите категорию:",
            reply_markup=get_categories_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
        await state.set_state(ExpenseStates.waiting_for_category)

# ===================== ВЕБХУК И ЗАПУСК =====================

async def handle_webhook(request):
    try:
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await dp.feed_update(bot, update)
        return web.Response(text="OK", status=200)
    except Exception as e:
        logging.error(f"Ошибка обработки вебхука: {e}")
        return web.Response(text="Error", status=500)

async def health_check(request):
    return web.Response(text="OK", status=200)

async def on_startup():
    await init_db_pool()
    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
    await bot.set_webhook(webhook_url, allowed_updates=dp.resolve_used_update_types())
    print(f"✅ Вебхук установлен на {webhook_url}")
    asyncio.create_task(check_budgets())
    print("🤖 Бот запущен и готов к работе!")

async def on_shutdown():
    await bot.delete_webhook()
    await close_db_pool()
    print("👋 Бот остановлен")

async def main():
    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)
    app.router.add_get("/healthcheck", health_check)
    app.router.add_get("/", health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    
    await on_startup()
    print(f"🚀 Сервер запущен на порту {PORT}")
    await site.start()
    
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        await on_shutdown()
        await runner.cleanup()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Бот остановлен пользователем")
