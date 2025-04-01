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
from config import API_TOKEN
from datetime import datetime
import os
from aiohttp import web
from populate_database import populate_database
import time

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
TOKEN = API_TOKEN
ADMIN_ID = 1495719377  # ID администратора

storage = MemoryStorage()
bot = Bot(token=TOKEN)
dp = Dispatcher(bot, storage=storage)

# Устанавливаем экземпляр бота как текущий
Bot.set_current(bot)
Dispatcher.set_current(dp)

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

# Регистрируем логирование
dp.middleware.setup(LoggingMiddleware())

# Состояния для возврата
class ToolReturnState(StatesGroup):
    waiting_for_tool_id = State()
    waiting_for_photo = State()

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

# Старт
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    keyboard = InlineKeyboardMarkup(row_width=2)
    
    # Обычные кнопки для всех пользователей
    keyboard.add(
        InlineKeyboardButton("🛠️ Инструменты", callback_data="tools"),
        InlineKeyboardButton("🔍 Поиск", callback_data="search_tools")
    )
    keyboard.add(
        InlineKeyboardButton("📸 Вернуть", callback_data="return"),
        InlineKeyboardButton("ℹ️ Помощь", callback_data="help")
    )
    
    # Добавляем кнопки админ-панели, если это администратор
    if message.from_user.id == ADMIN_ID:
        keyboard.add(
            InlineKeyboardButton("📋 Выданные", callback_data="admin_issued"),
            InlineKeyboardButton("📊 Отчёт", callback_data="admin_report")
        )
        keyboard.add(
            InlineKeyboardButton("📜 История", callback_data="admin_history"),
            InlineKeyboardButton("⚠️ Просрочки", callback_data="admin_overdue")
        )

    welcome_text = (
        f"👋 *Здравствуйте, {message.from_user.first_name}!*\n\n"
        "🤖 Я бот для учёта инструментов.\n"
        "Выберите нужное действие:\n\n"
        "┌ 🛠️ *Инструменты* - список всех инструментов\n"
        "├ 🔍 *Поиск* - поиск по названию\n"
        "├ 📸 *Вернуть* - возврат инструмента\n"
        "└ ℹ️ *Помощь* - справка по работе\n"
    )

    # Добавляем информацию об админ-функциях для администратора
    if message.from_user.id == ADMIN_ID:
        welcome_text += "\n*Функции администратора:*\n\n"
        welcome_text += "┌ 📋 *Выданные* - список выданных\n"
        welcome_text += "├ 📊 *Отчёт* - общая статистика\n"
        welcome_text += "├ 📜 *История* - история операций\n"
        welcome_text += "└ ⚠️ *Просрочки* - просроченные\n"

    await message.reply(
        welcome_text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query_handler(lambda c: c.data == "help")
async def show_help(callback_query: types.CallbackQuery):
    help_text = (
        "ℹ️ *Как пользоваться ботом*\n\n"
        "*1. Просмотр инструментов*\n"
        "┌ Нажмите кнопку 🛠️ *Инструменты*\n"
        "└ Увидите список всех инструментов и их статус\n\n"
        "*2. Поиск инструмента*\n"
        "┌ Нажмите кнопку 🔍 *Поиск*\n"
        "└ Введите название или часть названия\n\n"
        "*3. Возврат инструмента*\n"
        "┌ Нажмите кнопку 📸 *Вернуть*\n"
        "├ Выберите инструмент из списка\n"
        "└ Отправьте фото инструмента\n\n"
        "💡 *Дополнительно*\n"
        "• Зелёный статус 🟢 - инструмент доступен\n"
        "• Красный статус 🔴 - инструмент выдан\n"
    )

    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("🛠️ К инструментам", callback_data="tools"),
        InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
    )

    await callback_query.message.reply(help_text, reply_markup=keyboard, parse_mode="Markdown")
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "main_menu")
async def main_menu(callback_query: types.CallbackQuery):
    try:
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("🛠️ Инструменты", callback_data="tools"),
            InlineKeyboardButton("🔍 Поиск", callback_data="search_tools")
        )
        keyboard.add(
            InlineKeyboardButton("📸 Вернуть", callback_data="return"),
            InlineKeyboardButton("ℹ️ Помощь", callback_data="help")
        )
        
        if callback_query.from_user.id == ADMIN_ID:
            keyboard.add(
                InlineKeyboardButton("📋 Выданные", callback_data="admin_issued"),
                InlineKeyboardButton("📊 Отчёт", callback_data="admin_report")
            )
            keyboard.add(
                InlineKeyboardButton("📜 История", callback_data="admin_history"),
                InlineKeyboardButton("⚠️ Просрочки", callback_data="admin_overdue")
            )

        menu_text = (
            "🤖 *Главное меню*\n\n"
            "Выберите нужное действие:\n\n"
            "┌ 🛠️ *Инструменты* - список всех инструментов\n"
            "├ 🔍 *Поиск* - поиск по названию\n"
            "├ 📸 *Вернуть* - возврат инструмента\n"
            "└ ℹ️ *Помощь* - справка по работе\n"
        )

        # Добавляем информацию об админ-функциях для администратора
        if callback_query.from_user.id == ADMIN_ID:
            menu_text += "\n*Функции администратора:*\n\n"
            menu_text += "┌ 📋 *Выданные* - список выданных\n"
            menu_text += "├ 📊 *Отчёт* - общая статистика\n"
            menu_text += "├ 📜 *История* - история операций\n"
            menu_text += "└ ⚠️ *Просрочки* - просроченные\n"

        await callback_query.message.reply(menu_text, reply_markup=keyboard, parse_mode="Markdown")
        
        try:
            await callback_query.answer()
        except:
            logging.warning("Не удалось ответить на callback_query (возможно, истек срок действия)")
    except Exception as e:
        logging.error(f"Ошибка в main_menu: {e}")
        try:
            await callback_query.answer("Произошла ошибка. Попробуйте еще раз.")
        except:
            pass

