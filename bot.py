import asyncio
import os
import re
import uuid
import socket
import ipaddress
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile
from dotenv import load_dotenv

try:
    from parser_5ka import parse_any_url
except ImportError:
    from parser_5ka import parse_5ka_url as parse_any_url
from excel_writer import create_excel_file


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN. Создай файл .env и добавь BOT_TOKEN=...")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def extract_url(text: str) -> str | None:
    if not text:
        return None

    match = URL_RE.search(text)

    if not match:
        return None

    return match.group(0).strip()


def is_public_ip(ip: str) -> bool:
    """
    Защита сервера.
    Запрещаем localhost, приватные IP и внутренние адреса.
    """
    try:
        ip_obj = ipaddress.ip_address(ip)

        return not (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
        )
    except ValueError:
        return False


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """
    Проверяем, что пользователь прислал обычную публичную http/https ссылку.
    Это важно, потому что бот будет открывать ссылку с сервера.
    """
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ["http", "https"]:
            return False, "Разрешены только ссылки http:// или https://."

        if not parsed.netloc:
            return False, "Некорректная ссылка."

        hostname = parsed.hostname

        if not hostname:
            return False, "Не удалось определить домен ссылки."

        blocked_hosts = {
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
        }

        if hostname.lower() in blocked_hosts:
            return False, "Нельзя отправлять локальные адреса."

        try:
            ip = socket.gethostbyname(hostname)
        except socket.gaierror:
            return False, "Не удалось определить IP-адрес сайта."

        if not is_public_ip(ip):
            return False, "Нельзя отправлять внутренние или приватные адреса."

        return True, ""

    except Exception:
        return False, "Ошибка проверки ссылки."


async def run_parser_in_thread(url: str, scroll_steps: int = 10):
    """
    Playwright sync-код запускаем в отдельном потоке,
    чтобы не блокировать Telegram-бота.
    """
    return await asyncio.to_thread(parse_any_url, url, scroll_steps)


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "Привет! Я могу собрать товары, бренды и производителей/поставщиков "
        "с публичной страницы сайта в Excel.\n\n"
        "Просто отправь мне ссылку на страницу с товарами.\n\n"
        "Лучше отправлять ссылку на конкретную категорию, каталог или страницу поиска."
    )


@dp.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(
        "Как пользоваться:\n\n"
        "1. Открой сайт с товарами.\n"
        "2. Перейди в нужную категорию, каталог или поиск.\n"
        "3. Скопируй ссылку.\n"
        "4. Отправь ссылку мне.\n\n"
        "Я попробую собрать данные и отправлю Excel-файл.\n\n"
        "Важно: не все сайты отдают производителя или поставщика. "
        "Если таких данных нет в карточке/API сайта, в Excel будет указано, что данные не найдены."
    )


@dp.message(F.text)
async def parse_link_handler(message: Message):
    text = message.text or ""
    url = extract_url(text)

    if not url:
        await message.answer(
            "Отправь ссылку на страницу с товарами, например:\n"
            "https://example.com/catalog"
        )
        return

    is_safe, error_text = is_safe_public_url(url)

    if not is_safe:
        await message.answer(
            f"Я не могу открыть эту ссылку.\n\nПричина: {error_text}"
        )
        return

    status_message = await message.answer(
        "Принял ссылку.\n"
        "Запускаю парсер, собираю данные и готовлю Excel..."
    )

    try:
        result = await run_parser_in_thread(url, scroll_steps=10)

        rows = result["rows"]
        products_count = result["products_count"]
        rows_count = result["rows_count"]

        if not rows:
            await status_message.edit_text(
                "Не удалось найти товары на этой странице.\n\n"
                "Попробуй отправить ссылку на конкретную категорию, каталог или страницу поиска."
            )
            return

        file_id = str(uuid.uuid4())[:8]
        output_path = os.path.join("output", f"parsed_suppliers_{file_id}.xlsx")

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
