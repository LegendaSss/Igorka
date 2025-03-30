import sys
import os

path = '/home/Rasal/telegrambot'
if path not in sys.path:
    sys.path.append(path)

from bot import dp, bot

async def application(environ, start_response):
    if environ['REQUEST_METHOD'] == 'POST':
        from aiohttp.web import Response
        
        # Получаем данные запроса
        request_body_size = int(environ.get('CONTENT_LENGTH', 0))
        request_body = environ['wsgi.input'].read(request_body_size)
        
        # Обрабатываем update
        await dp.process_update(request_body)
        
        return Response(status=200)
