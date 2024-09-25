#!/bin/env python3

from dotenv import load_dotenv
load_dotenv()

import os, sys, signal
import asyncio
from pymongo import MongoClient
from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.filters.command import Command
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback
import logging
from datetime import datetime, timezone

TOKEN = os.getenv('TOKEN')
dp = Dispatcher()

mongoClient = MongoClient(os.getenv("MONGO_URI"))
db = mongoClient["market_bot"]

# Словарь для временного хранения информации о пользователях (название продукта и дата)
user_data = {}

# Функция проверки пользователя
async def validate_user(message):
    user = db["users"].find_one({"id": message.from_user.id})
    if not user:
        await message.reply("Извините, но Вам запрещено использовать этого бота 😓")
        return False
    else:
        return True

# Команда /start
@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    if not await validate_user(message):
        return
    await message.reply("<b>Мануал по использованию бота:</b>\n\n/start - мануал\n/add - добавить новую поставку\n/all - список добавленых продуктов")

# Команда /add для добавления продукта
@dp.message(Command('add'))
async def add_command(message: Message) -> None:
    if not await validate_user(message):
        return
    await message.answer("Пожалуйста, введите название продукта:")
    user_data[message.from_user.id] = {"step": "name"}

# Команда /all для того, чтобы посмотреть список добавленых продуктов
@dp.message(Command('all'))
async def add_command(message: Message) -> None:
    if not await validate_user(message):
        return
    products = db['products'].find()  # Получаем все продукты
    message_to_reply = ""
    for product in products:  # Используем async for, так как find() возвращает курсор
        product_name = product.get('product_name', 'Неизвестный продукт')  # Получаем название продукта
        expiry_date = product.get('expiry_date', 'Дата неизвестна').get("human", 'Дата неизвестна')  # Получаем дату истечения
        message_to_reply += f"{product_name} -> {expiry_date}\n"  # Формируем сообщение

    if message_to_reply:
        await message.reply(message_to_reply)
    else:
        await message.reply("Нет доступных продуктов.")

# Обработка текста (для получения названия продукта)
@dp.message()
async def any_message(message: Message) -> None:
    if message.from_user.id in user_data and user_data[message.from_user.id]["step"] == "name":
        product_name = message.text
        user_data[message.from_user.id]["name"] = product_name
        user_data[message.from_user.id]["step"] = "date"
        await message.answer("Теперь выберите дату истечения срока годности:", reply_markup=await SimpleCalendar().start_calendar())
    else:
        await validate_user(message)

# Обработка выбора даты из календаря
@dp.callback_query(SimpleCalendarCallback.filter())
async def process_simple_calendar(callback_query: CallbackQuery, callback_data: SimpleCalendarCallback):
    selected, date = await SimpleCalendar().process_selection(callback_query, callback_data)
    if selected:
        user_id = callback_query.from_user.id
        if user_id in user_data and user_data[user_id]["step"] == "date":
            product_name = user_data[user_id]["name"]
            # Сохранение продукта в базу данных
            db["products"].insert_one({
                "user_id": user_id,
                "product_name": product_name,
                "expiry_date": {
                    "human": date.strftime('%Y-%m-%d'),
                    "iso": date.timestamp() * 1000
                }
            })
            # Очистка временных данных пользователя
            del user_data[user_id]
            await callback_query.message.answer(f"Продукт '{product_name}' добавлен с датой истечения срока {date}")
        else:
            await callback_query.message.answer("Что-то пошло не так. Попробуйте снова.")

# Фоновая задача для проверки просроченных продуктов
async def check_expired_products(bot: Bot):
    while True:
        now = datetime.now().timestamp() * 1000
        # Найти все просроченные продукты
        expired_products = db['products'].find()

        for product in expired_products:
            user_id = product.get('user_id')
            product_name = product.get("product_name")
            expiry_date = product.get("expiry_date", {}).get("iso")
            expiry_date_human = product.get("expiry_date", {}).get("human")

            if expiry_date <= now:
                try:
                    await bot.send_message(user_id, f"❗❗❗ Продукт <b>'{product_name}'</b> с датой истечения {expiry_date_human} уже просрочен! ❗❗❗")
                except Exception as e:
                    logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
                    return
                # Удаляем продукт из базы данных
                db['products'].delete_one({"_id": product["_id"]})

        await asyncio.sleep(10)  # Проверять каждые 10 сек

# Главная функция
async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(check_expired_products(bot))
    await dp.start_polling(bot)

# Обработка сигнала завершения работы
def handler(signum, frame):
    print("Caught SIGTERM")
    sys.exit(1)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handler)
    try:
        print("Starting MarketBot")
        logging.basicConfig(level=logging.INFO, stream=sys.stdout)
        asyncio.run(main())
    except SystemExit:
        print("Stopping MarketBot")
    except Exception as e:
        print(f"Crash! {e}")
        print("Trying to stop MarketBot")
