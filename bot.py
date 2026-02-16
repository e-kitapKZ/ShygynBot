
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode

# ===================== НАСТРОЙКИ =====================

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = "8591130371:AAE68AUESluEA34WjR7Ykm5Yy-WBn34Ryz0"
CURRENCY = "₸"  # или "₽" для рублей

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ===================== КАТЕГОРИИ =====================

CATEGORIES = [
    # Основные расходы
    'продукты',
    'кафе',
    'транспорт',
    'здоровье',
    'одежда',
    
    # Жильё и коммуналка
    'дом',
    'комуслуга',
    'ипотека',
    
    # Финансы
    'кредит',
    'подписка',
    'связь',
    
    # Транспортные расходы
    'парковка',
    'платная_дорога',
    
    # Техника и покупки
    'техника',
    'подарки',
    'образование',
    
    # Благотворительность
    'милостыня',
    
    # Остальное
    'развлечения',
    'прочее'
]

CATEGORY_EMOJI = {
    # Основные
    'продукты': '🛒',
    'кафе': '🍽',
    'транспорт': '🚗',
    'здоровье': '💊',
    'одежда': '👕',
    
    # Жильё
    'дом': '🏠',
    'комуслуга': '💡',
    'ипотека': '🏘️',
    
    # Финансы
    'кредит': '💳',
    'подписка': '📱',
    'связь': '📞',
    
    # Транспорт
    'парковка': '🅿️',
    'платная_дорога': '🛣️',
    
    # Техника и покупки
    'техника': '💻',
    'подарки': '🎁',
    'образование': '📚',
    
    # Благотворительность
    'милостыня': '🤲',
    
    # Остальное
    'развлечения': '🎮',
    'прочее': '📦'
}

# ===================== СОСТОЯНИЯ =====================

class ExpenseStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_category = State()
    edit_amount = State()
    waiting_for_budget_category = State()
    waiting_for_budget_amount = State()

# ===================== БАЗА ДАННЫХ =====================

def init_db():
    """Инициализация всех таблиц"""
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    
    # Расходы
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Временные расходы
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_expenses (
            user_id INTEGER PRIMARY KEY,
            amount REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Бюджеты по категориям
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS budgets (
            category TEXT NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            limit_amount REAL NOT NULL,
            notified BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (category, month, year)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ База данных готова")

# ===================== РАБОТА С РАСХОДАМИ =====================

def add_expense(user_id: int, username: str, amount: float, category: str):
    """Добавить расход"""
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO expenses (user_id, username, amount, category)
        VALUES (?, ?, ?, ?)
    ''', (user_id, username, amount, category))
    conn.commit()
    conn.close()

def get_today_expenses():
    """Расходы за сегодня"""
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    today = datetime.now().date()
    cursor.execute('''
        SELECT amount, category, username, date 
        FROM expenses 
        WHERE DATE(date) = ?
        ORDER BY date DESC
    ''', (today,))
    expenses = cursor.fetchall()
    conn.close()
    return expenses

def get_week_expenses():
    """Расходы за неделю"""
    week_ago = datetime.now() - timedelta(days=7)
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT amount, category, username 
        FROM expenses 
        WHERE date >= ?
    ''', (week_ago,))
    expenses = cursor.fetchall()
    conn.close()
    return expenses

def get_month_expenses(year: int, month: int):
    """Расходы за месяц"""
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT amount, category, username, date 
        FROM expenses 
        WHERE strftime('%Y', date) = ? AND strftime('%m', date) = ?
        ORDER BY date DESC
    ''', (str(year), f"{month:02d}"))
    expenses = cursor.fetchall()
    conn.close()
    
    total = sum(exp[0] for exp in expenses)
    by_category = defaultdict(float)
    by_user = defaultdict(float)
    
    for exp in expenses:
        by_category[exp[1]] += exp[0]
        by_user[exp[2] or "Аноним"] += exp[0]
    
    return total, dict(by_category), dict(by_user), expenses

def get_last_expenses(limit: int = 10):
    """Последние записи"""
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT amount, category, username, date 
        FROM expenses 
        ORDER BY date DESC 
        LIMIT ?
    ''', (limit,))
    expenses = cursor.fetchall()
    conn.close()
    return expenses

# ===================== РАБОТА С БЮДЖЕТАМИ =====================

