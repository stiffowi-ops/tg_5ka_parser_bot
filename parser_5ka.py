import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


PRODUCT_HINT_KEYS = {
    "name",
    "title",
    "product_name",
    "display_name",
    "productName",
    "brand",
    "brand_name",
    "brandName",
    "manufacturer",
    "manufacturer_name",
    "manufacturerName",
    "vendor",
    "vendor_name",
    "supplier",
    "supplier_name",
    "producer",
    "producer_name",
    "category",
    "category_name",
    "categoryName",
    "url",
    "slug",
    "link",
    "href",
}

NAME_KEYS = [
    "name",
    "title",
    "product_name",
    "productName",
    "display_name",
    "displayName",
    "caption",
]

BRAND_KEYS = [
    "brand",
    "brand_name",
    "brandName",
    "trade_mark",
    "trademark",
    "tradeMark",
    "tm",
]

MANUFACTURER_KEYS = [
    "manufacturer",
    "manufacturer_name",
    "manufacturerName",
    "producer",
    "producer_name",
    "producerName",
    "vendor",
    "vendor_name",
    "vendorName",
    "supplier",
    "supplier_name",
    "supplierName",
    "company",
    "company_name",
    "companyName",
    "legal_entity",
    "legalEntity",
    "organization",
    "organization_name",
]

CATEGORY_KEYS = [
    "category",
    "category_name",
    "categoryName",
    "section",
    "section_name",
    "parent_category",
    "parentCategory",
    "group",
    "group_name",
]

PRICE_KEYS = [
    "price",
    "current_price",
    "currentPrice",
    "regular_price",
    "regularPrice",
    "old_price",
    "oldPrice",
]


def ensure_playwright_chromium():
    """
    Проверяет наличие Chromium для Playwright.

    Важно:
    - Эта функция вызывается только при запросе на парсинг.
    - При запуске Telegram-бота Chromium не запускается и не устанавливается.
    - Браузер сохраняется в папку .playwright-browsers, если не задана другая переменная.
    """
    browsers_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH", ".playwright-browsers")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path

    browsers_dir = Path(browsers_path)

    chromium_exists = False

    if browsers_dir.exists():
        for path in browsers_dir.rglob("*"):
            if path.name in {"chrome", "chrome-headless-shell"} and path.is_file():
                chromium_exists = True
                break

    if chromium_exists:
        return

    print("Chromium для Playwright не найден. Устанавливаю chromium...", flush=True)

    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "Не удалось установить Chromium для Playwright. "
            "На Bothost проверь, хватает ли места и разрешена ли установка браузеров. "
            "Также попробуй добавить переменную окружения "
            "PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers"
        ) from error

    print("Chromium для Playwright установлен.", flush=True)


