import sqlite3
import os
from db import create_tables

def clear_database():
    """Очищает все таблицы в базе данных"""
    print("Очистка базы данных...")
    data_dir = os.getenv('DATA_DIR', '.')
    db_path = os.path.join(data_dir, 'tools.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Удаляем данные из всех таблиц
        cursor.execute('DROP TABLE IF EXISTS tool_history')
        cursor.execute('DROP TABLE IF EXISTS issued_tools')
        cursor.execute('DROP TABLE IF EXISTS issue_requests')
        cursor.execute('DROP TABLE IF EXISTS tools')
        
        # Сбрасываем автоинкремент
        cursor.execute('DELETE FROM sqlite_sequence')
        
        conn.commit()
        print("База данных очищена")
    except Exception as e:
        print(f"Ошибка при очистке базы данных: {str(e)}")
        conn.rollback()
    finally:
        conn.close()

def populate_database():
    """Заполняет базу данных начальными данными"""
    # Очищаем базу данных
    clear_database()
    
    # Создаем таблицы
    create_tables()
    
    # Список инструментов и их количество
    tools = [
        ("Milwaukee - Болгарка", 5),
        ("Milwaukee - Перфоратор", 3),
        ("Milwaukee - Сабельная пила", 4),
        ("Milwaukee - Шуруповёрт", 6),
        ("Milwaukee - Пылеуловитель для перфоратора", 2),
        ("Milwaukee - Зарядка для аккумуляторов", 3),
        ("Milwaukee - Аккумулятор", 10),
        ("Toua Газовый монтажный пистолет", 2),
        ("Bosch - Перфоратор", 3),
        ("Makita - Перфоратор", 3),
        ("Makita - Сабельная пила", 2),
        ("Makita - Болгарка xLock", 4),
        ("Makita - Проводная болгарка", 3),
        ("Пылесос для модулей Makita", 1),
        ("Makita - Станция", 1),
        ("Makita - Зарядная станция", 2),
        ("SHTOK - Лестница 2.6м", 2),
        ("Лестница - 6 ступеней", 2),
        ("Лестница - 7 ступеней", 2),
        ("Лестница 3 секции - 7 ступеней", 1),
        ("Удлинитель - 50 метров", 2),
        ("Удлинитель - 30 метров", 2),
        ("Стол для производства", 1),
        ("Насадка для перфоратора", 5),
        ("CONDTROL - Лазерный уровень", 1),
        ("ROCODIL - Лазерный уровень", 1),
        ("Пылесос", 1),
        ("REXANT - Инфракрасный пирометр", 2),
        ("LIXE - Пороховой монтажный пистолет", 1)
    ]
    
    # Добавляем инструменты
    data_dir = os.getenv('DATA_DIR', '.')
    db_path = os.path.join(data_dir, 'tools.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        for tool_name, quantity in tools:
            # Добавляем инструменты напрямую в базу данных
            for _ in range(quantity):
                cursor.execute('''
                    INSERT INTO tools (name, status)
                    VALUES (?, 'available')
                ''', (tool_name,))
            print(f"✅ Добавлен инструмент: {tool_name} (количество: {quantity})")
        
        conn.commit()
    except Exception as e:
        print(f"❌ Ошибка при добавлении инструментов: {str(e)}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    populate_database()