def set_budget(category: str, amount: float):
    """Установить бюджет на текущий месяц"""
    now = datetime.now()
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO budgets (category, month, year, limit_amount, notified)
        VALUES (?, ?, ?, ?, 0)
    ''', (category, now.month, now.year, amount))
    
    conn.commit()
    conn.close()

def get_budgets():
    """Получить все бюджеты на текущий месяц"""
    now = datetime.now()
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT category, limit_amount, notified 
        FROM budgets 
        WHERE month = ? AND year = ?
    ''', (now.month, now.year))
    
    budgets = cursor.fetchall()
    conn.close()
    return {cat: (limit, notified) for cat, limit, notified in budgets}

def update_notification_status(category: str):
    """Отметить что уведомление отправлено"""
    now = datetime.now()
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE budgets 
        SET notified = 1 
        WHERE category = ? AND month = ? AND year = ?
    ''', (category, now.month, now.year))
    
    conn.commit()
    conn.close()

def delete_budget(category: str):
    """Удалить бюджет для категории"""
    now = datetime.now()
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        DELETE FROM budgets 
        WHERE category = ? AND month = ? AND year = ?
    ''', (category, now.month, now.year))
    
    conn.commit()
    conn.close()

# ===================== ПРОВЕРКА БЮДЖЕТОВ =====================

async def check_budgets():
    """Проверка превышения бюджетов и отправка уведомлений"""
    while True:
        try:
            now = datetime.now()
            budgets = get_budgets()
            
            if budgets:
                _, by_category, _, _ = get_month_expenses(now.year, now.month)
                
                for category, (limit, notified) in budgets.items():
                    spent = by_category.get(category, 0)
                    
                    if spent > limit and not notified:
                        conn = sqlite3.connect('family_budget.db')
                        cursor = conn.cursor()
                        cursor.execute('SELECT DISTINCT user_id FROM expenses')
                        users = cursor.fetchall()
                        conn.close()
                        
                        emoji = CATEGORY_EMOJI.get(category, '•')
                        over_amount = spent - limit
                        over_percent = (over_amount / limit) * 100
                        
                        for (user_id,) in users:
                            try:
                                await bot.send_message(
                                    user_id,
                                    f"⚠️ *ПРЕВЫШЕНИЕ БЮДЖЕТА!*\n\n"
                                    f"{emoji} Категория: *{category}*\n"
                                    f"💰 Лимит: *{limit:.0f} {CURRENCY}*\n"
                                    f"💳 Потрачено: *{spent:.0f} {CURRENCY}*\n"
                                    f"📈 Превышение: *+{over_amount:.0f} {CURRENCY}* ({over_percent:.1f}%)\n\n"
                                    f"📅 {now.strftime('%d.%m.%Y')}",
                                    parse_mode=ParseMode.MARKDOWN
                                )
                            except Exception as e:
                                logging.error(f"Ошибка отправки уведомления: {e}")
                        
                        update_notification_status(category)
                        
        except Exception as e:
            logging.error(f"Ошибка проверки бюджетов: {e}")
        
        await asyncio.sleep(3600)

# ===================== КЛАВИАТУРЫ =====================

def get_categories_keyboard():
    """Клавиатура с категориями"""
    builder = InlineKeyboardBuilder()
    for cat in CATEGORIES:
        emoji = CATEGORY_EMOJI.get(cat, '•')
        builder.button(text=f"{emoji} {cat}", callback_data=f"cat_{cat}")
    builder.adjust(2)
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(2, 1)
    return builder.as_markup()

def get_budget_categories_keyboard():
    """Клавиатура для выбора категории бюджета"""
    builder = InlineKeyboardBuilder()
    budgets = get_budgets()
    
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
    """Клавиатура подтверждения"""
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
        "• Отправьте сумму (например: *1500*)\n"
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
        "1️⃣ Отправьте сумму цифрами (например: *2500*)\n"
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
        "🚗 транспорт    🅿️ парковка      🛣️ платная_дорога\n"
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
    """Установка бюджета"""
    await message.answer(
        "📌 Выберите категорию для установки бюджета:",
        reply_markup=get_budget_categories_keyboard()
    )
    await state.set_state(ExpenseStates.waiting_for_budget_category)

