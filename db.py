import sqlite3
from datetime import datetime, timedelta
import logging
import os

# Create a logger
logger = logging.getLogger(__name__)

# Определяем путь к базе данных
DB_PATH = os.path.join(os.getenv('DATA_DIR', ''), 'tools.db')
if not DB_PATH:
    DB_PATH = 'tools.db'

class DatabaseConnection:
    def __init__(self):
        logger.info(f"DatabaseConnection: путь к базе данных: {DB_PATH}")
        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()

    def __enter__(self):
        return self.cursor

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        else:
            self.conn.commit()
        self.conn.close()

def create_tables():
    """Создает необходимые таблицы в базе данных"""
    logger.info(f"create_tables: путь к базе данных: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Создаем таблицу инструментов, если её нет
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'available',
                last_maintenance_date DATE,
                quantity INTEGER DEFAULT 1
            )
        ''')
        logger.info("create_tables: создана таблица tools")

        # Создаем таблицу выданных инструментов, если её нет
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS issued_tools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_id INTEGER,
                employee_name TEXT NOT NULL,
                issue_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                expected_return_date DATE,
                return_date DATETIME,
                FOREIGN KEY (tool_id) REFERENCES tools(id)
            )
        ''')
        logger.info("create_tables: создана таблица issued_tools")

        # Создаем таблицу истории инструментов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tool_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_id INTEGER,
                action TEXT NOT NULL,
                employee_name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tool_id) REFERENCES tools(id)
            )
        ''')
        logger.info("create_tables: создана таблица tool_history")

        # Создаем таблицу запросов на выдачу инструментов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS issue_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_id INTEGER,
                employee_name TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                request_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                FOREIGN KEY (tool_id) REFERENCES tools(id)
            )
        ''')
        logger.info("create_tables: создана таблица issue_requests")

        conn.commit()
        logger.info("create_tables: все таблицы успешно созданы")
    except Exception as e:
        logger.error(f"create_tables: ошибка при создании таблиц: {str(e)}")
        conn.rollback()
    finally:
        conn.close()

def get_tools():
    """Получает список всех инструментов"""
    logger.info("DEBUG: Получение списка инструментов")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT id, name, status, quantity FROM tools')
        tools = cursor.fetchall()
        logger.info(f"DEBUG: Получено {len(tools)} инструментов: {tools}")
        return tools
    except Exception as e:
        logger.error(f"DEBUG: Ошибка при получении списка инструментов: {e}")
        return []
    finally:
        conn.close()

def get_issued_tools():
    """Получить список выданных инструментов"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT t.id, t.name, i.employee_name, 
                   COALESCE(strftime('%Y-%m-%d', i.issue_date), date('now')) as issue_date,
                   COALESCE(strftime('%Y-%m-%d', i.expected_return_date), date('now', '+7 days')) as expected_return_date
            FROM tools t
            JOIN issued_tools i ON t.id = i.tool_id
            WHERE i.return_date IS NULL
        ''')
        issued_tools = cursor.fetchall()
        conn.close()
        return issued_tools
    except Exception as e:
        logger.error(f"Ошибка при получении списка выданных инструментов: {e}")
        return []

def get_admin_issued_tools():
    """Получить список выданных инструментов для админа"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT t.name, i.employee_name, 
                   COALESCE(strftime('%d.%m.%Y', i.issue_date), 'Не указана') as issue_date,
                   COALESCE(strftime('%d.%m.%Y', i.expected_return_date), 'Не указана') as expected_return_date
            FROM tools t
            JOIN issued_tools i ON t.id = i.tool_id
            WHERE i.return_date IS NULL
            ORDER BY i.expected_return_date ASC
        ''')
        issued_tools = cursor.fetchall()
        conn.close()
        return issued_tools
    except Exception as e:
        logger.error(f"Ошибка при получении списка выданных инструментов для админа: {e}")
        return []

def get_issued_tool_by_id(tool_id: int):
    """Получить информацию о выданном инструменте по ID"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT t.id, t.name, i.employee_name,
                   COALESCE(strftime('%Y-%m-%d', i.issue_date), date('now')) as issue_date,
                   COALESCE(strftime('%Y-%m-%d', i.expected_return_date), date('now', '+7 days')) as expected_return_date
            FROM tools t
            JOIN issued_tools i ON t.id = i.tool_id
            WHERE t.id = ? AND i.return_date IS NULL
        ''', (tool_id,))
        issued_tool = cursor.fetchone()
        conn.close()
        return issued_tool
    except Exception as e:
        logger.error(f"Ошибка при получении информации о выданном инструменте: {e}")
        return None

