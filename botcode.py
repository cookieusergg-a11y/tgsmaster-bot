import asyncio
import random
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

TOKEN = "ТВОЙ_ТОКЕН_БОТА"
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Храним коды: {user_id: (code, timestamp)}
codes = {}

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "👋 Привет! Чтобы войти на сайт, отправь мне свой Telegram ID.\n"
        "Я пришлю тебе код для входа.\n"
        "Или просто нажми кнопку 'Получить код' на сайте."
    )

@dp.message()
async def handle_code_request(message: types.Message):
    user_id = message.from_user.id
    # Проверяем, не запрашивал ли он код недавно (защита от спама)
    last_code = codes.get(user_id)
    if last_code and time.time() - last_code[1] < 30:
        await message.answer("⏳ Подожди 30 секунд перед новым запросом.")
        return
    
    # Генерируем 6-значный код
    code = f"{random.randint(100000, 999999)}"
    codes[user_id] = (code, time.time())
    await message.answer(f"🔑 Твой код для входа: `{code}`\nВведи его на сайте.", parse_mode="Markdown")
    await message.answer("Код действителен 5 минут.")

# Запуск бота
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())