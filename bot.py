import logging
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from db import (
    get_tools, get_issued_tools, create_tool_request, 
    approve_issue_request, get_issue_request_info, get_tool_by_id,
    get_issued_tool_by_id, update_tool_status, add_tool_history,
    get_tool_history, get_overdue_tools, get_all_issue_requests,
    create_tables, get_return_info, complete_return, DatabaseConnection
)
from config import API_TOKEN, WEBHOOK_HOST, WEBHOOK_PATH, WEBAPP_HOST, WEBAPP_PORT
from datetime import datetime
import os
from aiohttp import web
from populate_database import populate_database
import time
from aiogram.dispatcher.filters import Text
from datetime import timedelta

# Инициализация бота и диспетчера
TOKEN = API_TOKEN
ADMIN_ID = 1495719377  # ID администратора

# Инициализация хранилища состояний
storage = MemoryStorage()

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher(bot, storage=storage)

# Устанавливаем экземпляр бота как текущий
Bot.set_current(bot)
Dispatcher.set_current(dp)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Регистрируем логирование
dp.middleware.setup(LoggingMiddleware())

# Проверяем содержимое базы данных при запуске
logger.info("Проверка базы данных при запуске...")
tools = get_tools()
logger.info(f"Количество инструментов в базе: {len(tools)}")
if tools:
    logger.info("Примеры инструментов:")
    for tool in tools[:5]:  # Показываем первые 5 инструментов
        logger.info(f"- {tool}")
else:
    logger.info("База данных пуста, заполняем начальными данными...")
    populate_database()
    tools = get_tools()
    logger.info(f"После заполнения в базе {len(tools)} инструментов")
    if tools:
        logger.info("Примеры добавленных инструментов:")
        for tool in tools[:5]:
            logger.info(f"- {tool}")

# Создание таблиц
create_tables()

# Состояния для возврата
class ToolReturnState(StatesGroup):
    waiting_for_tool_id = State()
    waiting_for_photo = State()
    waiting_for_confirmation = State()

# Состояния для поиска
class SearchState(StatesGroup):
    waiting_for_query = State()

# Состояния для выдачи
class ToolIssueState(StatesGroup):
    waiting_for_fullname = State()

# Состояния для процесса возврата инструмента
class ReturnToolStates(StatesGroup):
    waiting_for_photo = State()

# Количество инструментов на странице
TOOLS_PER_PAGE = 10

# Словарь для отслеживания последних callback-запросов
_last_callback_time = {}

async def throttle_callback(callback_query: types.CallbackQuery) -> bool:
    """
    Проверяет, не слишком ли часто отправляются callback-запросы.
    Возвращает True, если запрос нужно обработать, False если его нужно пропустить.
    """
    user_id = callback_query.from_user.id
    current_time = time.time()
    
    # Проверяем время последнего запроса
    if user_id in _last_callback_time:
        last_time = _last_callback_time[user_id]
        if current_time - last_time < 1:  # Минимальный интервал между запросами - 1 секунда
            await callback_query.answer("Пожалуйста, не нажимайте кнопки так часто", show_alert=True)
            return False
            
    # Обновляем время последнего запроса
    _last_callback_time[user_id] = current_time
    return True

# Состояния для выдачи инструмента
class ToolIssueState(StatesGroup):
    waiting_for_tool_id = State()
    waiting_for_employee_name = State()
    waiting_for_duration = State()
    waiting_for_confirmation = State()

# Состояния для возврата инструмента
class ToolReturnState(StatesGroup):
    waiting_for_tool_id = State()
    waiting_for_photo = State()
    waiting_for_confirmation = State()

# Фильтр для проверки админа
def is_admin(message: types.Message):
    return str(message.from_user.id) == str(ADMIN_ID)

# Базовые команды
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        types.KeyboardButton('/list'),
        types.KeyboardButton('/search'),
        types.KeyboardButton('/issue'),
        types.KeyboardButton('/return'),
        types.KeyboardButton('/help')
    ]
    keyboard.add(*buttons)
    
    if is_admin(message):
        admin_buttons = [
            types.KeyboardButton('/history'),
            types.KeyboardButton('/report'),
            types.KeyboardButton('/overdue')
        ]
        keyboard.add(*admin_buttons)
    
    await message.answer(
        "👋 Привет! Я бот для управления инструментами.\n"
        "Доступные команды:\n"
        "/list - Список всех инструментов\n"
        "/search - Поиск инструмента\n"
        "/issue - Выдать инструмент\n"
        "/return - Вернуть инструмент\n"
        "/help - Помощь",
        reply_markup=keyboard
    )