def clean_text(value):
    if value is None:
        return ""

    if isinstance(value, (int, float)):
        return str(value)

    value = str(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def get_by_keys(obj, keys):
    """
    Ищет первое непустое значение по списку ключей.
    Работает с обычными dict, вложенными dict и простыми списками.
    """
    if not isinstance(obj, dict):
        return ""

    for key in keys:
        if key not in obj:
            continue

        value = obj.get(key)

        if not value:
            continue

        if isinstance(value, dict):
            nested = get_by_keys(
                value,
                NAME_KEYS
                + BRAND_KEYS
                + MANUFACTURER_KEYS
                + CATEGORY_KEYS
                + PRICE_KEYS,
            )

            if nested:
                return nested

        elif isinstance(value, list):
            values = []

            for item in value:
                if isinstance(item, dict):
                    nested = get_by_keys(
                        item,
                        NAME_KEYS
                        + BRAND_KEYS
                        + MANUFACTURER_KEYS
                        + CATEGORY_KEYS
                        + PRICE_KEYS,
                    )

                    if nested:
                        values.append(nested)
                else:
                    text = clean_text(item)

                    if text:
                        values.append(text)

            result = "; ".join(values)

            if result:
                return result

        else:
            text = clean_text(value)

            if text:
                return text

    return ""


def looks_like_product_dict(obj):
    """
    Проверяет, похож ли dict на карточку товара.
    """
    if not isinstance(obj, dict):
        return False

    keys_lower = {str(k).lower() for k in obj.keys()}

    has_product_key = bool(keys_lower & {k.lower() for k in PRODUCT_HINT_KEYS})

    has_name = any(k in obj and clean_text(obj.get(k)) for k in NAME_KEYS)

    has_secondary_product_sign = any(
        k in obj and clean_text(obj.get(k))
        for k in BRAND_KEYS + CATEGORY_KEYS + PRICE_KEYS + ["url", "link", "href", "slug"]
    )

    return has_product_key and has_name and has_secondary_product_sign


def find_product_dicts(data):
    """
    Рекурсивно проходит по JSON и вытаскивает объекты, похожие на товары.
    """
    found = []

    if isinstance(data, dict):
        if looks_like_product_dict(data):
            found.append(data)

        for value in data.values():
            found.extend(find_product_dicts(value))

    elif isinstance(data, list):
        for item in data:
            found.extend(find_product_dicts(item))

    return found


def extract_from_characteristics(raw):
    """
    Достаёт бренд/производителя/категорию из характеристик,
    если сайт отдаёт их не отдельными полями.
    """
    result = {
        "brand": "",
        "manufacturer": "",
        "category": "",
    }

    if not isinstance(raw, dict):
        return result

    blocks = [
        "characteristics",
        "properties",
        "attributes",
        "params",
        "details",
        "specifications",
        "features",
        "options",
    ]

    for block_key in blocks:
        block = raw.get(block_key)

        if isinstance(block, list):
            for item in block:
                if not isinstance(item, dict):
                    continue

                item_name = clean_text(
                    item.get("name")
                    or item.get("title")
                    or item.get("key")
                    or item.get("label")
                    or item.get("property")
                ).lower()

                item_value = clean_text(
                    item.get("value")
                    or item.get("text")
                    or item.get("description")
                    or item.get("val")
                )

                if not item_value:
                    continue

                if not result["manufacturer"] and any(
                    x in item_name
                    for x in [
                        "производ",
                        "изготов",
                        "поставщик",
                        "manufacturer",
                        "supplier",
                        "producer",
                        "vendor",
                    ]
                ):
                    result["manufacturer"] = item_value

                if not result["brand"] and any(
                    x in item_name
                    for x in [
                        "бренд",
                        "марка",
                        "торговая",
                        "brand",
                        "trademark",
                    ]
                ):
                    result["brand"] = item_value

                if not result["category"] and any(
                    x in item_name
                    for x in [
                        "категор",
                        "раздел",
                        "category",
                        "section",
                    ]
                ):
                    result["category"] = item_value

        elif isinstance(block, dict):
            flat_text = json.dumps(block, ensure_ascii=False)

            if not result["manufacturer"]:
                match = re.search(
                    r"(?:производитель|изготовитель|manufacturer|producer|supplier|vendor)"
                    r"\"?\s*[:=]\s*\"?([^\",;}]+)",
                    flat_text,
                    flags=re.I,
                )

                if match:
                    result["manufacturer"] = clean_text(match.group(1))

            if not result["brand"]:
                match = re.search(
                    r"(?:бренд|brand|trademark|trade_mark)"
                    r"\"?\s*[:=]\s*\"?([^\",;}]+)",
                    flat_text,
                    flags=re.I,
                )

                if match:
                    result["brand"] = clean_text(match.group(1))

    return result


def normalize_url(possible_url, source_url):
    possible_url = clean_text(possible_url)

    if not possible_url:
        return source_url

    if possible_url.startswith("http://") or possible_url.startswith("https://"):
        return possible_url

    if possible_url.startswith("/"):
        parsed = urlparse(source_url)
        return f"{parsed.scheme}://{parsed.netloc}{possible_url}"

    return source_url


def normalize_price(value):
    """
    Приводит цену из разных форматов к строке.
    """
    if value is None:
        return ""

    if isinstance(value, dict):
        for key in ["value", "amount", "price", "current", "regular"]:
            if key in value:
                return normalize_price(value.get(key))

        return ""

    if isinstance(value, list):
        for item in value:
            price = normalize_price(item)

            if price:
                return price

        return ""

    text = clean_text(value)

    if not text:
        return ""

    if "₽" in text:
        return text

    return text


def normalize_product(raw, source_url):
    """
    Приводит товар из произвольного JSON сайта к единой структуре.
    """
    name = get_by_keys(raw, NAME_KEYS)
    brand = get_by_keys(raw, BRAND_KEYS)
    manufacturer = get_by_keys(raw, MANUFACTURER_KEYS)
    category = get_by_keys(raw, CATEGORY_KEYS)
    price = normalize_price(get_by_keys(raw, PRICE_KEYS))

    extra = extract_from_characteristics(raw)

    if not brand:
        brand = extra["brand"]

    if not manufacturer:
        manufacturer = extra["manufacturer"]

    if not category:
        category = extra["category"]

    product_url = get_by_keys(raw, ["url", "link", "href", "product_url", "productUrl"])
    product_url = normalize_url(product_url, source_url)

    return {
        "name": clean_text(name),
        "brand": clean_text(brand),
        "manufacturer": clean_text(manufacturer),
        "category": clean_text(category),
        "price": clean_text(price),
        "product_url": product_url or source_url,
    }


def guess_brand_from_name(name):
    """
    Примерно вытаскивает бренд из названия товара.

    Пример:
    'Печенье Lotte Choco Pie глазированное 336г' -> 'Lotte'
    'Эскимо Milka сливочное...' -> 'Milka'
    """
    name = clean_text(name)

    if not name:
        return ""

    known_prefixes = [
        "печенье",
        "шоколад",
        "конфеты",
        "конфитюр",
        "джем",
        "эскимо",
        "пломбир",
        "мороженое",
        "набор",
        "вафли",
        "торт",
        "пирожное",
        "мармелад",
        "зефир",
        "батончик",
        "карамель",
        "драже",
        "паста",
        "сироп",
        "мед",
        "мёд",
        "пряники",
        "кекс",
        "рулет",
        "сухари",
        "сушки",
        "халва",
    ]

    parts = name.split()

    if not parts:
        return ""

    first = parts[0].lower()

    if first in known_prefixes and len(parts) > 1:
        return parts[1]

    return parts[0]


def extract_price_from_text(text):
    """
    Ищет цену в строке.
    Поддерживает варианты:
    319 99 ₽
    319,99 ₽
    319.99 ₽
    319 ₽
    """
    text = clean_text(text)

    if not text:
        return ""

    patterns = [
        r"(\d{1,5})\s+(\d{2})\s*₽",
        r"(\d{1,5}[,.]\d{2})\s*₽",
        r"(\d{1,5})\s*₽",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if not match:
            continue

        if len(match.groups()) == 2:
            return f"{match.group(1)},{match.group(2)} ₽"

        return match.group(1).replace(".", ",") + " ₽"

    return ""


def cleanup_product_name_from_price_line(line):
    """
    Из строки с товаром и ценой пытается оставить только название.
    """
    name = clean_text(line)

    if not name:
        return ""

    # Удаляем скидку в начале.
    name = re.sub(r"^-\d+%\s*", "", name)

    # Удаляем мусор рейтинга/оценок в начале.
    name = re.sub(r"^(нет оценок|[\d.,]{1,4})\s+", "", name, flags=re.I)

    # Удаляем цену и всё после неё.
    name = re.sub(r"\s+\d{1,5}\s+\d{2}\s*₽.*$", "", name)
    name = re.sub(r"\s+\d{1,5}[,.]\d{2}\s*₽.*$", "", name)
    name = re.sub(r"\s+\d{1,5}\s*₽.*$", "", name)

    # Иногда цена приходит без символа рубля в конце строки: "319 99".
    name = re.sub(r"\s+\d{1,5}\s+\d{2}$", "", name)

    # Удаляем повторяющиеся технические хвосты.
    name = re.sub(r"\s*В корзину\s*$", "", name, flags=re.I)
    name = re.sub(r"\s*Подробнее\s*$", "", name, flags=re.I)

    return clean_text(name)


def is_bad_product_name(name):
    """
    Отсекает строки меню, фильтров и служебные фразы.
    """
    name = clean_text(name)

    if not name:
        return True

    low = name.lower()

    bad_exact = {
        "в корзину",
        "каталог",
        "фильтры",
        "фильтр",
        "цена",
        "бренд",
        "применить",
        "сбросить",
        "показать",
        "главная",
        "профиль",
        "корзина",
        "доставка",
        "самовывоз",
        "покупателям",
        "сотрудничество",
        "правовая информация",
        "loading",
        "loading...",
        "найти",
        "поиск",
        "акции",
        "магазины",
    }

    bad_contains = [
        "cookie",
        "cookies",
        "политика конфиденциальности",
        "пользовательское соглашение",
        "правовая информация",
        "адреса магазинов",
        "служба поддержки",
        "скачать приложение",
    ]

    if low in bad_exact:
        return True

    if any(x in low for x in bad_contains):
        return True

    if len(name) < 5:
        return True

    if not re.search(r"[А-Яа-яA-Za-z]", name):
        return True

    # Если строка состоит почти только из цены/цифр.
    if re.fullmatch(r"[\d\s.,₽$€%-]+", name):
        return True

    return False


def parse_products_from_html(html, page_url):
    """
    Фоллбэк для 5ka.ru и похожих страниц.
    Не зависит от css-классов, потому что у 5ka.ru они часто динамические.
    Ищет строки, где есть товар + цена в рублях.
    """
    soup = BeautifulSoup(html, "lxml")

    products = []
    seen = set()

    text = soup.get_text("\n")
    lines = [clean_text(line) for line in text.split("\n") if clean_text(line)]

    # Первый способ: ищем строки, где название и цена находятся в одной строке.
    for line in lines:
        if "₽" not in line:
            continue

        price = extract_price_from_text(line)
        name = cleanup_product_name_from_price_line(line)

        if is_bad_product_name(name):
            continue

        key = name.lower()

        if key in seen:
            continue

        seen.add(key)

        products.append(
            {
                "name": name,
                "brand": guess_brand_from_name(name),
                "manufacturer": "",
                "category": "Каталог сайта",
                "price": price,
                "product_url": page_url,
            }
        )

    # Второй способ: иногда название и цена находятся рядом, но в разных строках.
    # Тогда идём по строкам и ищем цену, а перед ней берём ближайшую похожую строку с названием.
    if not products:
        for index, line in enumerate(lines):
            if "₽" not in line:
                continue

            price = extract_price_from_text(line)

            candidates = []
            start = max(0, index - 6)

            for prev_line in lines[start:index]:
                prev_line = clean_text(prev_line)

                if is_bad_product_name(prev_line):
                    continue

                if "₽" in prev_line:
                    continue

                candidates.append(prev_line)

            if not candidates:
                continue

            name = candidates[-1]
            key = name.lower()

            if key in seen:
                continue

            seen.add(key)

            products.append(
                {
                    "name": name,
                    "brand": guess_brand_from_name(name),
                    "manufacturer": "",
                    "category": "Каталог сайта",
                    "price": price,
                    "product_url": page_url,
                }
            )

    # Третий способ: старый селекторный fallback.
    # Оставляем его на случай других сайтов/страниц.
    if not products:
        selectors = [
            "[data-testid*='product']",
            "[data-test*='product']",
            "[data-qa*='product']",
            "[class*='product']",
            "[class*='Product']",
            "[class*='card']",
            "[class*='Card']",
            "article",
        ]

        cards = soup.select(", ".join(selectors))

        for card in cards:
            parts = [
                clean_text(x)
                for x in card.get_text("\n").split("\n")
                if clean_text(x)
            ]

            if not parts:
                continue

            meaningful_parts = [
                p
                for p in parts
                if len(p) >= 3 and not re.fullmatch(r"[\d\s.,₽$€%-]+", p)
            ]

            if not meaningful_parts:
                continue

            name = meaningful_parts[0]

            if is_bad_product_name(name):
                continue

            price = ""

            for part in parts:
                if "₽" in part:
                    price = extract_price_from_text(part)
                    break

            link = ""
            a = card.select_one("a[href]")

            if a:
                link = a.get("href", "")

            link = normalize_url(link, page_url)

            key = name.lower()

            if key in seen:
                continue

            seen.add(key)

            products.append(
                {
                    "name": name,
                    "brand": guess_brand_from_name(name),
                    "manufacturer": "",
                    "category": "Каталог сайта",
                    "price": price,
                    "product_url": link or page_url,
                }
            )

    return products


def collect_products(start_url, scroll_steps=5, headless=True):
    """
    Открывает страницу, собирает JSON-ответы и HTML-карточки.
    Chromium устанавливается и запускается только здесь,
    то есть только после запроса на парсинг.
    """
    products = []
    seen_names = set()
    captured_json_objects = []

    ensure_playwright_chromium()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
            ],
        )

        try:
            context = browser.new_context(
                locale="ru-RU",
                viewport={"width": 1440, "height": 1000},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )

            page = context.new_page()
            page.set_default_timeout(30000)
            page.set_default_navigation_timeout(45000)

            def handle_response(response):
                url = response.url.lower()
                content_type = response.headers.get("content-type", "").lower()

                if "json" not in content_type and "/api/" not in url and "graphql" not in url:
                    return

                try:
                    data = response.json()
                    captured_json_objects.append((response.url, data))
                except Exception:
                    return

            page.on("response", handle_response)

            page.goto(start_url, wait_until="domcontentloaded", timeout=45000)

            # Даём JS сайта подгрузить первые товары.
            page.wait_for_timeout(7000)

            for _ in range(scroll_steps):
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(1200)

            html = page.content()
            current_url = page.url

            # Сначала пробуем вытащить товары из JSON/API.
            for source_url, data in captured_json_objects:
                raw_products = find_product_dicts(data)

                for raw in raw_products:
                    product = normalize_product(raw, source_url)
                    name = clean_text(product.get("name"))

                    if not name:
                        continue

                    key = name.lower()

                    if key in seen_names:
                        continue

                    seen_names.add(key)
                    products.append(product)

            # Если JSON не помог — парсим текст/HTML.
            if not products:
                html_products = parse_products_from_html(html, current_url)

                for product in html_products:
                    name = clean_text(product.get("name"))

                    if not name:
                        continue

                    key = name.lower()

                    if key in seen_names:
                        continue

                    seen_names.add(key)
                    products.append(product)

        finally:
            browser.close()

    return products