@dp.message(Command("budgets"))
async def cmd_show_budgets(message: Message):
    """Показать все бюджеты"""
    budgets = get_budgets()
    
    if not budgets:
        await message.answer("📊 Бюджеты не установлены")
        return
    
    now = datetime.now()
    _, by_category, _, _ = get_month_expenses(now.year, now.month)
    
    response = f"💰 *Бюджеты на {now.strftime('%B %Y')}:*\n\n"
    
    for category, (limit, _) in budgets.items():
        spent = by_category.get(category, 0)
        emoji = CATEGORY_EMOJI.get(category, '•')
        
        if spent > limit:
            status = "⚠️ ПРЕВЫШЕН!"
        else:
            remaining = limit - spent
            status = f"✅ Осталось: {remaining:.0f} {CURRENCY}"
        
        response += f"{emoji} *{category}*:\n"
        response += f"   Лимит: {limit:.0f} {CURRENCY}\n"
        response += f"   Потрачено: {spent:.0f} {CURRENCY}\n"
        response += f"   {status}\n\n"
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("today"))
async def cmd_today(message: Message):
    """Расходы за сегодня"""
    expenses = get_today_expenses()
    
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
        response += f"{emoji} {cat}: *{amount:.0f} {CURRENCY}*\n"
    
    response += f"\n💳 *ИТОГО: {total:.0f} {CURRENCY}*\n\n"
    
    response += "*По пользователям:*\n"
    for user, amount in sorted(by_user.items(), key=lambda x: x[1], reverse=True):
        percentage = (amount / total) * 100
        response += f"👤 {user}: *{amount:.0f} {CURRENCY}* ({percentage:.1f}%)\n"
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("week"))
async def cmd_week(message: Message):
    """Отчёт за неделю"""
    expenses = get_week_expenses()
    
    if not expenses:
        await message.answer("📊 За последнюю неделю расходов нет")
        return
    
    total = sum(exp[0] for exp in expenses)
    by_category = defaultdict(float)
    
    for exp in expenses:
        by_category[exp[1]] += exp[0]
    
    response = f"📊 *Отчёт за неделю*\n\n"
    response += f"💰 Всего: *{total:.0f} {CURRENCY}*\n"
    response += f"📊 В день: *{total/7:.0f} {CURRENCY}*\n\n"
    
    for cat, amount in sorted(by_category.items(), key=lambda x: x[1], reverse=True)[:5]:
        emoji = CATEGORY_EMOJI.get(cat, '•')
        response += f"{emoji} {cat}: *{amount:.0f} {CURRENCY}*\n"
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("month"))
async def cmd_month(message: Message):
    """Отчёт за месяц"""
    now = datetime.now()
    total, by_category, by_user, _ = get_month_expenses(now.year, now.month)
    
    month_names = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
                  'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']
    
    if total == 0:
        await message.answer(f"📊 За {month_names[now.month-1]} расходов нет")
        return
    
    response = f"📊 *Отчёт за {month_names[now.month-1]} {now.year}*\n\n"
    
    for cat, amount in sorted(by_category.items(), key=lambda x: x[1], reverse=True):
        percentage = (amount / total) * 100
        emoji = CATEGORY_EMOJI.get(cat, '•')
        response += f"{emoji} {cat}: *{amount:.0f} {CURRENCY}* ({percentage:.1f}%)\n"
    
    response += f"\n💳 *ВСЕГО: {total:.0f} {CURRENCY}*\n\n"
    
    response += "*По пользователям:*\n"
    for user, amount in sorted(by_user.items(), key=lambda x: x[1], reverse=True):
        percentage = (amount / total) * 100
        response += f"👤 {user}: *{amount:.0f} {CURRENCY}* ({percentage:.1f}%)\n"
    
    budgets = get_budgets()
    if budgets:
        response += f"\n💰 *Бюджеты:*\n"
        for cat, (limit, _) in budgets.items():
            spent = by_category.get(cat, 0)
            emoji = CATEGORY_EMOJI.get(cat, '•')
            if spent > limit:
                response += f"{emoji} {cat}: *{spent:.0f}* / {limit:.0f} ⚠️\n"
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("last"))
async def cmd_last(message: Message):
    """Последние записи"""
    expenses = get_last_expenses(10)
    
    if not expenses:
        await message.answer("📝 Пока нет записей")
        return
    
    response = "📝 *Последние 10 записей:*\n\n"
    for i, exp in enumerate(expenses, 1):
        amount, category, username, date = exp
        date_obj = datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
        date_str = date_obj.strftime("%d.%m %H:%M")
        emoji = CATEGORY_EMOJI.get(category, '•')
        user_short = username[:15] + "..." if username and len(username) > 15 else username or "Аноним"
        response += f"{i}. {date_str} {emoji} {category}: *{amount:.0f} {CURRENCY}* ({user_short})\n"
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN)

