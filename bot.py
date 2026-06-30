import asyncio
import os
import re
import uuid
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile
from dotenv import load_dotenv

from parser_5ka import parse_5ka_url
from excel_writer import create_excel_file


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN. Создай файл .env и добавь BOT_TOKEN=...")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def is_5ka_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        return "5ka.ru" in domain
    except Exception:
        return False


def extract_url(text: str) -> str | None:
    if not text:
        return None

    match = URL_RE.search(text)

    if not match:
        return None

    return match.group(0).strip()


async def run_parser_in_thread(url: str, scroll_steps: int = 10):
    """
    Playwright sync-код запускаем в отдельном потоке,
    чтобы не блокировать event loop Telegram-бота.
    """
    return await asyncio.to_thread(parse_5ka_url, url, scroll_steps)


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "Привет! Я могу собрать товары, бренды и поставщиков с 5ka в Excel.\n\n"
        "Просто отправь мне ссылку на страницу 5ka.\n\n"
        "Лучше отправлять ссылку на конкретную категорию или поисковую страницу, "
        "а не главную страницу."
    )


@dp.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(
        "Как пользоваться:\n\n"
        "1. Открой 5ka.ru.\n"
        "2. Найди нужную категорию или поиск.\n"
        "3. Скопируй ссылку.\n"
        "4. Отправь ссылку мне.\n\n"
        "Я соберу данные и отправлю Excel-файл."
    )


@dp.message(F.text)
async def parse_link_handler(message: Message):
    text = message.text or ""
    url = extract_url(text)

    if not url:
        await message.answer(
            "Отправь ссылку на страницу 5ka, например:\n"
            "https://5ka.ru/"
        )
        return

    if not is_5ka_url(url):
        await message.answer(
            "Пока я умею работать только со ссылками 5ka.ru."
        )
        return

    status_message = await message.answer(
        "Принял ссылку.\n"
        "Запускаю парсер, собираю товары и готовлю Excel..."
    )

    try:
        result = await run_parser_in_thread(url, scroll_steps=10)

        rows = result["rows"]
        products_count = result["products_count"]
        rows_count = result["rows_count"]

        if not rows:
            await status_message.edit_text(
                "Не удалось найти товары на этой странице.\n\n"
                "Попробуй отправить ссылку на конкретную категорию или страницу поиска."
            )
            return

        file_id = str(uuid.uuid4())[:8]
        output_path = os.path.join("output", f"5ka_suppliers_{file_id}.xlsx")

        create_excel_file(
            rows=rows,
            output_path=output_path,
            source_url=url,
        )

        document = FSInputFile(output_path)

        await message.answer_document(
            document=document,
            caption=(
                "Готово.\n\n"
                f"Найдено товаров: {products_count}\n"
                f"Строк в Excel: {rows_count}\n\n"
                "Если в файле указано «Не найдено на карточке товара», "
                "значит сайт не отдал производителя/поставщика в доступных данных."
            ),
        )

        await status_message.delete()

        try:
            os.remove(output_path)
        except Exception:
            pass

    except Exception as e:
        await status_message.edit_text(
            "Произошла ошибка при сборе данных.\n\n"
            f"Текст ошибки:\n{e}"
        )


async def main():
    os.makedirs("output", exist_ok=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