@dp.callback_query_handler(lambda c: c.data == "tools" or c.data.startswith("tools_page_"))
async def show_tools(callback_query: types.CallbackQuery):
    # Сразу отвечаем на callback
    await callback_query.answer()
    
    # Определяем текущую страницу
    if callback_query.data == "tools":
        current_page = 0
    else:
        current_page = int(callback_query.data.split("_")[2])
    
    logger.info(f"DEBUG: Вызвана функция show_tools с callback_data={callback_query.data}")
    tools = get_tools()
    logger.info(f"DEBUG: Получены инструменты: {tools}")
    
    # Группируем инструменты по названию и считаем количество
    grouped_tools = {}
    for tool_id, name, status, quantity in tools:
        if name not in grouped_tools:
            grouped_tools[name] = {
                'ids': [],
                'total_qty': 0,
                'available_qty': 0
            }
        grouped_tools[name]['ids'].append(tool_id)
        grouped_tools[name]['total_qty'] += quantity
        
    # Считаем доступное количество для каждого типа инструмента
    issued_tools = get_issued_tools()
    for name, info in grouped_tools.items():
        issued_count = len([t for t in issued_tools if t[1] in info['ids']])
        info['available_qty'] = info['total_qty'] - issued_count
    
    # Разбиваем сгруппированные инструменты на страницы
    sorted_tools = sorted(grouped_tools.items())
    total_pages = (len(sorted_tools) + TOOLS_PER_PAGE - 1) // TOOLS_PER_PAGE
    start_idx = current_page * TOOLS_PER_PAGE
    end_idx = min(start_idx + TOOLS_PER_PAGE, len(sorted_tools))
    current_tools = sorted_tools[start_idx:end_idx]
    
    response = f"🛠️ *Список инструментов* (стр. {current_page + 1}/{total_pages})\n\n"
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for name, info in current_tools:
        status_text = f"🟢 Доступно: {info['available_qty']} из {info['total_qty']}" if info['available_qty'] > 0 else "🔴 Нет в наличии"
        
        response += f"┌ *{name}*\n"
        response += f"└ Статус: {status_text}\n\n"
        
        # Добавляем кнопку выбора только для доступных инструментов
        if info['available_qty'] > 0:
            keyboard.add(
                InlineKeyboardButton(
                    f"📥 Взять {name}", 
                    callback_data=f"select_tool_{info['ids'][0]}"
                )
            )
    
    # Добавляем кнопки навигации
    nav_buttons = []
    if current_page > 0:
        nav_buttons.append(
            InlineKeyboardButton("⬅️ Назад", callback_data=f"tools_page_{current_page - 1}")
        )
    if current_page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton("➡️ Вперед", callback_data=f"tools_page_{current_page + 1}")
        )
    if nav_buttons:
        keyboard.row(*nav_buttons)
    
    keyboard.add(
        InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
    )
    
    await callback_query.message.reply(response, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data.startswith("select_tool_"))
async def select_tool(callback_query: types.CallbackQuery):
    try:
        tool_id = int(callback_query.data.split("_")[2])
        
        # Получаем состояние для текущего пользователя
        state = dp.current_state(user=callback_query.from_user.id)
        
        # Сохраняем ID инструмента в состоянии
        await state.update_data(tool_id=tool_id)
        
        # Переходим в состояние ожидания ФИО
        await state.set_state(ToolIssueState.waiting_for_fullname.state)
        
        await callback_query.message.answer(
            "👤 *Получение инструмента*\n\n"
            "Пожалуйста, введите ваше ФИО:",
            parse_mode="Markdown"
        )
        
        # Отвечаем на callback_query, чтобы убрать часики
        try:
            await callback_query.answer()
        except:
            logging.warning("Не удалось ответить на callback_query (возможно, истек срок действия)")
    except Exception as e:
        logging.error(f"Ошибка в select_tool: {e}")
        try:
            await callback_query.answer("Произошла ошибка. Попробуйте еще раз.")
        except:
            pass

