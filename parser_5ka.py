import os
import sys
import json
import re
import subprocess
from collections import defaultdict
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


PRODUCT_HINT_KEYS = {
    "name",
    "title",
    "product_name",
    "display_name",
    "productName",
    "displayName",
    "caption",
    "brand",
    "brand_name",
    "brandName",
    "manufacturer",
    "manufacturer_name",
    "manufacturerName",
    "vendor",
    "vendor_name",
    "vendorName",
    "supplier",
    "supplier_name",
    "supplierName",
    "producer",
    "producer_name",
    "producerName",
    "category",
    "category_name",
    "categoryName",
    "url",
    "slug",
    "link",
    "href",
    "price",
    "current_price",
    "currentPrice",
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


def ensure_playwright_browser():
    """
    Проверяет/устанавливает Chromium для Playwright.
    Нужно для хостингов, где pip install playwright не скачивает браузер автоматически.
    """
    marker_path = "/tmp/playwright_chromium_installed"

    if os.path.exists(marker_path):
        return

    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        with open(marker_path, "w", encoding="utf-8") as file:
            file.write("ok")

    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "Не удалось установить Chromium для Playwright.\n\n"
            "Попробуй добавить в Dockerfile или команду сборки:\n"
            "python -m playwright install --with-deps chromium\n\n"
            f"STDOUT:\n{e.stdout}\n\n"
            f"STDERR:\n{e.stderr}"
        )


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

        if value is None or value == "":
            continue

        if isinstance(value, dict):
            nested = get_by_keys(
                value,
                NAME_KEYS + BRAND_KEYS + MANUFACTURER_KEYS + CATEGORY_KEYS + PRICE_KEYS,
            )

            if nested:
                return nested

        elif isinstance(value, list):
            values = []

            for item in value:
                if isinstance(item, dict):
                    nested = get_by_keys(
                        item,
                        NAME_KEYS + BRAND_KEYS + MANUFACTURER_KEYS + CATEGORY_KEYS + PRICE_KEYS,
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


def normalize_product(raw, source_url):
    """
    Приводит товар из произвольного JSON сайта к единой структуре.
    """
    name = get_by_keys(raw, NAME_KEYS)
    brand = get_by_keys(raw, BRAND_KEYS)
    manufacturer = get_by_keys(raw, MANUFACTURER_KEYS)
    category = get_by_keys(raw, CATEGORY_KEYS)
    price = get_by_keys(raw, PRICE_KEYS)

    extra = extract_from_characteristics(raw)

    if not brand:
        brand = extra["brand"]

    if not manufacturer:
        manufacturer = extra["manufacturer"]

    if not category:
        category = extra["category"]

    product_url = get_by_keys(
        raw,
        [
            "url",
            "link",
            "href",
            "product_url",
            "productUrl",
            "canonical_url",
            "canonicalUrl",
        ],
    )

    product_url = normalize_url(product_url, source_url)

    return {
        "name": name,
        "brand": brand,
        "manufacturer": manufacturer,
        "category": category,
        "price": price,
        "product_url": product_url or source_url,
    }


def parse_products_from_html(html, page_url):
    """
    Фоллбэк, если JSON не удалось перехватить.
    Достаёт хотя бы названия товаров из HTML.
    """
    soup = BeautifulSoup(html, "lxml")
    products = []

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
            p for p in parts
            if len(p) >= 3
            and not re.fullmatch(r"[\d\s.,₽$€%-]+", p)
            and p.lower() not in ["купить", "в корзину", "добавить", "подробнее"]
        ]

        if not meaningful_parts:
            continue

        name = meaningful_parts[0]

        if not name or len(name) < 3:
            continue

        link = ""
        a = card.select_one("a[href]")

        if a:
            link = a.get("href", "")
            link = normalize_url(link, page_url)

        products.append(
            {
                "name": name,
                "brand": "",
                "manufacturer": "",
                "category": "",
                "price": "",
                "product_url": link or page_url,
            }
        )

    return products


def collect_products(start_url, scroll_steps=10, headless=True):
    """
    Открывает страницу, собирает JSON-ответы и HTML-карточки.
    """
    ensure_playwright_browser()

    products = []
    seen_names = set()
    captured_json_objects = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
            ],
        )

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

        def handle_response(response):
            url = response.url.lower()
            content_type = response.headers.get("content-type", "").lower()

            if (
                "json" not in content_type
                and "/api/" not in url
                and "graphql" not in url
                and "catalog" not in url
                and "product" not in url
            ):
                return

            try:
                data = response.json()
                captured_json_objects.append((response.url, data))
            except Exception:
                return

        page.on("response", handle_response)

        page.goto(start_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(4000)

        for _ in range(scroll_steps):
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(1200)

        html = page.content()
        current_url = page.url

        for source_url, data in captured_json_objects:
            raw_products = find_product_dicts(data)

            for raw in raw_products:
                product = normalize_product(raw, source_url)
                key = product["name"].lower()

                if not product["name"] or key in seen_names:
                    continue

                seen_names.add(key)
                products.append(product)

        if not products:
            html_products = parse_products_from_html(html, current_url)

            for product in html_products:
                key = product["name"].lower()

                if not product["name"] or key in seen_names:
                    continue

                seen_names.add(key)
                products.append(product)

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
                    "; ".join(categories_list)
                    + " / "
                    + "; ".join(products_list[:15])
                ),
                "Что производит / функция": function,
                "УНП": "",
                "Источник": "; ".join(sources_list[:3]),
            }
        )

    rows.sort(key=lambda x: x["Юрлицо / компания"])

    return rows


def parse_any_url(url, scroll_steps=10):
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


def parse_5ka_url(url, scroll_steps=10):
    """
    Оставлено для совместимости со старым кодом.
    Теперь делает то же самое, что parse_any_url.
    """
    return parse_any_url(url, scroll_steps)
