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

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
TOKEN = API_TOKEN
ADMIN_ID = 1495719377  # ID администратора

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Устанавливаем экземпляр бота как текущий
Bot.set_current(bot)

# Проверяем содержимое базы данных при запуске
logger.info("Проверка базы данных при запуске...")
tools = get_tools()
logger.info(f"Количество инструментов в базе: {len(tools)}")
if tools:
    logger.info("Примеры инструментов:")
    for tool in tools[:5]:  # Показываем первые 5 инструментов
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
    # Сразу отвечаем на callback
    await callback_query.answer()
    
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
async def select_tool(callback_query: types.CallbackQuery, state: FSMContext):
    tool_id = int(callback_query.data.split("_")[2])
    
    # Сохраняем ID инструмента в состоянии
    await state.update_data(tool_id=tool_id)
    
    # Переходим в состояние ожидания ФИО
    await ToolIssueState.waiting_for_fullname.set()
    
    await callback_query.message.answer(
        "👤 *Получение инструмента*\n\n"
        "Пожалуйста, введите ваше ФИО:",
        parse_mode="Markdown"
    )
    await callback_query.answer()

@dp.message_handler(state=ToolIssueState.waiting_for_fullname)
async def process_employee_fullname(message: types.Message, state: FSMContext):
    employee_fullname = message.text.strip()
    
    # Получаем данные из состояния
    data = await state.get_data()
    tool_id = data.get('tool_id')
    
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
    
    # Сбрасываем состояние
    await state.finish()

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
async def return_tool_request(callback_query: types.CallbackQuery):
    # Сразу отвечаем на callback
    await callback_query.answer()
    
    issued_tools = get_issued_tools()
    if not issued_tools:
        await callback_query.message.reply(
            "❌ *Нет выданных инструментов*\n\n"
            "Все инструменты находятся на месте.",
            parse_mode="Markdown"
        )
        return

    response = "📥 *Возврат инструмента*\n\n"
    response += "_Выберите инструмент для возврата:_\n\n"

    keyboard = InlineKeyboardMarkup(row_width=1)
    for tool in issued_tools:
        issue_date = tool[3].split()[0] if tool[3] else "Дата неизвестна"
        button_text = f"📦 {tool[1]} (выдан: {issue_date})"
        keyboard.add(InlineKeyboardButton(button_text, callback_data=f"return_{tool[0]}"))
    
    keyboard.add(InlineKeyboardButton("❌ Отмена", callback_data="tools"))
    
    await callback_query.message.reply(response, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data.startswith("return_"))
async def process_return_tool(callback_query: types.CallbackQuery, state: FSMContext):
    # Сразу отвечаем на callback
    await callback_query.answer()
    
    try:
        tool_id = int(callback_query.data.split("_")[1])
        
        # Получаем информацию о выданном инструменте
        issued_tools = get_issued_tools()
        tool_info = next((tool for tool in issued_tools if tool[0] == tool_id), None)
        
        if tool_info:
            async with state.proxy() as data:
                data['return_tool_id'] = tool_id
                data['employee_name'] = tool_info[2]  # Сохраняем имя сотрудника
            
            await ToolReturnState.waiting_for_photo.set()
            await callback_query.message.reply("📸 Пожалуйста, отправьте фото инструмента.")
        else:
            await callback_query.answer("❌ Инструмент не найден")
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка при выборе инструмента: {e}")
        await callback_query.answer("❌ Ошибка при выборе инструмента")

@dp.message_handler(content_types=['photo'], state=ToolReturnState.waiting_for_photo)
async def process_return_photo(message: types.Message, state: FSMContext):
    try:
        async with state.proxy() as data:
            tool_id = data['return_tool_id']
            employee_name = data['employee_name']
        
        if return_tool(tool_id, employee_name):
            await message.reply(
                "✅ Инструмент успешно возвращен!\n"
                "Спасибо за своевременный возврат."
            )
        else:
            raise Exception("Ошибка при возврате в базе данных")
            
    except Exception as e:
        logger.error(f"Ошибка при возврате инструмента: {e}")
        await message.reply(
            "❌ Произошла ошибка при возврате инструмента.\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору."
        )
    finally:
        await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("admin_"))
