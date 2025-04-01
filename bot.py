import logging
import os
from datetime import datetime
import asyncio
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, PhotoSize
from aiogram.filters import Command, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from db import (
    get_tools, get_issued_tools, create_tool_request, 
    approve_issue_request, get_issue_request_info, get_tool_by_id,
    get_issued_tool_by_id, update_tool_status, add_tool_history,
    get_tool_history, get_overdue_tools, get_all_issue_requests,
    create_tables, get_return_info, complete_return
)
from config import API_TOKEN, ADMIN_ID
from populate_database import populate_database

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
TOKEN = API_TOKEN
storage = MemoryStorage()
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# Создаем папку для фотографий, если её нет
PHOTOS_DIR = os.path.join(os.getcwd(), 'photos')
if not os.path.exists(PHOTOS_DIR):
    os.makedirs(PHOTOS_DIR)

# Проверяем содержимое базы данных при запуске
logger.info("Проверка базы данных при запуске...")
tools = get_tools()
logger.info(f"Количество инструментов в базе: {len(tools)}")
if tools:
    logger.info("Примеры инструментов:")
    for tool in tools[:5]:  # Показываем первые 5 инструментов
        logger.info(f"- {tool['name']}")

# Создание таблиц
create_tables()

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

async def throttle_callback(callback_query: CallbackQuery) -> bool:
    """
    Проверяет, не слишком ли часто отправляются callback-запросы.
    Возвращает True, если запрос нужно обработать, False если его нужно пропустить.
    """
    user_id = callback_query.from_user.id
    current_time = datetime.now().timestamp()
    
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
@router.message(Command("start"))
async def start(message: Message):
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

@router.callback_query(F.data == "help")
async def show_help(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "main_menu")
async def main_menu(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "tools" | F.data.startswith("tools_page_"))
async def show_tools(callback_query: CallbackQuery):
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

@router.callback_query(F.data.startswith("select_tool_"))
async def select_tool(callback_query: CallbackQuery):
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

@router.message(StateFilter(ToolIssueState.waiting_for_fullname))
async def process_employee_fullname(message: Message, state: FSMContext):
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

@router.callback_query(F.data.startswith(("approve_", "reject_")))
async def process_admin_issue_response(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "search_tools")
async def search_tools_start(callback_query: CallbackQuery):
    # Сразу отвечаем на callback
    await callback_query.answer()
    await SearchState.waiting_for_query.set()
    await callback_query.message.reply("🔍 Введите название инструмента:")

@router.message(StateFilter(SearchState.waiting_for_query))
async def process_search(message: Message, state: FSMContext):
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

@router.callback_query(F.data == "return")
async def show_return_menu(callback_query: CallbackQuery):
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

@router.callback_query(F.data.startswith("return_tool_"))
async def return_tool(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "cancel_return", StateFilter(ReturnToolStates.waiting_for_photo))
async def cancel_return(callback_query: CallbackQuery, state: FSMContext):
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
                InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
            )
        )
        await callback_query.answer()

@router.callback_query(F.data.startswith('reject_return_'))
async def reject_return(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "main_menu")
async def show_main_menu(callback_query: CallbackQuery):
    await callback_query.answer()
    await show_welcome(callback_query.message)

@router.callback_query(F.data == "help")
async def show_help_command(callback_query: CallbackQuery):
    await callback_query.answer()
    await show_help(callback_query.message)

@router.callback_query(F.data == "search_tools")
async def search_tools_command(callback_query: CallbackQuery):
    await callback_query.answer()
    await callback_query.message.edit_text(
        "🔍 Введите название или часть названия инструмента для поиска:",
        reply_markup=get_cancel_keyboard()
    )
    await ToolSearch.waiting_for_query.set()

@router.callback_query(F.data == "admin_history")
async def show_admin_history(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "admin_overdue")
async def show_overdue_tools(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "tools")
async def show_tools_command(callback_query: CallbackQuery):
    await callback_query.answer()
    await show_tools(callback_query.message)

@router.callback_query(F.data == "return")
async def return_tool_command(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "admin_report")
async def show_admin_report(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "admin_issued")
async def show_admin_issued(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "main_menu")
async def show_main_menu(callback_query: CallbackQuery):
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

@router.callback_query(F.data == "tools")
async def show_tools_command(callback_query: CallbackQuery):
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

@router.callback_query(F.data.startswith("select_tool_"))
async def select_tool_command(callback_query: CallbackQuery):
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

@router.message(StateFilter(ToolIssueState.waiting_for_fullname))
async def process_employee_fullname(message: Message, state: FSMContext):
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

@router.callback_query(F.data == "cancel_issue", StateFilter(ToolIssueState.waiting_for_fullname))
async def cancel_issue(callback_query: CallbackQuery, state: FSMContext):
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
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.environ.get('PORT', 8080))

# Настройка веб-приложения
app = web.Application()

async def on_startup(bot: Bot):
    # Создаем таблицы при запуске
    create_tables()
    
    # Проверяем наличие инструментов
    tools = get_tools()
    if not tools:
        logger.info("База данных пуста, заполняем начальными данными...")
        populate_database()
    
    # Устанавливаем вебхук
    await bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=API_TOKEN[:50]  # Используем первые 50 символов токена как секрет
    )
    logger.info(f"Webhook установлен на {WEBHOOK_URL}")

async def on_shutdown(bot: Bot):
    # Удаляем вебхук при выключении
    await bot.delete_webhook()
    logger.info("Webhook удален")

def main():
    # Создаем приложение
    app = web.Application()

    # Настраиваем вебхук
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=API_TOKEN[:50]  # Тот же секрет, что и при установке вебхука
    )
    webhook_handler.register(app, path=WEBHOOK_PATH)

    # Настраиваем события запуска/остановки
    setup_application(app, dp, bot=bot)

    # Добавляем обработчики запуска/остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Запускаем веб-сервер
    web.run_app(app, host=WEBAPP_HOST, port=WEBAPP_PORT)

if __name__ == '__main__':
    main()