async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    help_text = (
        "🔧 Помощь по командам:\n\n"
        "Основные команды:\n"
        "/list - Показать список всех инструментов\n"
        "/search - Поиск инструмента по названию\n"
        "/issue - Выдать инструмент\n"
        "/return - Вернуть инструмент\n"
        "/help - Показать это сообщение\n\n"
    )
    
    if is_admin(message):
        help_text += (
            "Команды администратора:\n"
            "/history - История операций\n"
            "/report - Отчет по инструментам\n"
            "/overdue - Просроченные инструменты\n"
        )
    
    await message.answer(help_text)

async def cancel_handler(message: types.Message, state: FSMContext):
    """Отмена текущей операции"""
    current_state = await state.get_state()
    if current_state is None:
        return
    
    await state.finish()
    await message.answer(
        "❌ Операция отменена.",
        reply_markup=types.ReplyKeyboardRemove()
    )

# Список инструментов
async def cmd_list(message: types.Message):
    """Показать список всех инструментов"""
    with DatabaseConnection() as db:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT t.id, t.name, t.quantity,
                   COALESCE(COUNT(CASE WHEN it.return_date IS NULL THEN 1 END), 0) as issued_count
            FROM tools t
            LEFT JOIN issued_tools it ON t.id = it.tool_id
            GROUP BY t.id, t.name, t.quantity
            ORDER BY t.name
        """)
        tools = cursor.fetchall()
    
    if not tools:
        await message.answer(
            "❌ Список инструментов пуст.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return
    
    result = "📋 *Список всех инструментов:*\n\n"
    for tool_id, name, total_qty, issued_qty in tools:
        available_qty = total_qty - issued_qty
        status = "🟢" if available_qty > 0 else "🔴"
        
        result += f"{status} *{name}*\n"
        result += f"┌ ID: {tool_id}\n"
        result += f"└ Доступно: {available_qty} из {total_qty}\n\n"
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton('/issue'), types.KeyboardButton('/return'))
    keyboard.add(types.KeyboardButton('/search'), types.KeyboardButton('/help'))
    
    await message.answer(
        result,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# Выдача инструмента
async def cmd_issue_start(message: types.Message):
    """Начало процесса выдачи инструмента"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton('/cancel'))
    
    # Получаем список доступных инструментов
    with DatabaseConnection() as db:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT id, name, quantity 
            FROM tools 
            WHERE quantity > 0
        """)
        tools = cursor.fetchall()
    
    if not tools:
        await message.answer(
            "❌ Извините, но сейчас нет доступных инструментов.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return
    
    tools_list = "\n".join([f"ID: {t[0]} - {t[1]} (доступно: {t[2]})" for t in tools])
    await message.answer(
        f"🔧 Доступные инструменты:\n\n{tools_list}\n\n"
        "Введите ID инструмента, который хотите получить:",
        reply_markup=keyboard
    )
    await ToolIssueState.waiting_for_tool_id.set()

async def process_tool_id(message: types.Message, state: FSMContext):
    """Обработка ID инструмента"""
    try:
        tool_id = int(message.text)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите числовой ID инструмента.")
        return
    
    with DatabaseConnection() as db:
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT name, quantity FROM tools WHERE id = ?",
            (tool_id,)
        )
        tool = cursor.fetchone()
    
    if not tool or tool[1] <= 0:
        await message.answer("❌ Инструмент не найден или недоступен.")
        return
    
    await state.update_data(tool_id=tool_id, tool_name=tool[0])
    await message.answer(
        f"👤 Вы выбрали: {tool[0]}\n"
        "Введите имя сотрудника:"
    )
    await ToolIssueState.waiting_for_employee_name.set()

async def process_employee_name(message: types.Message, state: FSMContext):
    """Обработка имени сотрудника"""
    employee_name = message.text
    if len(employee_name) < 2:
        await message.answer("❌ Имя сотрудника слишком короткое.")
        return
    
    await state.update_data(employee_name=employee_name)
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = ['1 день', '3 дня', '7 дней', '14 дней', '/cancel']
    keyboard.add(*buttons)
    
    await message.answer(
        "⏳ На какой срок выдать инструмент?",
        reply_markup=keyboard
    )
    await ToolIssueState.waiting_for_duration.set()

async def process_duration(message: types.Message, state: FSMContext):
    """Обработка срока выдачи"""
    duration_text = message.text.lower()
    duration_map = {
        '1 день': 1,
        '3 дня': 3,
        '7 дней': 7,
        '14 дней': 14
    }
    
    if duration_text not in duration_map:
        await message.answer(
            "❌ Пожалуйста, выберите срок из предложенных вариантов."
        )
        return
    
    days = duration_map[duration_text]
    data = await state.get_data()
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add('✅ Подтвердить', '❌ Отменить')
    
    await state.update_data(duration_days=days)
    await message.answer(
        f"📝 Проверьте данные:\n\n"
        f"🔧 Инструмент: {data['tool_name']}\n"
        f"👤 Сотрудник: {data['employee_name']}\n"
        f"⏳ Срок: {days} дней\n\n"
        "Всё верно?",
        reply_markup=keyboard
    )
    await ToolIssueState.waiting_for_confirmation.set()

async def process_issue_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения выдачи"""
    if message.text != '✅ Подтвердить':
        await state.finish()
        await message.answer(
            "❌ Операция отменена.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return
    
    data = await state.get_data()
    tool_id = data['tool_id']
    employee_name = data['employee_name']
    duration_days = data['duration_days']
    
    issue_date = datetime.now()
    return_date = issue_date + timedelta(days=duration_days)
    
    try:
        with DatabaseConnection() as db:
            cursor = db.conn.cursor()
            
            # Проверяем доступность инструмента
            cursor.execute(
                "SELECT quantity FROM tools WHERE id = ?",
                (tool_id,)
            )
            quantity = cursor.fetchone()[0]
            
            if quantity <= 0:
                raise ValueError("Инструмент больше не доступен")
            
            # Обновляем количество
            cursor.execute(
                "UPDATE tools SET quantity = quantity - 1 WHERE id = ?",
                (tool_id,)
            )
            
            # Добавляем запись о выдаче
            cursor.execute("""
                INSERT INTO issued_tools 
                (tool_id, employee_name, issue_date, expected_return_date)
                VALUES (?, ?, ?, ?)
            """, (tool_id, employee_name, issue_date, return_date))
            
            # Добавляем запись в историю
            cursor.execute("""
                INSERT INTO tool_history 
                (tool_id, action, employee_name, timestamp)
                VALUES (?, 'issued', ?, ?)
            """, (tool_id, employee_name, issue_date))
            
            db.conn.commit()
            
            # Уведомляем админа
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"📢 Выдан инструмент:\n"
                    f"🔧 {data['tool_name']}\n"
                    f"👤 Сотрудник: {employee_name}\n"
                    f"📅 Дата возврата: {return_date.strftime('%d.%m.%Y')}"
                )
            
            await message.answer(
                f"✅ Инструмент успешно выдан!\n\n"
                f"🔧 {data['tool_name']}\n"
                f"📅 Дата возврата: {return_date.strftime('%d.%m.%Y')}\n\n"
                "Пожалуйста, верните инструмент вовремя.",
                reply_markup=types.ReplyKeyboardRemove()
            )
            
    except Exception as e:
        logger.error(f"Error issuing tool: {e}")
        await message.answer(
            "❌ Произошла ошибка при выдаче инструмента. "
            "Пожалуйста, попробуйте позже.",
            reply_markup=types.ReplyKeyboardRemove()
        )
    
    finally:
        await state.finish()