async def process_admin_action(callback_query: types.CallbackQuery):
    # Сразу отвечаем на callback
    await callback_query.answer()
    
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.message.reply("⛔ У вас нет доступа к этой функции.")
        return

    action = callback_query.data.replace("admin_", "")
    
    if action == "issued":
        issued = get_issued_tools()
        if not issued:
            await callback_query.message.reply(
                "📋 *Выданные инструменты*\n\n"
                "Нет выданных инструментов.",
                parse_mode="Markdown"
            )
            return
            
        response = "📋 *Выданные инструменты*\n\n"
        for tool in issued:
            tool_id, name, employee, issue_date, expected_return = tool
            issue_date = datetime.strptime(issue_date, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
            expected_return = datetime.strptime(expected_return, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y') if expected_return else "Не указана"
            
            response += f"┌ *{name}*\n"
            response += f"├ Кому: _{employee}_\n"
            response += f"├ Выдан: {issue_date}\n"
            response += f"└ Ожидается: {expected_return}\n\n"
            
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu"))
        
        await callback_query.message.reply(response, reply_markup=keyboard, parse_mode="Markdown")
        
    elif action == "report":
        tools = get_tools()
        issued = get_issued_tools()
        
        total_tools = sum(tool[3] for tool in tools)  # Суммируем количество всех инструментов
        issued_count = len(issued)
        available = total_tools - issued_count
        
        response = "📊 *Общая статистика*\n\n"
        response += f"┌ Всего инструментов: {total_tools}\n"
        response += f"├ Выдано: {issued_count}\n"
        response += f"└ Доступно: {available}\n"
        
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu"))
        
        await callback_query.message.reply(response, reply_markup=keyboard, parse_mode="Markdown")
        
    elif action == "history":
        history = get_tool_history()
        if not history:
            await callback_query.message.reply(
                "📜 *История операций*\n\n"
                "История пуста.",
                parse_mode="Markdown"
            )
            return
            
        response = "📜 *История операций*\n\n"
        for entry in history:
            name, action, date, employee, notes = entry
            try:
                formatted_date = datetime.strptime(date, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
            except ValueError:
                formatted_date = "Некорректная дата"
            
            # Convert action to Russian
            action_text = "Выдан" if action == "issue" else "Возвращен" if action == "return" else action
            
            response += f"┌ *{name}*\n"
            response += f"├ Действие: _{action_text}_\n"
            if employee:
                response += f"├ Сотрудник: {employee}\n"
            response += f"└ Дата: {formatted_date}\n\n"
            
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu"))
        
        await callback_query.message.reply(response, reply_markup=keyboard, parse_mode="Markdown")
        
    elif action == "overdue":
        overdue = get_overdue_tools()
        if not overdue:
            await callback_query.message.reply(
                "⚠️ *Просроченные инструменты*\n\n"
                "Нет просроченных инструментов.",
                parse_mode="Markdown"
            )
            return
            
        response = "⚠️ *Просроченные инструменты*\n\n"
        for tool in overdue:
            tool_id, name, employee, issue_date, expected_return, quantity = tool
            issue_date = datetime.strptime(issue_date, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
            expected_return = datetime.strptime(expected_return, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y') if expected_return else "Не указана"
            
            response += f"┌ *{name}*\n"
            response += f"├ Сотрудник: _{employee}_\n"
            response += f"├ Выдан: {issue_date}\n"
            response += f"└ Ожидался: {expected_return}\n\n"
            
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu"))
        
        await callback_query.message.reply(response, reply_markup=keyboard, parse_mode="Markdown")


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