@dp.message_handler(state=ToolIssueState.waiting_for_fullname)
async def process_employee_fullname(message: types.Message):
    # Получаем состояние для текущего пользователя
    state = dp.current_state(user=message.from_user.id)
    
    employee_fullname = message.text.strip()
    
    # Получаем данные из состояния
    data = await state.get_data()
    tool_id = data.get('tool_id')
    
    # Сбрасываем состояние
    await state.finish()
    
    # Создаем запрос на выдачу инструмента
    if create_tool_request(tool_id, employee_fullname, message.chat.id):
        # Получаем информацию об инструменте
        tools = get_tools()
        tool_name = next((tool[1] for tool in tools if tool[0] == tool_id), "Неизвестный инструмент")
        
        # Отправляем сообщение сотруднику
        await message.answer(
            "✅ Ваш запрос на получение инструмента отправлен администратору.\n"
            f"*Инструмент:* {tool_name}\n"
            f"*ФИО:* {employee_fullname}\n\n"
            "Ожидайте подтверждения.",
            parse_mode="Markdown"
        )
        
        # Создаем клавиатуру для админа
        admin_keyboard = InlineKeyboardMarkup(row_width=2)
        admin_keyboard.add(
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{tool_id}_{message.chat.id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{tool_id}_{message.chat.id}")
        )
        
        # Отправляем уведомление админу
        await bot.send_message(
            ADMIN_ID,
            f"📝 *Новый запрос на получение инструмента*\n\n"
            f"*Инструмент:* {tool_name}\n"
            f"*Сотрудник:* {employee_fullname}\n"
            f"*Чат ID:* {message.chat.id}",
            reply_markup=admin_keyboard,
            parse_mode="Markdown"
        )
    else:
        await message.answer("❌ Произошла ошибка при создании запроса. Попробуйте позже.")

@dp.callback_query_handler(lambda c: c.data.startswith(('approve_', 'reject_')))
async def process_admin_issue_response(callback_query: types.CallbackQuery):
    try:
        logging.info(f"DEBUG: Получен callback для одобрения выдачи: {callback_query.data}")
        # Parse callback data
        data = callback_query.data.split('_')
        if len(data) != 3:
            logging.error(f"DEBUG: Неверный формат callback data: {callback_query.data}")
            await callback_query.answer("❌ Ошибка: неверный формат данных")
            return
            
        action = data[0]  # approve или reject
        tool_id = int(data[1])
        user_id = int(data[2])
        
        # Получаем информацию о запросе
        db = DatabaseConnection()
        with db.connection:
            cursor = db.connection.cursor()
            
            try:
                # Проверяем существование запроса
                cursor.execute('''
                    SELECT id, employee_name 
                    FROM issue_requests 
                    WHERE tool_id = ? AND chat_id = ? AND status = "pending"
                ''', (tool_id, user_id))
                request = cursor.fetchone()
                
                if not request:
                    logging.error(f"DEBUG: Запрос на выдачу не найден для tool_id={tool_id}, user_id={user_id}")
                    await callback_query.answer("❌ Ошибка: запрос не найден или уже обработан")
                    return
                    
                request_id, employee_name = request
                
                if action == "approve":
                    # Обновляем статус инструмента
                    cursor.execute('UPDATE tools SET status = "issued" WHERE id = ?', (tool_id,))
                    
                    # Создаем запись о выдаче
                    cursor.execute('''
                        INSERT INTO issued_tools (tool_id, employee_name, issue_date, expected_return_date)
                        VALUES (?, ?, CURRENT_TIMESTAMP, date('now', '+7 days'))
                    ''', (tool_id, employee_name))
                    
                    # Получаем id созданной записи
                    issue_id = cursor.lastrowid
                    
                    # Обновляем статус запроса
                    cursor.execute('UPDATE issue_requests SET status = "approved" WHERE id = ?', (request_id,))
                    
                    # Добавляем запись в историю
                    cursor.execute('''
                        INSERT INTO tool_history (tool_id, action, employee_name, timestamp)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ''', (tool_id, 'issued', employee_name))
                    
                    db.connection.commit()
                    
                    # Получаем название инструмента
                    cursor.execute('SELECT name FROM tools WHERE id = ?', (tool_id,))
                    tool_name = cursor.fetchone()[0]
                    
                    # Уведомляем пользователя
                    await bot.send_message(
                        user_id,
                        f"✅ Запрос на получение инструмента одобрен!\n\n"
                        f"🔧 Инструмент: {tool_name}\n"
                        f"📝 Номер выдачи: {issue_id}\n"
                        f"⏳ Ожидаемая дата возврата: через 7 дней\n\n"
                        f"❗️ Пожалуйста, верните инструмент вовремя",
                        reply_markup=InlineKeyboardMarkup().add(
                            InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                        )
                    )
                    
                    # Обновляем сообщение админа
                    await callback_query.message.edit_text(
                        f"{callback_query.message.text}\n\n"
                        f"✅ Одобрено",
                        reply_markup=None
                    )
                elif action == "reject":
                    # Обновляем статус запроса
                    cursor.execute('UPDATE issue_requests SET status = "rejected" WHERE id = ?', (request_id,))
                    
                    db.connection.commit()
                    
                    # Уведомляем пользователя
                    await bot.send_message(
                        user_id,
                        "❌ Ваш запрос на получение инструмента отклонен."
                    )
                    
                    # Обновляем сообщение админа
                    await callback_query.message.edit_text(
                        f"{callback_query.message.text}\n\n"
                        "❌ Отклонено",
                        reply_markup=None
                    )
            except Exception as e:
                db.connection.rollback()
                logging.error(f"Ошибка при обновлении БД: {e}")
                await callback_query.answer("❌ Ошибка при обновлении базы данных")
                return
            finally:
                cursor.close()
                
    except Exception as e:
        logging.error(f"Ошибка при обработке одобрения: {e}")
        await callback_query.answer("❌ Произошла ошибка при обработке запроса")
        
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "search_tools")
async def search_tools_start(callback_query: types.CallbackQuery):
    # Сразу отвечаем на callback
    await callback_query.answer()
    await SearchState.waiting_for_query.set()
    await callback_query.message.reply("🔍 Введите название инструмента:")