# ===================== ОБРАБОТКА БЮДЖЕТОВ =====================

@dp.callback_query(F.data == "show_budgets", ExpenseStates.waiting_for_budget_category)
async def show_budgets_from_callback(callback: CallbackQuery, state: FSMContext):
    """Показать бюджеты из callback"""
    await callback.answer()
    budgets = get_budgets()
    
    if not budgets:
        await callback.message.edit_text(
            "📊 Бюджеты не установлены",
            reply_markup=get_budget_categories_keyboard()
        )
        return
    
    now = datetime.now()
    _, by_category, _, _ = get_month_expenses(now.year, now.month)
    
    response = f"💰 *Бюджеты на {now.strftime('%B %Y')}:*\n\n"
    
    for category, (limit, _) in budgets.items():
        spent = by_category.get(category, 0)
        emoji = CATEGORY_EMOJI.get(category, '•')
        
        if spent > limit:
            status = "⚠️ ПРЕВЫШЕН!"
        else:
            remaining = limit - spent
            status = f"✅ Осталось: {remaining:.0f} {CURRENCY}"
        
        response += f"{emoji} *{category}*:\n"
        response += f"   Лимит: {limit:.0f} {CURRENCY}\n"
        response += f"   Потрачено: {spent:.0f} {CURRENCY}\n"
        response += f"   {status}\n\n"
    
    await callback.message.edit_text(
        response,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_budget_categories_keyboard()
    )

