import sqlite3

def check_database():
    conn = sqlite3.connect('tools.db')
    cursor = conn.cursor()
    
    # Проверяем таблицы
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print("\nТаблицы в базе данных:")
    for table in tables:
        print(f"- {table[0]}")
    
    # Проверяем содержимое таблицы tools
    print("\nСодержимое таблицы tools:")
    cursor.execute("SELECT * FROM tools")
    tools = cursor.fetchall()
    for tool in tools:
        print(f"ID: {tool[0]}, Название: {tool[1]}, Статус: {tool[3]}")
    
    conn.close()

if __name__ == "__main__":
    check_database()