@dp.message_handler(state=SearchState.waiting_for_query)
async def process_search(message: types.Message, state: FSMContext):
    query = message.text.lower()
    tools = get_tools()
    
    found_tools = []
    for tool in tools:
        if query in tool[1].lower():
            found_tools.append(tool)
    
    if not found_tools:
        await message.reply("❌ *Инструменты не найдены*\n\nПопробуйте другой запрос.", parse_mode="Markdown")
    else:
        response = "🔍 *Результаты поиска*\n\n"
        for tool in found_tools:
            status = "🟢 Доступен" if not is_tool_issued(tool[0]) else "🔴 Выдан"
            response += f"┌ *{tool[1]}*\n"
            response += f"└ Статус: {status}\n\n"
        
        keyboard = InlineKeyboardMarkup()
        keyboard.add(
            InlineKeyboardButton("🔄 Новый поиск", callback_data="search_tools"),
            InlineKeyboardButton("📋 Все инструменты", callback_data="tools")
        )
        
        await message.reply(response, reply_markup=keyboard, parse_mode="Markdown")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "return")
async def show_return_menu(callback_query: types.CallbackQuery):
    """Показать меню возврата инструментов"""
    try:
        logger.info("DEBUG: Получение списка выданных инструментов")
        issued_tools = get_issued_tools()
        logger.info(f"DEBUG: Получено {len(issued_tools)} выданных инструментов")

        if not issued_tools:
            await callback_query.message.edit_text(
                "❌ *Нет выданных инструментов*\n\n"
                "У вас нет инструментов для возврата.",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                )
            )
            return

        keyboard = InlineKeyboardMarkup(row_width=1)
        for tool in issued_tools:
            try:
                tool_id, name, employee, issue_date, expected_return = tool
                if issue_date:
                    issue_date = datetime.strptime(issue_date, '%Y-%m-%d').strftime('%d.%m.%Y')
                else:
                    issue_date = "Дата не указана"
                    
                if expected_return:
                    expected_return = datetime.strptime(expected_return, '%Y-%m-%d').strftime('%d.%m.%Y')
                else:
                    expected_return = "Дата не указана"
                
                button_text = f"🔧 {name} - {employee} (до {expected_return})"
                keyboard.add(InlineKeyboardButton(button_text, callback_data=f"return_tool_{tool_id}"))
            except Exception as e:
                logger.error(f"Ошибка при обработке инструмента: {e}")
                continue

        keyboard.add(InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu"))

        await callback_query.message.edit_text(
            "*📋 Выберите инструмент для возврата:*\n\n"
            "Нажмите на инструмент, который хотите вернуть.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Ошибка при показе меню возврата: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при получении списка инструментов.\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )

@dp.callback_query_handler(lambda c: c.data.startswith("return_tool_"))
async def return_tool(callback_query: types.CallbackQuery):
    try:
        logging.info(f"DEBUG: Вызвана функция возврата инструмента с callback_data={callback_query.data}")
        tool_id = int(callback_query.data.split('_')[2])
        
        # Получаем информацию о выданном инструменте
        issued_tool = get_issued_tool_by_id(tool_id)
        if not issued_tool:
            logging.error(f"DEBUG: Не найден выданный инструмент с ID {tool_id}")
            await callback_query.message.edit_text(
                "❌ Ошибка: инструмент не найден или уже возвращен",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                )
            )
            await callback_query.answer()
            return

        issue_id, tool_name, employee = issued_tool[0], issued_tool[2], issued_tool[3]

        # Сохраняем информацию в состоянии
        state = dp.current_state(user=callback_query.from_user.id)
        await state.set_state(ReturnToolStates.waiting_for_photo.state)
        async with state.proxy() as data:
            data['issue_id'] = issue_id
            data['tool_name'] = tool_name
            data['employee'] = employee

        # Отправляем сообщение с инструкциями
        await callback_query.message.edit_text(
            f"📸 Для возврата инструмента *{tool_name}* отправьте фотографию:\n\n"
            "✅ На фото должны быть видны:\n"
            "- Общее состояние инструмента\n"
            "- Серийный номер (если есть)\n"
            "- Комплектность\n\n"
            "❗️ Убедитесь, что фото четкое и хорошо освещено",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Отмена", callback_data="cancel_return")
            )
        )
        await callback_query.answer()
        
    except Exception as e:
        logging.error(f"Ошибка при возврате инструмента: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при обработке возврата.\n"
            "Пожалуйста, попробуйте еще раз или обратитесь к администратору.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )
        await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "cancel_return", state=ReturnToolStates.waiting_for_photo)