def return_tool(tool_id, employee_name):
    """Возвращает инструмент"""
    try:
        with DatabaseConnection() as cursor:
            # Проверяем, что инструмент был выдан
            cursor.execute('SELECT status FROM tools WHERE id = ?', (tool_id,))
            result = cursor.fetchone()
            
            if not result or result[0] != 'issued':
                logger.error(f"DEBUG: Попытка вернуть невыданный инструмент {tool_id}")
                return False
            
            # Проверяем, что инструмент был выдан указанному сотруднику
            cursor.execute('''
                SELECT employee_name FROM issued_tools
                WHERE tool_id = ?
            ''', (tool_id,))
            issue_record = cursor.fetchone()
            
            if not issue_record or issue_record[0] != employee_name:
                logger.error(f"DEBUG: Попытка вернуть инструмент {tool_id} от неверного сотрудника")
                return False
            
            # Удаляем запись о выдаче
            cursor.execute('''
                DELETE FROM issued_tools
                WHERE tool_id = ? AND employee_name = ?
            ''', (tool_id, employee_name))
            
            # Обновляем статус инструмента
            cursor.execute('''
                UPDATE tools
                SET status = 'available'
                WHERE id = ?
            ''', (tool_id,))
            
            # Добавляем запись в историю
            cursor.execute('''
                INSERT INTO tool_history (tool_id, action, employee_name)
                VALUES (?, 'return', ?)
            ''', (tool_id, employee_name))
            
            logger.info(f"DEBUG: Инструмент {tool_id} успешно возвращен от сотрудника {employee_name}")
            return True
            
    except Exception as e:
        logger.error(f"DEBUG: Ошибка при возврате инструмента: {str(e)}")
        return False

def get_tool_history():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT t.name, th.action, th.timestamp, th.employee_name, th.notes
        FROM tool_history th
        JOIN tools t ON th.tool_id = t.id
        ORDER BY th.timestamp DESC
    ''')
    
    history = cursor.fetchall()
    conn.close()
    return history

def get_overdue_tools(days_threshold=7):
    """Получает список просроченных инструментов"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                t.id,
                t.name,
                i.employee_name,
                i.issue_date,
                i.expected_return_date,
                t.quantity
            FROM tools t
            JOIN issued_tools i ON t.id = i.tool_id
            WHERE i.return_date IS NULL
            AND (
                (i.expected_return_date IS NOT NULL AND i.expected_return_date < datetime('now'))
                OR
                (i.issue_date < datetime('now', '-' || ? || ' days'))
            )
        ''', (days_threshold,))
        
        overdue = cursor.fetchall()
        conn.close()
        return overdue
    except Exception as e:
        logger.error(f"Ошибка при получении просроченных инструментов: {e}")
        return []

