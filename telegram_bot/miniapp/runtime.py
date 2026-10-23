"""Сервер панели делит цикл событий, историю и блокировку экземпляра с polling-ботом."""

import asyncio
from contextlib import contextmanager, suppress

from aiogram.exceptions import TelegramAPIError
from aiogram.types import MenuButtonCommands, MenuButtonDefault, MenuButtonWebApp, WebAppInfo


async def configure_menu(bot, settings):
    button = (
        MenuButtonWebApp(text="Меню", web_app=WebAppInfo(url=settings.miniapp_url))
        if settings.interface == "miniapp"
        else MenuButtonCommands()
    )
    if settings.trial_owner_id:
        await bot.set_chat_menu_button(chat_id=settings.trial_owner_id, menu_button=button)
    else:
        await bot.set_chat_menu_button(menu_button=button)


async def open_menu(message, controller):
    # Убрать только известное служебное меню прежнего режима, не ответы пользователя/модели.
    old = controller.store.screen_id(message.bot.id, message.chat.id)
    if old:
        await controller.delivery.retire(message, old)
    if controller.settings.trial_owner_id:
        await configure_menu(message.bot, controller.settings)
    else:
        await message.bot.set_chat_menu_button(
            chat_id=message.chat.id, menu_button=MenuButtonDefault()
        )
    with suppress(TelegramAPIError):
        await message.delete()  # Только отправленная самим пользователем slash-команда.


async def poll_with_panel(controller, dispatcher, bot, **polling_options):
    if controller.settings.interface != "miniapp":
        await configure_menu(bot, controller.settings)
        return await dispatcher.start_polling(bot, **polling_options)
    import uvicorn

    from telegram_bot.miniapp.server import create_miniapp

    class Server(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            # SIGTERM/CTRL+C обслуживает dispatcher, а не второй обработчик.
            yield

    server = Server(
        uvicorn.Config(
            create_miniapp(controller),
            host=controller.settings.miniapp_host,
            port=controller.settings.miniapp_port,
            access_log=False,
            log_config=None,
            proxy_headers=False,
            server_header=False,
        )
    )
    serving = asyncio.create_task(server.serve())
    polling = None
    try:
        for _ in range(100):
            if serving.done():
                await serving
                raise RuntimeError("Mini App HTTP server stopped")
            if server.started:
                break
            await asyncio.sleep(0.05)
        if not server.started:
            raise RuntimeError("Mini App HTTP server did not start")
        await configure_menu(bot, controller.settings)
        polling = asyncio.create_task(dispatcher.start_polling(bot, **polling_options))
        done, _ = await asyncio.wait({serving, polling}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
        if serving in done and not server.should_exit:
            raise RuntimeError("Mini App server stopped while polling")
    finally:
        if polling and not polling.done():
            with suppress(RuntimeError):
                await dispatcher.stop_polling()
            polling.cancel()
            await asyncio.gather(polling, return_exceptions=True)
        server.should_exit = True
        try:
            await asyncio.wait_for(serving, timeout=10)
        except TimeoutError:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
