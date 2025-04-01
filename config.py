import os

# config.py
API_TOKEN = "7684943328:AAHR76K3U8ckNS6OohIIiyS9VooIDgOOQ4Y"  # Возвращаем оригинальный токен
ADMIN_ID = 1495719377  # ID администратора в Telegram

# Webhook settings
WEBHOOK_HOST = 'https://igorka-bot.onrender.com'  # The base URL for your webhook
WEBHOOK_PATH = '/webhook/'  # The URL path where the webhook will receive updates
WEBAPP_HOST = '0.0.0.0'  # The host to bind the web server to
WEBAPP_PORT = int(os.environ.get('PORT', 8000))  # The port to run the web server on