def group_products_for_excel(products, source_url):
    """
    Группирует товары по производителю/поставщику.
    Если производитель неизвестен — группирует по бренду.
    Если бренд тоже неизвестен — группирует в общий блок.
    """
    grouped = defaultdict(
        lambda: {
            "company": "",
            "brands": set(),
            "categories": set(),
            "products": set(),
            "sources": set(),
        }
    )

    for item in products:
        manufacturer = clean_text(item.get("manufacturer"))
        brand = clean_text(item.get("brand"))
        name = clean_text(item.get("name"))
        category = clean_text(item.get("category")) or "Каталог сайта"
        product_url = clean_text(item.get("product_url")) or source_url

        company = manufacturer or brand or "Не найдено на карточке товара"
        key = company.lower()

        grouped[key]["company"] = company

        if brand:
            grouped[key]["brands"].add(brand)

        if category:
            grouped[key]["categories"].add(category)

        if name:
            grouped[key]["products"].add(name)

        if product_url:
            grouped[key]["sources"].add(product_url)

    rows = []

    for group in grouped.values():
        company = group["company"]
        products_list = sorted(group["products"])
        brands_list = sorted(group["brands"])
        categories_list = sorted(group["categories"])
        sources_list = sorted(group["sources"])

        if company == "Не найдено на карточке товара":
            role = "Поставщик/производитель не указан в данных сайта"
            function = (
                "На сайте не найдено отдельное поле производителя/поставщика. "
                "Нужно уточнять по карточке товара, этикетке или данным поставки."
            )
        else:
            role = "Производитель / бренд / поставщик по данным сайта"
            function = "Связан с товарами: " + "; ".join(products_list[:8])

            if len(products_list) > 8:
                function += f" и ещё {len(products_list) - 8} поз."

        rows.append(
            {
                "Юрлицо / компания": company,
                "Роль": role,
                "Бренд(ы) в категории": "; ".join(brands_list) if brands_list else company,
                "Подкатегории / товары из категории": (
                    "; ".join(categories_list) + " / " + "; ".join(products_list[:15])
                ),
                "Что производит / функция": function,
                "УНП": "",
                "Источник": "; ".join(sources_list[:3]),
            }
        )

    rows.sort(key=lambda x: x["Юрлицо / компания"])

    return rows


def parse_any_url(url, scroll_steps=5):
    """
    Универсальная точка входа для Telegram-бота.
    Принимает любую публичную ссылку.
    """
    products = collect_products(
        start_url=url,
        scroll_steps=scroll_steps,
        headless=True,
    )

    rows = group_products_for_excel(products, url)

    return {
        "products_count": len(products),
        "rows_count": len(rows),
        "rows": rows,
    }


def parse_5ka_url(url, scroll_steps=5):
    """
    Оставлено для совместимости со старым кодом.
    Теперь делает то же самое, что parse_any_url.
    """
    return parse_any_url(url, scroll_steps)
