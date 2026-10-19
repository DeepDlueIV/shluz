"""Ограничение тела до JSON-парсинга и SQL rate limit для всех веб-маршрутов."""
import asyncio
import time

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.product.service import locked_session
from app.webaccess.security import client_network, consume


class WebPerimeter:
    def __init__(self, app, settings, database):
        self.app, self.settings, self.database = app, settings, database

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/v1/web/'):
            return await self.app(scope, receive, send)
        request = Request(scope)
        s = self.settings
        if not s.enabled:
            return await JSONResponse({'error': {'code': 'web_not_configured',
                                      'message': 'Вход на сайте пока не подключён.'}}, 503)(scope, receive, send)
        origin = request.headers.get('origin')
        cors = {'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Credentials': 'true',
                'Vary': 'Origin'} if origin in s.origins else {}
        if scope['method'] == 'OPTIONS':
            if not cors:
                return await Response(status_code=403)(scope, receive, send)
            cors.update({'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                         'Access-Control-Allow-Headers': 'Content-Type, X-CSRF-Token',
                         'Access-Control-Max-Age': '600'})
            return await Response(status_code=204, headers=cors)(scope, receive, send)
        started = False
        async def safe_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                headers = list(message.get('headers', []))
                headers.extend((k.lower().encode(), v.encode()) for k, v in cors.items())
                headers.extend([(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff')])
                message = {**message, 'headers': headers}
            await send(message)
        try:
            _, network = client_network(request, s)
            def rate():
                with locked_session(self.database) as db:
                    consume(db, 'http:' + network, 120, 60)
                    consume(db, 'http:global', 2000, 60)
            await run_in_threadpool(rate)
            if scope['method'] not in {'GET', 'HEAD'}:
                if origin not in s.origins:
                    raise HTTPException(403, detail={'code': 'origin_rejected'})
                if request.headers.get('content-encoding', 'identity') != 'identity':
                    raise HTTPException(415, detail={'code': 'encoding_unsupported'})
                try:
                    length = int(request.headers.get('content-length', '0'))
                    if length < 0:
                        raise ValueError()
                except ValueError:
                    raise HTTPException(400, detail={'code': 'invalid_length'}) from None
                if length > 32768:
                    raise HTTPException(413, detail={'code': 'body_too_large'})
                data = bytearray()
                async with asyncio.timeout(10):
                    while True:
                        message = await receive()
                        if message['type'] == 'http.disconnect':
                            return
                        chunk = message.get('body', b'')
                        if len(data) + len(chunk) > 32768:
                            raise HTTPException(413, detail={'code': 'body_too_large'})
                        data.extend(chunk)
                        if not message.get('more_body', False):
                            break
                if data and request.headers.get('content-type', '').split(';')[0] != 'application/json':
                    raise HTTPException(415, detail={'code': 'json_required'})
                delivered = False
                original_receive = receive
                async def bounded_receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {'type': 'http.request', 'body': bytes(data), 'more_body': False}
                    return await original_receive()
                receive = bounded_receive
            await self.app(scope, receive, safe_send)
        except HTTPException as exc:
            if started:
                raise
            detail = exc.detail if isinstance(exc.detail, dict) else {'code': 'request_rejected'}
            headers = {'Retry-After': '60'} if exc.status_code == 429 else {}
            await JSONResponse({'error': detail}, exc.status_code, headers=headers)(scope, receive, safe_send)
        except (SQLAlchemyError, TimeoutError):
            if started:
                raise
            await JSONResponse({'error': {'code': 'temporarily_unavailable',
                                         'message': 'Сервис временно недоступен.'}}, 503)(scope, receive, safe_send)