async def cancel_return(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        logging.info("DEBUG: Отмена возврата инструмента")
        await state.finish()
        await callback_query.message.edit_text(
            "❌ Возврат инструмента отменен",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )
        await callback_query.answer()
        
    except Exception as e:
        logging.error(f"Ошибка при отмене возврата: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при отмене возврата",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_query="main_menu")
            )
        )
        await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('reject_return_'))
async def reject_return(callback_query: types.CallbackQuery):
    try:
        logging.info(f"DEBUG: Получен callback для отклонения возврата: {callback_query.data}")
        # Parse callback data
        data = callback_query.data.split('_')
        if len(data) != 4:
            logging.error(f"DEBUG: Неверный формат callback data: {callback_query.data}")
            await callback_query.answer("❌ Ошибка: неверный формат данных")
            return
            
        issue_id = int(data[2])
        user_id = int(data[3])
        
        # Получаем информацию о возврате
        return_info = get_return_info(issue_id)
        if not return_info:
            logging.error(f"DEBUG: Не найдена информация о возврате с ID {issue_id}")
            await callback_query.answer("❌ Ошибка: информация о возврате не найдена")
            return
            
        # Уведомляем администратора
        await callback_query.message.edit_caption(
            callback_query.message.caption + "\n\n❌ Возврат отклонен",
            reply_markup=None
        )
        logging.info(f"DEBUG: Отправлено уведомление об отклонении администратору")
        
        # Уведомляем пользователя
        await bot.send_message(
            user_id,
            f"❌ Возврат инструмента отклонен.\n"
            "Причины отклонения могут быть следующими:\n"
            "- Нечеткое или плохо освещенное фото\n"
            "- Не видно состояние инструмента\n"
            "- Не видно серийный номер\n"
            "- Некомплектность инструмента\n\n"
            "Пожалуйста, проверьте состояние инструмента и сделайте новое фото.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔄 Попробовать снова", callback_data=f"return_tool_{return_info[1]}"),
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )
        logging.info(f"DEBUG: Отправлено уведомление об отклонении пользователю {user_id}")
        
        # Подтверждаем обработку callback
        await callback_query.answer()
        
    except Exception as e:
        logging.error(f"Ошибка при отклонении возврата: {e}")
        await callback_query.message.answer(
            "❌ Произошла ошибка при отклонении возврата",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )

@dp.callback_query_handler(lambda c: c.data == "main_menu")
async def show_main_menu(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await show_welcome(callback_query.message)

@dp.callback_query_handler(lambda c: c.data == "help")
async def show_help_command(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await show_help(callback_query.message)

@dp.callback_query_handler(lambda c: c.data == "search_tools")
async def search_tools_command(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text(
        "🔍 Введите название или часть названия инструмента для поиска:",
        reply_markup=get_cancel_keyboard()
    )
    await ToolSearch.waiting_for_query.set()

@dp.callback_query_handler(lambda c: c.data == "admin_history")
async def show_admin_history(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("⛔ У вас нет доступа к этой функции.")
        return

    await callback_query.answer()
    history = get_tool_history()
    if not history:
        await callback_query.message.edit_text(
            "📜 История операций пуста", 
            reply_markup=get_admin_keyboard()
        )
        return

    text = "📜 *История операций*\n\n"
    for entry in history:
        tool_name, action, employee, date = entry
        text += f"🔧 *{tool_name}*\n"
        text += f"✨ Действие: _{action}_\n"
        text += f"👤 Сотрудник: _{employee}_\n"
        text += f"📅 Дата: {date}\n\n"

    await callback_query.message.edit_text(
        text, 
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query_handler(lambda c: c.data == "admin_overdue")
async def show_overdue_tools(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("⛔ У вас нет доступа к этой функции.")
        return

    await callback_query.answer()
    overdue_tools = get_overdue_tools()
    
    if not overdue_tools:
        await callback_query.message.edit_text(
            "✅ Нет просроченных инструментов", 
            reply_markup=get_admin_keyboard()
        )
        return

    text = "⚠️ *Просроченные инструменты*\n\n"
    for tool in overdue_tools:
        name, employee, issue_date, expected_return = tool
        text += f"🔧 *{name}*\n"
        text += f"👤 Сотрудник: _{employee}_\n"
        text += f"📅 Выдан: {issue_date}\n"
        text += f"⏰ Ожидался возврат: {expected_return}\n\n"

    await callback_query.message.edit_text(
        text, 
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query_handler(lambda c: c.data == "tools")
async def show_tools_command(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await show_tools(callback_query.message)

@dp.callback_query_handler(lambda c: c.data == "return")
async def return_tool_command(callback_query: types.CallbackQuery):
    await callback_query.answer()
    issued_tools = get_issued_tools()
    
    if not issued_tools:
        await callback_query.message.edit_text(
            "❌ У вас нет выданных инструментов для возврата",
            reply_markup=get_main_keyboard()
        )
        return

    text = "🔧 *Выберите инструмент для возврата:*\n\n"
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for tool in issued_tools:
        tool_id, name, employee, issue_date = tool
        if employee == callback_query.from_user.full_name:
            text += f"• {name} (выдан {issue_date})\n"
            keyboard.add(InlineKeyboardButton(
                f"Вернуть: {name}", 
                callback_data=f"return_{tool_id}"
            ))
    
    keyboard.add(InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu"))
    
    await callback_query.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query_handler(lambda c: c.data == "admin_report")
async def show_admin_report(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("⛔ У вас нет доступа к этой функции.")
        return

    await callback_query.answer()
    tools = get_tools()
    issued = get_issued_tools()
    
    total_tools = sum(tool[3] for tool in tools)  # Суммируем количество всех инструментов
    issued_count = len(issued)

    text = "📊 *Отчет по инструментам*\n\n"
    text += f"📦 Всего инструментов: {total_tools}\n"
    text += f"📤 Выдано: {issued_count}\n\n"

    # Добавляем статистику по брендам
    brands = {}
    for tool in tools:
        brand = tool[1].split(' - ')[0] if ' - ' in tool[1] else 'Другое'
        brands[brand] = brands.get(brand, 0) + 1

    text += "*Статистика по брендам:*\n"
    for brand, count in sorted(brands.items(), key=lambda x: x[1], reverse=True):
        text += f"• {brand}: {count}\n"

    await callback_query.message.edit_text(
        text,
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query_handler(lambda c: c.data == "admin_issued")
async def show_admin_issued(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("⛔ У вас нет доступа к этой функции.")
        return

    await callback_query.answer()
    issued_tools = get_issued_tools()
    
    if not issued_tools:
        await callback_query.message.edit_text(
            "📋 Нет выданных инструментов", 
            reply_markup=get_admin_keyboard()
        )
        return

    text = "📋 *Выданные инструменты*\n\n"
    for tool in issued_tools:
        tool_id, name, employee, issue_date = tool
        text += f"🔧 *{name}*\n"
        text += f"👤 Сотрудник: _{employee}_\n"
        text += f"📅 Выдан: {issue_date}\n\n"

    await callback_query.message.edit_text(
        text,
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query_handler(lambda c: c.data == "main_menu")
async def show_main_menu(callback_query: types.CallbackQuery):
    try:
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        # Обычные кнопки для всех пользователей
        keyboard.add(
            InlineKeyboardButton("🔧 Инструменты", callback_data="tools"),
            InlineKeyboardButton("↩️ Вернуть", callback_data="return")
        )
        
        # Добавляем кнопку админа только для админа
        if str(callback_query.from_user.id) == ADMIN_ID:
            keyboard.add(InlineKeyboardButton("👨‍💼 Админ панель", callback_data="admin"))

        await callback_query.message.edit_text(
            "🏠 *Главное меню*\n\n"
            "Выберите действие:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Ошибка при показе главного меню: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка.\n"
            "Попробуйте позже или обратитесь к администратору.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔄 Обновить", callback_data="main_menu")
            )
        )

@dp.callback_query_handler(lambda c: c.data == "tools")
async def show_tools_command(callback_query: types.CallbackQuery):
    try:
        logger.info("DEBUG: Вызвана функция show_tools с callback_data=tools")
        tools = get_tools()
        logger.info(f"DEBUG: Получены инструменты: {tools}")
        
        if not tools:
            await callback_query.message.edit_text(
                "❌ *Список инструментов пуст*\n\n"
                "В базе данных нет доступных инструментов.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                )
            )
            return

        # Получаем список выданных инструментов для проверки статуса
        issued_tools = get_issued_tools()
        issued_tool_ids = [tool[0] for tool in issued_tools]

        # Группируем инструменты по брендам
        tools_by_brand = {}
        for tool in tools:
            brand = tool[1].split(' - ')[0] if ' - ' in tool[1] else 'Другое'
            if brand not in tools_by_brand:
                tools_by_brand[brand] = []
            tools_by_brand[brand].append(tool)

        text = "🛠️ *Доступные инструменты:*\n\n"
        keyboard = InlineKeyboardMarkup(row_width=1)

        for brand in sorted(tools_by_brand.keys()):
            brand_tools = tools_by_brand[brand]
            text += f"*{brand}:*\n"
            for tool in brand_tools:
                status = "❌ Выдан" if tool[0] in issued_tool_ids else "✅ Доступен"
                text += f"• {tool[1]} - {status}\n"
            text += "\n"

        text += "\nДля получения инструмента нажмите на соответствующую кнопку ниже:"
        
        # Добавляем кнопки только для доступных инструментов
        for tool in tools:
            if tool[0] not in issued_tool_ids:
                keyboard.add(InlineKeyboardButton(
                    f"📦 {tool[1]}", 
                    callback_data=f"select_tool_{tool[0]}"
                ))

        keyboard.add(InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu"))
        
        await callback_query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка при показе списка инструментов: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при получении списка инструментов.\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )

@dp.callback_query_handler(lambda c: c.data.startswith("select_tool_"))
async def select_tool_command(callback_query: types.CallbackQuery):
    try:
        tool_id = int(callback_query.data.replace("select_tool_", ""))
        
        # Проверяем, не выдан ли уже инструмент
        if is_tool_issued(tool_id):
            await callback_query.message.edit_text(
                "❌ Этот инструмент уже выдан.\n"
                "Пожалуйста, выберите другой инструмент.",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔙 Назад к списку", callback_data="tools"),
                    InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                )
            )
            return

        # Получаем информацию об инструменте
        tools = get_tools()
        tool_info = next((tool for tool in tools if tool[0] == tool_id), None)
        
        if not tool_info:
            await callback_query.message.edit_text(
                "❌ Инструмент не найден.\n"
                "Пожалуйста, выберите инструмент из списка.",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔙 Назад к списку", callback_data="tools"),
                    InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                )
            )
            return

        await ToolIssueState.waiting_for_fullname.set()
        await callback_query.message.edit_text(
            f"🛠️ *Выбран инструмент:* {tool_info[1]}\n\n"
            "👤 Пожалуйста, введите ваше ФИО полностью\n"
            "Например: _Иванов Иван Иванович_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Отмена", callback_data="cancel_issue")
            )
        )
        
        # Сохраняем ID инструмента в state
        async with dp.current_state().proxy() as data:
            data['tool_id'] = tool_id
            data['tool_name'] = tool_info[1]

    except ValueError as e:
        logger.error(f"Некорректный ID инструмента: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при выборе инструмента.\n"
            "Пожалуйста, попробуйте еще раз.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Назад к списку", callback_data="tools"),
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )
    except Exception as e:
        logger.error(f"Ошибка при выборе инструмента: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла критическая ошибка.\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )

@dp.callback_query_handler(lambda c: c.data == "cancel_issue", state=ToolIssueState.waiting_for_fullname)
async def cancel_issue(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await callback_query.answer("Выдача инструмента отменена")
    await callback_query.message.edit_text(
        "❌ Выдача инструмента отменена.",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
        )
    )

# Добавляем веб-сервер
WEBHOOK_HOST = 'https://igorka.onrender.com'
WEBHOOK_PATH = '/webhook'
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

app = web.Application()

async def on_startup(app):
    """Установка вебхука при запуске"""
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        await bot.delete_webhook()
        await bot.set_webhook(WEBHOOK_URL)
    logger.info("Бот запущен и установлен вебхук на " + WEBHOOK_URL)

async def on_shutdown(app):
    """Отключение вебхука при выключении"""
    await bot.delete_webhook()
    await dp.storage.close()
    await dp.storage.wait_closed()
    logger.info("Бот остановлен")

async def handle_webhook(request):
    """Обработчик вебхука"""
    if request.match_info.get('token') != bot.token:
        return web.Response(status=403)
    
    request_data = await request.json()
    update = types.Update(**request_data)
    await dp.process_update(update)
    return web.Response(status=200)

def setup_routes(app: web.Application):
    """Настройка маршрутов веб-приложения"""
    app.router.add_post(f'{WEBHOOK_PATH}/', handle_webhook)

def register_handlers(dp: Dispatcher):
    # Basic handlers
    dp.register_message_handler(start, commands=['start'])
    dp.register_callback_query_handler(show_help, lambda c: c.data == "help")
    dp.register_callback_query_handler(main_menu, lambda c: c.data == "main_menu")
    
    # Tool management handlers
    dp.register_callback_query_handler(show_tools, lambda c: c.data == "tools")
    dp.register_callback_query_handler(select_tool, lambda c: c.data.startswith('select_tool_'))
    dp.register_message_handler(process_employee_fullname, state=ToolIssueState.waiting_for_fullname)
    
    # Admin handlers
    dp.register_callback_query_handler(process_admin_issue_response, lambda c: c.data.startswith(('approve_', 'reject_')))
    dp.register_callback_query_handler(show_admin_history, lambda c: c.data == "admin_history")
    dp.register_callback_query_handler(show_admin_report, lambda c: c.data == "admin_report")
    dp.register_callback_query_handler(show_admin_issued, lambda c: c.data == "admin_issued")
    dp.register_callback_query_handler(show_overdue_tools, lambda c: c.data == "overdue_tools")
    
    # Search handlers
    dp.register_callback_query_handler(search_tools_start, lambda c: c.data == "search")
    dp.register_message_handler(process_search, state=SearchState.waiting_for_query)
    
    # Return handlers
    dp.register_callback_query_handler(show_return_menu, lambda c: c.data == "return")
    dp.register_callback_query_handler(return_tool, lambda c: c.data.startswith('return_tool_'))
    dp.register_callback_query_handler(cancel_return, lambda c: c.data == "cancel_return", state="*")
    dp.register_callback_query_handler(reject_return, lambda c: c.data.startswith('reject_return_'))
    dp.register_callback_query_handler(approve_return, lambda c: c.data.startswith('approve_return_'))
    dp.register_message_handler(process_return_photo, content_types=['photo'], state=ReturnToolStates.waiting_for_photo)

@dp.callback_query_handler(lambda c: c.data.startswith('approve_return_'))
async def approve_return(callback_query: types.CallbackQuery):
    try:
        logging.info(f"DEBUG: Получен callback для подтверждения возврата: {callback_query.data}")
        # Parse callback data
        data = callback_query.data.split('_')
        if len(data) != 4:
            logging.error(f"DEBUG: Неверный формат callback data: {callback_query.data}")
            await callback_query.answer("❌ Ошибка: неверный формат данных")
            return
            
        issue_id = int(data[2])
        user_id = int(data[3])
        
        db = DatabaseConnection()
        with db.connection:
            cursor = db.connection.cursor()
            
            try:
                # Получаем информацию о возврате
                cursor.execute("""
                    SELECT t.id, t.name, it.employee_name, it.issue_date
                    FROM issued_tools it
                    JOIN tools t ON it.tool_id = t.id
                    WHERE it.id = ?
                """, (issue_id,))
                
                return_info = cursor.fetchone()
                if not return_info:
                    logging.error(f"DEBUG: Не найдена информация о возврате с ID {issue_id}")
                    await callback_query.answer("❌ Ошибка: информация о возврате не найдена")
                    return
                
                tool_id, tool_name, employee_name, issue_date = return_info
                
                # Обновляем статус инструмента
                cursor.execute("UPDATE tools SET status = 'available' WHERE id = ?", (tool_id,))
                
                # Обновляем дату возврата
                return_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute("""
                    UPDATE issued_tools 
                    SET return_date = ? 
                    WHERE id = ?
                """, (return_date, issue_id))
                
                # Добавляем запись в историю
                cursor.execute("""
                    INSERT INTO tool_history (tool_id, action, employee_name, timestamp)
                    VALUES (?, 'returned', ?, ?)
                """, (tool_id, employee_name, return_date))
                
                db.connection.commit()
                
                # Отправляем уведомление пользователю
                await bot.send_message(
                    user_id,
                    f"✅ Возврат инструмента подтвержден!\n"
                    f"🔧 Инструмент: {tool_name}\n"
                    f"📅 Дата возврата: {return_date}",
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                    )
                )
                
                # Обновляем сообщение админа
                await callback_query.message.edit_text(
                    f"✅ Возврат инструмента подтвержден\n"
                    f"🔧 Инструмент: {tool_name}\n"
                    f"👤 Сотрудник: {employee_name}\n"
                    f"📅 Дата выдачи: {issue_date}\n"
                    f"📅 Дата возврата: {return_date}",
                    reply_markup=None
                )
                
            except Exception as e:
                db.connection.rollback()
                logging.error(f"Ошибка при подтверждении возврата: {e}")
                await callback_query.answer("❌ Ошибка при обновлении базы данных")
                return
                
    except Exception as e:
        logging.error(f"Ошибка при обработке подтверждения возврата: {e}")
        await callback_query.answer("❌ Произошла ошибка при обработке запроса")
        
    await callback_query.answer()

@dp.message_handler(content_types=['photo'], state=ReturnToolStates.waiting_for_photo)
async def process_return_photo(message: types.Message, state: FSMContext):
    try:
        logging.info("DEBUG: Получено фото для возврата инструмента")
        
        # Получаем данные из состояния
        async with state.proxy() as data:
            issue_id = data['issue_id']
            tool_name = data['tool_name']
            employee = data['employee']
            
        # Получаем фото с наилучшим качеством
        photo = message.photo[-1]
        file_id = photo.file_id
        
        # Создаем клавиатуру для админа
        admin_keyboard = InlineKeyboardMarkup(row_width=2)
        admin_keyboard.add(
            InlineKeyboardButton("✅ Принять", callback_data=f"approve_return_{issue_id}_{message.from_user.id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_return_{issue_id}_{message.from_user.id}")
        )
        
        # Отправляем фото и информацию админу
        admin_message = (
            f"📸 Получена фотография для возврата инструмента\n\n"
            f"🔧 Инструмент: {tool_name}\n"
            f"👤 Сотрудник: {employee}\n"
            f"🆔 ID выдачи: {issue_id}"
        )
        
        # Отправляем сообщение админу
        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=admin_message,
            reply_markup=admin_keyboard
        )
        
        # Очищаем состояние
        await state.finish()
        
        # Отправляем подтверждение пользователю
        await message.reply(
            "✅ Фото получено и отправлено на проверку администратору.\n"
            "Пожалуйста, ожидайте подтверждения.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )
        
    except Exception as e:
        logging.error(f"Ошибка при обработке фото возврата: {e}")
        await message.reply(
            "❌ Произошла ошибка при обработке фотографии.\n"
            "Пожалуйста, попробуйте еще раз или обратитесь к администратору.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )
        await state.finish()

if __name__ == '__main__':
    # Создаем таблицы при запуске
    try:
        create_tables()
        
        # Проверяем базу данных при запуске
        logging.info('Проверка базы данных при запуске...')
        tools = get_tools()
        logging.info(f'Количество инструментов в базе: {len(tools)}')
        
        if len(tools) == 0:
            logging.info('База данных пуста, заполняем начальными данными...')
            populate_database()
            tools = get_tools()
            logging.info(f'После заполнения в базе {len(tools)} инструментов')
    except Exception as e:
        logging.error(f"Ошибка при инициализации базы данных: {e}")
        raise

    # Создаем экземпляр бота
    bot = Bot(token=API_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(bot, storage=storage)
    dp.middleware.setup(LoggingMiddleware())
    
    # Регистрируем все обработчики
    register_handlers(dp)
    
    # Настраиваем веб-сервер
    app = web.Application()
    setup_routes(app)
    
    # Запускаем бота
    logging.info('Bot started')
    
    # Запускаем веб-сервер
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(
        app,
        host='0.0.0.0',
        port=int(os.getenv('PORT', 8080))
    )