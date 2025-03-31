import logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from db import create_tables, get_tools, get_issued_tools, return_tool, get_tool_history, get_overdue_tools, is_tool_issued, issue_tool, create_tool_request, get_issue_request_info, approve_issue_request, reject_issue_request
from config import API_TOKEN
from datetime import datetime
import os
from aiohttp import web
from populate_database import populate_database

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

# Количество инструментов на странице
TOOLS_PER_PAGE = 10

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

@dp.callback_query_handler(lambda c: c.data.startswith(("approve_", "reject_")))
async def process_admin_issue_response(callback_query: types.CallbackQuery):
    logger.info(f"DEBUG: Получен callback: {callback_query.data}")
    
    try:
        parts = callback_query.data.split("_")
        if len(parts) != 3:
            logger.error(f"DEBUG: Неверный формат callback data: {callback_query.data}")
            await callback_query.answer("❌ Ошибка обработки запроса")
            return
            
        action = parts[0]  # approve или reject
        tool_id = int(parts[1])
        chat_id = int(parts[2])
        
        logger.info(f"DEBUG: Обработка {action} для tool_id={tool_id}, chat_id={chat_id}")
        
        # Проверяем, что запрос обрабатывает админ
        if callback_query.from_user.id != ADMIN_ID:
            logger.warning(f"DEBUG: Попытка неавторизованного доступа от user_id={callback_query.from_user.id}")
            await callback_query.answer("⛔ У вас нет прав для этого действия")
            return
            
        # Получаем информацию о запросе до выполнения действия
        request_info = get_issue_request_info(tool_id, chat_id)
        logger.info(f"DEBUG: Информация о запросе: {request_info}")
        
        if action == "approve":
            if request_info and approve_issue_request(tool_id, chat_id):
                logger.info(f"DEBUG: Запрос успешно одобрен")
                # Уведомляем сотрудника
                await bot.send_message(
                    chat_id,
                    "✅ Ваш запрос на получение инструмента одобрен!\n"
                    "Вы можете получить инструмент."
                )
                await callback_query.message.edit_text(
                    f"{callback_query.message.text}\n\n"
                    "✅ Запрос одобрен",
                    parse_mode="Markdown"
                )
                await callback_query.answer("✅ Запрос одобрен")
            else:
                logger.error(f"DEBUG: Ошибка при одобрении запроса")
                await callback_query.answer("❌ Ошибка при одобрении запроса")
        elif action == "reject":
            if reject_issue_request(tool_id, chat_id):
                logger.info(f"DEBUG: Запрос успешно отклонен")
                # Уведомляем сотрудника
                await bot.send_message(
                    chat_id,
                    "❌ Ваш запрос на получение инструмента отклонен."
                )
                await callback_query.message.edit_text(
                    f"{callback_query.message.text}\n\n"
                    "❌ Запрос отклонен",
                    parse_mode="Markdown"
                )
                await callback_query.answer("❌ Запрос отклонен")
            else:
                logger.error(f"DEBUG: Ошибка при отклонении запроса")
                await callback_query.answer("❌ Ошибка при отклонении запроса")
    except Exception as e:
        logger.error(f"DEBUG: Необработанная ошибка: {str(e)}")
        await callback_query.answer("❌ Произошла ошибка")

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
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                )
            )
            return

        keyboard = InlineKeyboardMarkup(row_width=1)
        for tool in issued_tools:
            tool_id, name, employee, issue_date, expected_return = tool
            issue_date = datetime.strptime(issue_date, '%Y-%m-%d').strftime('%d.%m.%Y')
            expected_return = datetime.strptime(expected_return, '%Y-%m-%d').strftime('%d.%m.%Y')
            
            button_text = f"🔧 {name} - {employee} (до {expected_return})"
            keyboard.add(InlineKeyboardButton(button_text, callback_data=f"return_tool_{tool_id}"))

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
        tool_id = int(callback_query.data.replace("return_tool_", ""))
        logger.info(f"DEBUG: Запрос на возврат инструмента {tool_id}")

        # Получаем информацию о выданном инструменте
        issued_tool = get_issued_tool_by_id(tool_id)
        if not issued_tool:
            await callback_query.message.edit_text(
                "❌ Инструмент не найден или уже возвращен.",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔙 Назад", callback_data="return"),
                    InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                )
            )
            return

        tool_name = issued_tool[1]
        employee = issued_tool[2]
        issue_date = datetime.strptime(issued_tool[3], '%Y-%m-%d').strftime('%d.%m.%Y')
        expected_return = datetime.strptime(issued_tool[4], '%Y-%m-%d').strftime('%d.%m.%Y')

        # Сохраняем ID инструмента в state
        await ToolReturnState.waiting_for_photo.set()
        state = dp.current_state(user=callback_query.from_user.id)
        async with state.proxy() as data:
            data['tool_id'] = tool_id
            data['tool_name'] = tool_name
            data['employee'] = employee

        await callback_query.message.edit_text(
            f"📸 *Возврат инструмента*\n\n"
            f"🛠️ Инструмент: *{tool_name}*\n"
            f"👤 Сотрудник: {employee}\n"
            f"📅 Дата выдачи: {issue_date}\n"
            f"⚠️ Вернуть до: {expected_return}\n\n"
            "Пожалуйста, сфотографируйте инструмент для подтверждения возврата.\n"
            "Фото должно быть четким и показывать состояние инструмента.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("❌ Отмена", callback_data="cancel_return")
            )
        )

    except ValueError as e:
        logger.error(f"Некорректный ID инструмента: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при обработке запроса.\n"
            "Пожалуйста, попробуйте еще раз.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔙 Назад", callback_data="return"),
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )
    except Exception as e:
        logger.error(f"Ошибка при возврате инструмента: {e}")
        await callback_query.message.edit_text(
            "❌ Произошла ошибка при обработке запроса.\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )

@dp.message_handler(content_types=['photo'], state=ToolReturnState.waiting_for_photo)
async def process_return_photo(message: types.Message, state: FSMContext):
    try:
        async with state.proxy() as data:
            tool_id = data['tool_id']
            tool_name = data['tool_name']
            employee = data['employee']
        
        # Сохраняем фото
        photo = message.photo[-1]  # Берем последнее (самое качественное) фото
        file_id = photo.file_id
        
        try:
            # Пытаемся вернуть инструмент
            if return_tool(tool_id, employee):
                await message.reply(
                    "✅ Инструмент успешно возвращен!\n"
                    "Спасибо за своевременный возврат.",
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                    )
                )
                
                # Отправляем уведомление администратору с фото
                if ADMIN_ID:
                    try:
                        await bot.send_photo(
                            ADMIN_ID,
                            photo=file_id,
                            caption=f"📸 Фото возвращенного инструмента\n"
                                  f"👤 Сотрудник: {employee}"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка при отправке фото администратору: {e}")
            else:
                raise Exception("Ошибка при возврате в базе данных")
                
        except Exception as e:
            logger.error(f"Ошибка при возврате инструмента в БД: {e}")
            await message.reply(
                "❌ Произошла ошибка при возврате инструмента.\n"
                "Пожалуйста, попробуйте позже или обратитесь к администратору.",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
                )
            )
    except KeyError as e:
        logger.error(f"Отсутствуют необходимые данные в state: {e}")
        await message.reply(
            "❌ Ошибка: отсутствуют необходимые данные.\n"
            "Пожалуйста, начните процесс возврата заново.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )
    except Exception as e:
        logger.error(f"Критическая ошибка при обработке фото: {e}")
        await message.reply(
            "❌ Произошла критическая ошибка при обработке фото.\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )
    finally:
        await state.finish()

@dp.callback_query_handler(lambda c: c.data == "cancel_return", state=ToolReturnState.waiting_for_photo)
async def cancel_return(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await callback_query.answer("Возврат инструмента отменен")
    await callback_query.message.edit_text(
        "❌ Возврат инструмента отменен.",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
        )
    )

def get_admin_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("📋 Выданные инструменты", callback_data="admin_issued"))
    keyboard.add(InlineKeyboardButton("📊 Отчет", callback_data="admin_report"))
    keyboard.add(InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu"))
    return keyboard

@dp.callback_query_handler(lambda c: c.data.startswith("admin_"))
async def process_admin_action(callback_query: types.CallbackQuery):
    try:
        # Сразу отвечаем на callback
        await callback_query.answer()
        
        if callback_query.from_user.id != ADMIN_ID:
            await callback_query.message.reply("⛔ У вас нет доступа к этой функции.")
            return

        action = callback_query.data.replace("admin_", "")
        
        if action == "issued":
            try:
                issued_tools = get_issued_tools()
                if not issued_tools:
                    await callback_query.message.edit_text(
                        "📋 *Выданные инструменты*\n\n"
                        "На данный момент нет выданных инструментов.", 
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
            except Exception as e:
                logger.error(f"Ошибка при получении выданных инструментов: {e}")
                await callback_query.message.edit_text(
                    "❌ Произошла ошибка при получении списка выданных инструментов.\n"
                    "Пожалуйста, попробуйте позже.",
                    reply_markup=get_admin_keyboard()
                )
                
        elif action == "report":
            try:
                tools = get_tools()
                issued = get_issued_tools()
                
                total_tools = sum(tool[3] for tool in tools)  # Суммируем количество всех инструментов
                issued_count = len(issued)
                available = total_tools - issued_count
                
                response = "📊 *Отчет по инструментам*\n\n"
                response += f"📦 Всего инструментов: {total_tools}\n"
                response += f"✅ Доступно: {available}\n"
                response += f"📤 Выдано: {issued_count}\n\n"

                # Добавляем статистику по брендам
                brands = {}
                for tool in tools:
                    brand = tool[1].split(' - ')[0] if ' - ' in tool[1] else 'Другое'
                    brands[brand] = brands.get(brand, 0) + 1

                response += "*Статистика по брендам:*\n"
                for brand, count in sorted(brands.items(), key=lambda x: x[1], reverse=True):
                    response += f"• {brand}: {count}\n"

                await callback_query.message.edit_text(
                    response,
                    reply_markup=get_admin_keyboard(),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Ошибка при формировании отчета: {e}")
                await callback_query.message.edit_text(
                    "❌ Произошла ошибка при формировании отчета.\n"
                    "Пожалуйста, попробуйте позже.",
                    reply_markup=get_admin_keyboard()
                )
                
        elif action == "history":
            try:
                history = get_tool_history()
                if not history:
                    await callback_query.message.edit_text(
                        "📜 *История операций*\n\n"
                        "История пуста.",
                        parse_mode="Markdown",
                        reply_markup=get_admin_keyboard()
                    )
                    return
                    
                text = "📜 *История операций*\n\n"
                for entry in history[:10]:  # Показываем только последние 10 записей
                    tool_name, action_type, employee, timestamp = entry
                    text += f"🔧 *{tool_name}*\n"
                    text += f"📝 Действие: _{action_type}_\n"
                    if employee:
                        text += f"👤 Сотрудник: _{employee}_\n"
                    text += f"🕒 Время: {timestamp}\n\n"
                
                await callback_query.message.edit_text(
                    text,
                    parse_mode="Markdown",
                    reply_markup=get_admin_keyboard()
                )
            except Exception as e:
                logger.error(f"Ошибка при получении истории: {e}")
                await callback_query.message.edit_text(
                    "❌ Произошла ошибка при получении истории операций.\n"
                    "Пожалуйста, попробуйте позже.",
                    reply_markup=get_admin_keyboard()
                )
                
        elif action == "overdue":
            try:
                overdue_tools = get_overdue_tools()
                if not overdue_tools:
                    await callback_query.message.edit_text(
                        "⚠️ *Просроченные инструменты*\n\n"
                        "Нет просроченных инструментов.",
                        parse_mode="Markdown",
                        reply_markup=get_admin_keyboard()
                    )
                    return

                text = "⚠️ *Просроченные инструменты*\n\n"
                for tool in overdue_tools:
                    name, employee, issue_date, expected_return = tool
                    text += f"🔧 *{name}*\n"
                    text += f"👤 Сотрудник: _{employee}_\n"
                    text += f"📅 Выдан: {issue_date}\n"
                    text += f"⚠️ Ожидался возврат: {expected_return}\n\n"

                await callback_query.message.edit_text(
                    text,
                    parse_mode="Markdown",
                    reply_markup=get_admin_keyboard()
                )
            except Exception as e:
                logger.error(f"Ошибка при получении просроченных инструментов: {e}")
                await callback_query.message.edit_text(
                    "❌ Произошла ошибка при получении списка просроченных инструментов.\n"
                    "Пожалуйста, попробуйте позже.",
                    reply_markup=get_admin_keyboard()
                )
        else:
            logger.warning(f"Неизвестное админ-действие: {action}")
            await callback_query.message.edit_text(
                "❌ Неизвестное действие.\n"
                "Пожалуйста, выберите действие из меню.",
                reply_markup=get_admin_keyboard()
            )
            
    except Exception as e:
        logger.error(f"Критическая ошибка в обработчике админ-действий: {e}")
        try:
            await callback_query.message.edit_text(
                "❌ Произошла критическая ошибка.\n"
                "Пожалуйста, попробуйте позже или обратитесь к разработчику.",
                reply_markup=get_admin_keyboard()
            )
        except Exception as e2:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e2}")

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
    available_tools = sum(1 for tool in tools if tool[2] == 'available')
    issued_count = len(issued)

    text = "📊 *Отчет по инструментам*\n\n"
    text += f"📦 Всего инструментов: {total_tools}\n"
    text += f"✅ Доступно: {available_tools}\n"
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
            InlineKeyboardButton("🛠️ Инструменты", callback_data="tools"),
            InlineKeyboardButton("🔍 Поиск", callback_data="search_tools")
        )
        keyboard.add(
            InlineKeyboardButton("📸 Вернуть", callback_data="return"),
            InlineKeyboardButton("ℹ️ Помощь", callback_data="help")
        )
        
        # Добавляем кнопки админ-панели, если это администратор
        if callback_query.from_user.id == ADMIN_ID:
            keyboard.add(
                InlineKeyboardButton("📋 Выданные", callback_data="admin_issued"),
                InlineKeyboardButton("📊 Отчёт", callback_data="admin_report")
            )
            keyboard.add(
                InlineKeyboardButton("📜 История", callback_data="admin_history"),
                InlineKeyboardButton("⚠️ Просрочки", callback_data="admin_overdue")
            )

        await callback_query.message.edit_text(
            f"👋 *Здравствуйте, {callback_query.from_user.first_name}!*\n\n"
            "Выберите нужное действие из меню ниже:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка при показе главного меню: {e}")
        await callback_query.answer("❌ Произошла ошибка. Попробуйте /start")

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
        await bot.set_webhook(WEBHOOK_URL)
    logger.info("Bot started")

async def on_shutdown(app):
    """Отключение вебхука при выключении"""
    await bot.delete_webhook()
    await bot.close()
    logger.info("Bot stopped")

async def handle_webhook(request):
    """Обработчик вебхука"""
    # Проверяем подпись запроса от Telegram
    update = types.Update(**await request.json())
    await dp.process_update(update)
    return web.Response(status=200)

def setup_routes(app: web.Application):
    app.router.add_post(WEBHOOK_PATH, handle_webhook)

if __name__ == '__main__':
    # Создаем таблицы при запуске
    create_tables()
    
    # Настраиваем маршруты
    setup_routes(app)
    
    # Добавляем обработчики запуска и остановки
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    # Запускаем веб-сервер
    web.run_app(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))