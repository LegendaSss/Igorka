# Telegram Bot

Telegram бот, написанный на Python с использованием библиотеки aiogram.

## Установка

1. Клонируйте репозиторий
2. Создайте виртуальное окружение: `python -m venv venv`
3. Активируйте виртуальное окружение:
   - Windows: `venv\Scripts\activate`
   - Linux/Mac: `source venv/bin/activate`
4. Установите зависимости: `pip install -r requirements.txt`
5. Создайте файл `.env` и добавьте ваш токен бота:
   ```
   BOT_TOKEN=your_bot_token_here
   ```
6. Запустите бота: `python bot.py`

## Структура проекта

- `bot.py` - основной файл бота
- `config.py` - конфигурация
- `db.py` - работа с базой данных
- `populate_database.py` - скрипт для заполнения базы данных
- `check_db.py` - утилита для проверки базы данных
- `wsgi.py` - файл для веб-сервера