# Возврат инструмента
async def cmd_return_start(message: types.Message):
    """Начало процесса возврата инструмента"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton('/cancel'))
    
    # Получаем список выданных инструментов
    with DatabaseConnection() as db:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT t.id, t.name, it.employee_name, it.issue_date 
            FROM tools t
            JOIN issued_tools it ON t.id = it.tool_id
            WHERE it.return_date IS NULL
        """)
        tools = cursor.fetchall()
    
    if not tools:
        await message.answer(
            "❌ Нет выданных инструментов для возврата.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return
    
    tools_list = "\n".join([
        f"ID: {t[0]} - {t[1]} (выдан: {t[2]} {t[3].split()[0]})" 
        for t in tools
    ])
    
    await message.answer(
        f"🔧 Выданные инструменты:\n\n{tools_list}\n\n"
        "Введите ID инструмента для возврата:",
        reply_markup=keyboard
    )
    await ToolReturnState.waiting_for_tool_id.set()

async def process_return_tool_id(message: types.Message, state: FSMContext):
    """Обработка ID инструмента для возврата"""
    try:
        tool_id = int(message.text)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите числовой ID инструмента.")
        return
    
    with DatabaseConnection() as db:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT t.name, it.employee_name, it.issue_date
            FROM tools t
            JOIN issued_tools it ON t.id = it.tool_id
            WHERE t.id = ? AND it.return_date IS NULL
        """, (tool_id,))
        tool = cursor.fetchone()
    
    if not tool:
        await message.answer(
            "❌ Инструмент не найден или уже возвращен."
        )
        return
    
    await state.update_data(tool_id=tool_id, tool_name=tool[0])
    await message.answer(
        f"📸 Для возврата инструмента *{tool[0]}* отправьте его фотографию.\n\n"
        "Фото должно быть четким и показывать состояние инструмента.",
        parse_mode="Markdown"
    )
    await ToolReturnState.waiting_for_photo.set()

async def process_return_photo(message: types.Message, state: FSMContext):
    """Обработка фото возвращаемого инструмента"""
    if not message.photo:
        await message.answer(
            "❌ Пожалуйста, отправьте фотографию инструмента."
        )
        return
    
    data = await state.get_data()
    photo = message.photo[-1]  # Берем самую большую версию фото
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add('✅ Подтвердить', '❌ Отменить')
    
    await message.answer(
        f"📝 Проверьте данные возврата:\n\n"
        f"🔧 Инструмент: {data['tool_name']}\n"
        "Фото получено ✅\n\n"
        "Всё верно?",
        reply_markup=keyboard
    )
    
    # Сохраняем ID фото в состоянии
    await state.update_data(photo_id=photo.file_id)
    await ToolReturnState.waiting_for_confirmation.set()

async def process_return_confirmation(message: types.Message, state: FSMContext):
    """Обработка подтверждения возврата"""
    if message.text != '✅ Подтвердить':
        await state.finish()
        await message.answer(
            "❌ Возврат отменен.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return
    
    data = await state.get_data()
    tool_id = data['tool_id']
    photo_id = data['photo_id']
    
    try:
        with DatabaseConnection() as db:
            cursor = db.conn.cursor()
            
            # Получаем информацию о выдаче
            cursor.execute("""
                SELECT employee_name, issue_date 
                FROM issued_tools 
                WHERE tool_id = ? AND return_date IS NULL
            """, (tool_id,))
            issue_info = cursor.fetchone()
            
            if not issue_info:
                raise ValueError("Инструмент уже возвращен")
            
            employee_name, issue_date = issue_info
            return_date = datetime.now()
            
            # Обновляем запись о выдаче
            cursor.execute("""
                UPDATE issued_tools 
                SET return_date = ?, return_photo = ?
                WHERE tool_id = ? AND return_date IS NULL
            """, (return_date, photo_id, tool_id))
            
            # Обновляем количество доступных инструментов
            cursor.execute("""
                UPDATE tools 
                SET quantity = quantity + 1
                WHERE id = ?
            """, (tool_id,))
            
            # Добавляем запись в историю
            cursor.execute("""
                INSERT INTO tool_history 
                (tool_id, action, employee_name, timestamp)
                VALUES (?, 'returned', ?, ?)
            """, (tool_id, employee_name, return_date))
            
            db.conn.commit()
            
            # Уведомляем админа
            if ADMIN_ID:
                await bot.send_photo(
                    ADMIN_ID,
                    photo_id,
                    caption=f"📢 Возвращен инструмент:\n"
                    f"🔧 {data['tool_name']}\n"
                    f"👤 Сотрудник: {employee_name}\n"
                    f"📅 Дата выдачи: {issue_date}\n"
                    f"📅 Дата возврата: {return_date.strftime('%d.%m.%Y')}"
                )
            
            await message.answer(
                f"✅ Инструмент успешно возвращен!\n\n"
                f"🔧 {data['tool_name']}\n"
                f"📅 Дата возврата: {return_date.strftime('%d.%m.%Y')}",
                reply_markup=types.ReplyKeyboardRemove()
            )
            
    except Exception as e:
        logger.error(f"Error returning tool: {e}")
        await message.answer(
            "❌ Произошла ошибка при возврате инструмента. "
            "Пожалуйста, попробуйте позже.",
            reply_markup=types.ReplyKeyboardRemove()
        )
    
    finally:
        await state.finish()

# Поиск инструментов
async def cmd_search_start(message: types.Message):
    """Начало процесса поиска инструмента"""
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton('/cancel'))
    
    await message.answer(
        "🔍 Введите название или часть названия инструмента:",
        reply_markup=keyboard
    )
    await SearchState.waiting_for_query.set()

async def process_search_query(message: types.Message, state: FSMContext):
    """Обработка поискового запроса"""
    search_query = message.text.lower()
    
    with DatabaseConnection() as db:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT t.id, t.name, t.quantity,
                   COALESCE(COUNT(CASE WHEN it.return_date IS NULL THEN 1 END), 0) as issued_count
            FROM tools t
            LEFT JOIN issued_tools it ON t.id = it.tool_id
            WHERE LOWER(t.name) LIKE ?
            GROUP BY t.id, t.name, t.quantity
        """, (f"%{search_query}%",))
        tools = cursor.fetchall()
    
    if not tools:
        await message.answer(
            "❌ Инструменты не найдены.\n"
            "Попробуйте другой поисковый запрос.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.finish()
        return
    
    result = "🔍 *Результаты поиска:*\n\n"
    for tool_id, name, total_qty, issued_qty in tools:
        available_qty = total_qty - issued_qty
        status = "🟢" if available_qty > 0 else "🔴"
        
        result += f"{status} *{name}*\n"
        result += f"┌ ID: {tool_id}\n"
        result += f"└ Доступно: {available_qty} из {total_qty}\n\n"
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton('/issue'), types.KeyboardButton('/search'))
    keyboard.add(types.KeyboardButton('/help'))
    
    await message.answer(
        result,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await state.finish()

# Админские команды
async def cmd_history(message: types.Message):
    """Показать историю операций (только для админа)"""
    if not is_admin(message):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    
    with DatabaseConnection() as db:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT t.name, th.action, th.employee_name, th.timestamp
            FROM tool_history th
            JOIN tools t ON th.tool_id = t.id
            ORDER BY th.timestamp DESC
            LIMIT 20
        """)
        history = cursor.fetchall()
    
    if not history:
        await message.answer("📜 История операций пуста.")
        return
    
    result = "📜 *Последние операции:*\n\n"
    for name, action, employee, timestamp in history:
        action_emoji = "📥" if action == "issued" else "📤"
        result += f"{action_emoji} *{name}*\n"
        result += f"┌ Действие: {action}\n"
        result += f"├ Сотрудник: {employee}\n"
        result += f"└ Дата: {timestamp.split()[0]}\n\n"
    
    await message.answer(result, parse_mode="Markdown")

async def cmd_report(message: types.Message):
    """Показать отчет по инструментам (только для админа)"""
    if not is_admin(message):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    
    with DatabaseConnection() as db:
        cursor = db.conn.cursor()
        
        # Общая статистика
        cursor.execute("""
            SELECT 
                COUNT(*) as total_tools,
                SUM(quantity) as total_quantity,
                (SELECT COUNT(*) FROM issued_tools WHERE return_date IS NULL) as issued_count
            FROM tools
        """)
        stats = cursor.fetchone()
        
        # Топ выдаваемых инструментов
        cursor.execute("""
            SELECT t.name, COUNT(*) as issue_count
            FROM issued_tools it
            JOIN tools t ON it.tool_id = t.id
            GROUP BY t.id, t.name
            ORDER BY issue_count DESC
            LIMIT 5
        """)
        top_tools = cursor.fetchall()
        
        # Просроченные инструменты
        cursor.execute("""
            SELECT t.name, it.employee_name, it.expected_return_date
            FROM issued_tools it
            JOIN tools t ON it.tool_id = t.id
            WHERE it.return_date IS NULL 
            AND it.expected_return_date < date('now')
        """)
        overdue = cursor.fetchall()
    
    result = "📊 *Отчет по инструментам*\n\n"
    
    # Общая статистика
    result += "*Общая статистика:*\n"
    result += f"┌ Всего типов инструментов: {stats[0]}\n"
    result += f"├ Общее количество: {stats[1]}\n"
    result += f"└ Сейчас выдано: {stats[2]}\n\n"
    
    # Топ инструментов
    if top_tools:
        result += "*Самые популярные инструменты:*\n"
        for name, count in top_tools:
            result += f"• {name}: {count} раз(а)\n"
        result += "\n"
    
    # Просроченные
    if overdue:
        result += "⚠️ *Просроченные инструменты:*\n"
        for name, employee, date in overdue:
            result += f"┌ {name}\n"
            result += f"├ Сотрудник: {employee}\n"
            result += f"└ Дата возврата: {date}\n\n"
    
    await message.answer(result, parse_mode="Markdown")

async def cmd_overdue(message: types.Message):
    """Показать просроченные инструменты (только для админа)"""
    if not is_admin(message):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    
    with DatabaseConnection() as db:
        cursor = db.conn.cursor()
        cursor.execute("""
            SELECT 
                t.name,
                it.employee_name,
                it.issue_date,
                it.expected_return_date,
                julianday('now') - julianday(it.expected_return_date) as days_overdue
            FROM issued_tools it
            JOIN tools t ON it.tool_id = t.id
            WHERE it.return_date IS NULL 
            AND it.expected_return_date < date('now')
            ORDER BY days_overdue DESC
        """)
        overdue = cursor.fetchall()
    
    if not overdue:
        await message.answer("✅ Нет просроченных инструментов.")
        return
    
    result = "⚠️ *Просроченные инструменты:*\n\n"
    for name, employee, issue_date, expected_date, days in overdue:
        result += f"🔧 *{name}*\n"
        result += f"┌ Сотрудник: {employee}\n"
        result += f"├ Выдан: {issue_date.split()[0]}\n"
        result += f"├ Ожидался: {expected_date}\n"
        result += f"└ Просрочка: {int(days)} дней\n\n"
    
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton('/report'))
    
    await message.answer(
        result,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# Регистрируем обработчики
def register_handlers(dp: Dispatcher):
    """Регистрация всех обработчиков команд"""
    # Базовые команды
    dp.register_message_handler(cmd_start, commands=['start'])
    dp.register_message_handler(cmd_help, commands=['help'])
    dp.register_message_handler(cmd_list, commands=['list'])
    dp.register_message_handler(cancel_handler, commands=['cancel'], state='*')
    
    # Выдача инструмента
    dp.register_message_handler(cmd_issue_start, commands=['issue'])
    dp.register_message_handler(process_tool_id, state=ToolIssueState.waiting_for_tool_id)
    dp.register_message_handler(process_employee_name, state=ToolIssueState.waiting_for_employee_name)
    dp.register_message_handler(process_duration, state=ToolIssueState.waiting_for_duration)
    dp.register_message_handler(process_issue_confirmation, state=ToolIssueState.waiting_for_confirmation)
    
    # Возврат инструмента
    dp.register_message_handler(cmd_return_start, commands=['return'])
    dp.register_message_handler(process_return_tool_id, state=ToolReturnState.waiting_for_tool_id)
    dp.register_message_handler(process_return_photo, state=ToolReturnState.waiting_for_photo, content_types=['photo'])
    dp.register_message_handler(process_return_confirmation, state=ToolReturnState.waiting_for_confirmation)
    
    # Поиск инструментов
    dp.register_message_handler(cmd_search_start, commands=['search'])
    dp.register_message_handler(process_search_query, state=SearchState.waiting_for_query)
    
    # Админские команды
    dp.register_message_handler(cmd_history, commands=['history'])
    dp.register_message_handler(cmd_report, commands=['report'])
    dp.register_message_handler(cmd_overdue, commands=['overdue'])

# Инициализация и запуск
async def on_startup(app):
    """Действия при запуске бота"""
    # Создаем таблицы в БД
    create_tables()
    
    # Устанавливаем вебхук
    webhook_url = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url)
    logger.info(f"Webhook установлен: {webhook_url}")

async def on_shutdown(app):
    """Действия при остановке бота"""
    # Удаляем вебхук
    await bot.delete_webhook()
    logger.info("Webhook удален")

def main():
    """Основная функция запуска бота"""
    try:
        # Регистрируем все обработчики
        register_handlers(dp)
        
        # Настраиваем маршруты
        app = web.Application()
        app.router.add_post(WEBHOOK_PATH, handle_webhook)
        app.router.add_get("/health", health_check)
        
        # Устанавливаем обработчики запуска/остановки
        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)
        
        # Запускаем веб-сервер
        web.run_app(app, host=WEBAPP_HOST, port=WEBAPP_PORT)
        
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise

if __name__ == "__main__":
    main()