@dp.callback_query(F.data.startswith('budget_'), ExpenseStates.waiting_for_budget_category)
async def process_budget_category(callback: CallbackQuery, state: FSMContext):
    """Выбор категории для бюджета"""
    await callback.answer()
    category = callback.data.replace('budget_', '')
    
    await state.update_data(budget_category=category)
    
    budgets = get_budgets()
    if category in budgets:
        limit, _ = budgets[category]
        await callback.message.edit_text(
            f"📌 Категория: *{category}*\n"
            f"Текущий бюджет: *{limit:.0f} {CURRENCY}*\n\n"
            f"Введите новую сумму или 0 для удаления:",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await callback.message.edit_text(
            f"📌 Категория: *{category}*\n\n"
            f"Введите сумму бюджета на месяц:",
            parse_mode=ParseMode.MARKDOWN
        )
    
    await state.set_state(ExpenseStates.waiting_for_budget_amount)

@dp.message(ExpenseStates.waiting_for_budget_amount)
async def process_budget_amount(message: Message, state: FSMContext):
    """Установка суммы бюджета"""
    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число")
        return
    
    data = await state.get_data()
    category = data.get('budget_category')
    
    if amount <= 0:
        delete_budget(category)
        await message.answer(
            f"✅ Бюджет для категории *{category}* удалён",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        set_budget(category, amount)
        emoji = CATEGORY_EMOJI.get(category, '•')
        await message.answer(
            f"✅ Бюджет установлен:\n"
            f"{emoji} *{category}*: *{amount:.0f} {CURRENCY}* на текущий месяц",
            parse_mode=ParseMode.MARKDOWN
        )
    
    await state.clear()

# ===================== ОБРАБОТКА РАСХОДОВ =====================

@dp.message(F.text.regexp(r'^-?\d+$'))
async def handle_amount(message: Message, state: FSMContext):
    """Обработка суммы"""
    amount = abs(float(message.text.strip()))
    
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO pending_expenses (user_id, amount)
        VALUES (?, ?)
    ''', (message.from_user.id, amount))
    conn.commit()
    conn.close()
    
    await state.update_data(amount=amount)
    
    await message.answer(
        f"💰 Сумма: *{amount:.0f} {CURRENCY}*\n\n"
        f"📌 Выберите категорию:",
        reply_markup=get_categories_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(ExpenseStates.waiting_for_category)

@dp.callback_query(F.data.startswith('cat_'), ExpenseStates.waiting_for_category)
async def process_category(callback: CallbackQuery, state: FSMContext):
    """Выбор категории"""
    await callback.answer()
    category = callback.data.replace('cat_', '')
    
    data = await state.get_data()
    amount = data.get('amount')
    
    if not amount:
        conn = sqlite3.connect('family_budget.db')
        cursor = conn.cursor()
        cursor.execute('SELECT amount FROM pending_expenses WHERE user_id = ?', 
                      (callback.from_user.id,))
        result = cursor.fetchone()
        conn.close()
        amount = result[0] if result else None
    
    if not amount:
        await callback.message.answer("❌ Ошибка, попробуйте снова")
        await state.clear()
        return
    
    await state.update_data(category=category)
    
    emoji = CATEGORY_EMOJI.get(category, '•')
    await callback.message.edit_text(
        text=(
            f"📝 *Проверьте данные:*\n\n"
            f"💰 Сумма: *{amount:.0f} {CURRENCY}*\n"
            f"{emoji} Категория: *{category}*\n\n"
            f"Всё верно?"
        ),
        reply_markup=get_confirmation_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

@dp.callback_query(F.data == 'confirm', ExpenseStates.waiting_for_category)
async def process_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение расхода"""
    await callback.answer()
    
    data = await state.get_data()
    amount = data.get('amount')
    category = data.get('category')
    
    if not amount or not category:
        await callback.message.answer("❌ Ошибка")
        await state.clear()
        return
    
    username = callback.from_user.username or callback.from_user.full_name
    
    add_expense(callback.from_user.id, username, amount, category)
    
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM pending_expenses WHERE user_id = ?', 
                  (callback.from_user.id,))
    conn.commit()
    conn.close()
    
    budgets = get_budgets()
    if category in budgets:
        limit, _ = budgets[category]
        _, by_category, _, _ = get_month_expenses(datetime.now().year, datetime.now().month)
        spent = by_category.get(category, 0)
        
        if spent > limit:
            emoji = CATEGORY_EMOJI.get(category, '•')
            over = spent - limit
            await callback.message.edit_text(
                text=(
                    f"✅ *Расход сохранён!*\n\n"
                    f"{emoji} {category}: *{amount:.0f} {CURRENCY}*\n"
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
            f"{emoji} {category}: *{amount:.0f} {CURRENCY}*\n"
            f"👤 {username}"
        ),
        parse_mode=ParseMode.MARKDOWN
    )
    
    await state.clear()

@dp.callback_query(F.data == 'edit_amount', ExpenseStates.waiting_for_category)
async def process_edit_amount(callback: CallbackQuery, state: FSMContext):
    """Редактирование суммы"""
    await callback.answer()
    await callback.message.edit_text("✏️ Введите новую сумму:")
    await state.set_state(ExpenseStates.edit_amount)

@dp.callback_query(F.data == 'edit_category', ExpenseStates.waiting_for_category)
async def process_edit_category(callback: CallbackQuery, state: FSMContext):
    """Изменение категории"""
    await callback.answer()
    await callback.message.edit_text(
        "📌 Выберите новую категорию:",
        reply_markup=get_categories_keyboard()
    )

@dp.callback_query(F.data == 'cancel')
async def process_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена"""
    await callback.answer()
    
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM pending_expenses WHERE user_id = ?', 
                  (callback.from_user.id,))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text("❌ Операция отменена")
    await state.clear()

@dp.message(ExpenseStates.edit_amount)
async def process_new_amount(message: Message, state: FSMContext):
    """Новая сумма после редактирования"""
    try:
        amount = abs(float(message.text.strip()))
    except ValueError:
        await message.answer("❌ Введите число")
        return
    
    conn = sqlite3.connect('family_budget.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO pending_expenses (user_id, amount)
        VALUES (?, ?)
    ''', (message.from_user.id, amount))
    conn.commit()
    conn.close()
    
    await state.update_data(amount=amount)
    
    data = await state.get_data()
    category = data.get('category')
    
    if category:
        emoji = CATEGORY_EMOJI.get(category, '•')
        await message.answer(
            text=(
                f"📝 *Проверьте данные:*\n\n"
                f"💰 Сумма: *{amount:.0f} {CURRENCY}*\n"
                f"{emoji} Категория: *{category}*\n\n"
                f"Всё верно?"
            ),
            reply_markup=get_confirmation_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
        await state.set_state(ExpenseStates.waiting_for_category)
    else:
        await message.answer(
            f"💰 Сумма: *{amount:.0f} {CURRENCY}*\n\n"
            f"📌 Выберите категорию:",
            reply_markup=get_categories_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
        await state.set_state(ExpenseStates.waiting_for_category)

# ===================== ЗАПУСК =====================

async def main():
    init_db()
    asyncio.create_task(check_budgets())
    
    print("🤖 Семейный бюджет бот запущен!")
    print(f"💰 Валюта: {CURRENCY}")
    print(f"📊 Категорий: {len(CATEGORIES)}")
    print("📋 Команды: /start, /budget, /budgets, /today, /week, /month, /last, /categories")
    
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())