def is_tool_issued(tool_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM issued_tools WHERE tool_id = ?', (tool_id,))
    count = cursor.fetchone()[0]
    
    conn.close()
    return count > 0

def issue_tool(tool_id, employee_name):
    try:
        with DatabaseConnection() as cursor:
            # Проверяем, не выдан ли уже инструмент
            if is_tool_issued(tool_id):
                raise Exception("Инструмент уже выдан")
            
            # Добавляем запись о выдаче
            cursor.execute('''
                INSERT INTO issued_tools (tool_id, employee_name)
                VALUES (?, ?)
            ''', (tool_id, employee_name))
            
            # Обновляем статус в tools
            cursor.execute('UPDATE tools SET status = "issued" WHERE id = ?', (tool_id,))
            
            # Добавляем запись в историю
            cursor.execute('''
                INSERT INTO tool_history (tool_id, action, employee_name)
                VALUES (?, 'issue', ?)
            ''', (tool_id, employee_name))
            
    except Exception as e:
        raise e

def create_tool_request(tool_id: int, employee_name: str, chat_id: int) -> bool:
    """Создает запрос на выдачу инструмента"""
    try:
        with DatabaseConnection() as cursor:
            # Проверяем, не выдан ли уже инструмент
            cursor.execute('SELECT status FROM tools WHERE id = ?', (tool_id,))
            result = cursor.fetchone()
            if not result or result[0] == 'issued':
                return False

            # Создаем запрос
            cursor.execute('''
                INSERT INTO issue_requests (tool_id, employee_name, chat_id)
                VALUES (?, ?, ?)
            ''', (tool_id, employee_name, chat_id))
            
            logger.info(f"DEBUG: Создан запрос на выдачу инструмента {tool_id} сотруднику {employee_name}")
            return True
    except Exception as e:
        logger.error(f"DEBUG: Ошибка при создании запроса на выдачу: {str(e)}")
        return False

def get_issue_request_info(tool_id: int, chat_id: int):
    """Получает информацию о запросе на выдачу"""
    try:
        with DatabaseConnection() as cursor:
            cursor.execute('''
                SELECT r.id, r.tool_id, r.employee_name, r.status, t.name
                FROM issue_requests r
                JOIN tools t ON t.id = r.tool_id
                WHERE r.tool_id = ? AND r.chat_id = ? AND r.status = 'pending'
            ''', (tool_id, chat_id))
            return cursor.fetchone()
    except Exception as e:
        logger.error(f"DEBUG: Ошибка при получении информации о запросе: {str(e)}")
        return None

def approve_issue_request(tool_id: int, chat_id: int) -> bool:
    """Одобряет запрос на выдачу инструмента"""
    try:
        with DatabaseConnection() as cursor:
            # Получаем информацию о запросе
            cursor.execute('''
                SELECT employee_name
                FROM issue_requests
                WHERE tool_id = ? AND chat_id = ? AND status = 'pending'
            ''', (tool_id, chat_id))
            request = cursor.fetchone()
            
            if not request:
                logger.error(f"DEBUG: Запрос на выдачу не найден: tool_id={tool_id}, chat_id={chat_id}")
                return False
            
            employee_name = request[0]
            
            # Проверяем статус инструмента
            cursor.execute('SELECT status FROM tools WHERE id = ?', (tool_id,))
            tool = cursor.fetchone()
            if not tool or tool[0] != 'available':
                logger.error(f"DEBUG: Инструмент недоступен для выдачи: {tool_id}")
                return False
            
            # Обновляем статус инструмента
            cursor.execute('''
                UPDATE tools
                SET status = 'issued'
                WHERE id = ? AND status = 'available'
            ''', (tool_id,))
            
            # Добавляем запись в issued_tools
            cursor.execute('''
                INSERT INTO issued_tools (tool_id, employee_name)
                VALUES (?, ?)
            ''', (tool_id, employee_name))
            
            # Обновляем статус запроса
            cursor.execute('''
                UPDATE issue_requests
                SET status = 'approved'
                WHERE tool_id = ? AND chat_id = ? AND status = 'pending'
            ''', (tool_id, chat_id))
            
            # Добавляем запись в историю
            cursor.execute('''
                INSERT INTO tool_history (tool_id, action, employee_name)
                VALUES (?, 'issue', ?)
            ''', (tool_id, employee_name))
            
            logger.info(f"DEBUG: Запрос на выдачу одобрен: tool_id={tool_id}, employee={employee_name}")
            return True
            
    except Exception as e:
        logger.error(f"DEBUG: Ошибка при одобрении запроса: {str(e)}")
        return False

def reject_issue_request(tool_id: int, chat_id: int) -> bool:
    """Отклоняет запрос на выдачу инструмента"""
    try:
        with DatabaseConnection() as cursor:
            cursor.execute('''
                UPDATE issue_requests
                SET status = 'rejected'
                WHERE tool_id = ? AND chat_id = ? AND status = 'pending'
            ''', (tool_id, chat_id))
            
            if cursor.rowcount > 0:
                logger.info(f"DEBUG: Запрос на выдачу отклонен: tool_id={tool_id}, chat_id={chat_id}")
                return True
            else:
                logger.error(f"DEBUG: Запрос на выдачу не найден: tool_id={tool_id}, chat_id={chat_id}")
                return False
                
    except Exception as e:
        logger.error(f"DEBUG: Ошибка при отклонении запроса: {str(e)}")
        return False

def create_tool(name, quantity=1, description=None):
    """Создает новый инструмент"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO tools (name, description, status, quantity)
            VALUES (?, ?, 'available', ?)
        ''', (name, description, quantity))
        
        tool_id = cursor.lastrowid
        
        conn.commit()
        return tool_id
    except Exception as e:
        logger.error(f"Ошибка при создании инструмента: {e}")
        return None
    finally:
        conn.close()

def get_tool_by_id(tool_id: int):
    """Получает информацию об инструменте по его ID"""
    try:
        with DatabaseConnection() as cursor:
            cursor.execute('''
                SELECT id, name, status, quantity, description
                FROM tools
                WHERE id = ?
            ''', (tool_id,))
            return cursor.fetchone()
    except Exception as e:
        logger.error(f"Ошибка при получении инструмента по ID: {e}")
        return None

def update_tool_status(tool_id: int, status: str):
    """Обновляет статус инструмента"""
    try:
        with DatabaseConnection() as cursor:
            cursor.execute('''
                UPDATE tools
                SET status = ?
                WHERE id = ?
            ''', (status, tool_id))
            cursor.connection.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса инструмента: {e}")
        return False

def add_tool_history(tool_id: int, action: str, employee_name: str):
    """Добавляет запись в историю инструмента"""
    try:
        with DatabaseConnection() as cursor:
            cursor.execute('''
                INSERT INTO tool_history (tool_id, action, employee_name, timestamp)
                VALUES (?, ?, ?, datetime('now'))
            ''', (tool_id, action, employee_name))
            cursor.connection.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка при добавлении записи в историю: {e}")
        return False

def get_all_issue_requests():
    """Получает все запросы на выдачу инструментов"""
    try:
        with DatabaseConnection() as cursor:
            cursor.execute('''
                SELECT r.id, t.name, r.employee_name, r.chat_id, r.request_date, r.status
                FROM issue_requests r
                JOIN tools t ON r.tool_id = t.id
                ORDER BY r.request_date DESC
            ''')
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"Ошибка при получении списка запросов: {e}")
        return []