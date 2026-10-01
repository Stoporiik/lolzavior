import asyncio
from aiogram import Bot

async def safe_start():
    bot = Bot(token="ТОКЕН БОТА")
    for i in range(5):
        try:
            await bot.get_me()
            print("Бот поднят")
            return bot
        except Exception as e:
            print(f"Попытка {i+1} неудачна: {e}")
            await asyncio.sleep(5)
    raise SystemExit("Не удалось подключиться к Telegram")

asyncio.run(safe_start())