import json
import re
from collections import defaultdict
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


PRODUCT_HINT_KEYS = {
    "name", "title", "product_name", "display_name",
    "brand", "brand_name",
    "manufacturer", "manufacturer_name",
    "vendor", "supplier", "producer",
    "category", "category_name",
    "url", "slug",
}

NAME_KEYS = [
    "name",
    "title",
    "product_name",
    "display_name",
]

BRAND_KEYS = [
    "brand",
    "brand_name",
    "trade_mark",
    "trademark",
]

MANUFACTURER_KEYS = [
    "manufacturer",
    "manufacturer_name",
    "producer",
    "producer_name",
    "vendor",
    "vendor_name",
    "supplier",
    "supplier_name",
    "company",
    "legal_entity",
    "organization",
]

CATEGORY_KEYS = [
    "category",
    "category_name",
    "section",
    "parent_category",
]


def clean_text(value):
    if value is None:
        return ""

    if isinstance(value, (int, float)):
        return str(value)

    value = str(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def get_by_keys(obj, keys):
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
                NAME_KEYS + BRAND_KEYS + MANUFACTURER_KEYS + CATEGORY_KEYS
            )
            if nested:
                return nested

        elif isinstance(value, list):
            result = "; ".join(clean_text(x) for x in value if clean_text(x))
            if result:
                return result

        else:
            text = clean_text(value)
            if text:
                return text

    return ""


def looks_like_product_dict(obj):
    if not isinstance(obj, dict):
        return False

    keys = {str(k).lower() for k in obj.keys()}

    has_product_key = bool(keys & PRODUCT_HINT_KEYS)
    has_name = any(k in obj and clean_text(obj.get(k)) for k in NAME_KEYS)

    return has_product_key and has_name


def find_product_dicts(data):
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


def normalize_product(raw, source_url):
    name = get_by_keys(raw, NAME_KEYS)
    brand = get_by_keys(raw, BRAND_KEYS)
    manufacturer = get_by_keys(raw, MANUFACTURER_KEYS)
    category = get_by_keys(raw, CATEGORY_KEYS)

    for block_key in [
        "characteristics",
        "properties",
        "attributes",
        "params",
        "details",
    ]:
        block = raw.get(block_key) if isinstance(raw, dict) else None

        if isinstance(block, list):
            for item in block:
                if not isinstance(item, dict):
                    continue

                item_name = clean_text(
                    item.get("name")
                    or item.get("title")
                    or item.get("key")
                    or item.get("label")
                ).lower()

                item_value = clean_text(
                    item.get("value")
                    or item.get("text")
                    or item.get("description")
                )

                if not item_value:
                    continue

                if not manufacturer and any(
                    x in item_name
                    for x in ["производ", "изготов", "поставщик", "manufacturer", "supplier"]
                ):
                    manufacturer = item_value

                if not brand and any(
                    x in item_name
                    for x in ["бренд", "марка", "торговая", "brand"]
                ):
                    brand = item_value

                if not category and any(
                    x in item_name
                    for x in ["категор", "раздел", "category"]
                ):
                    category = item_value

        elif isinstance(block, dict):
            flat_text = json.dumps(block, ensure_ascii=False)

            if not manufacturer:
                match = re.search(
                    r"(?:производитель|изготовитель|manufacturer|producer|supplier)"
                    r"\"?\s*[:=]\s*\"?([^\",;}]+)",
                    flat_text,
                    flags=re.I,
                )
                if match:
                    manufacturer = clean_text(match.group(1))

    product_url = get_by_keys(raw, ["url", "link", "href", "product_url"])

    if product_url and product_url.startswith("/"):
        parsed = urlparse(source_url)
        product_url = f"{parsed.scheme}://{parsed.netloc}{product_url}"

    return {
        "name": name,
        "brand": brand,
        "manufacturer": manufacturer,
        "category": category,
        "product_url": product_url or source_url,
    }


def parse_products_from_html(html, page_url):
    soup = BeautifulSoup(html, "lxml")
    products = []

    cards = soup.select(
        "[data-testid*='product'], "
        "[class*='product'], "
        "[class*='Product'], "
        "article"
    )

    for card in cards:
        parts = [
            clean_text(x)
            for x in card.get_text("\n").split("\n")
            if clean_text(x)
        ]

        if not parts:
            continue

        name = parts[0]

        if not name or len(name) < 3:
            continue

        link = ""
        a = card.select_one("a[href]")

        if a:
            link = a.get("href", "")

            if link.startswith("/"):
                parsed = urlparse(page_url)
                link = f"{parsed.scheme}://{parsed.netloc}{link}"

        products.append({
            "name": name,
            "brand": "",
            "manufacturer": "",
            "category": "",
            "product_url": link or page_url,
        })

    return products


def collect_products(start_url, scroll_steps=10, headless=True):
    products = []
    seen_names = set()
    captured_json_objects = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
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

            if "json" not in content_type and "/api/" not in url:
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

                if key not in seen_names:
                    seen_names.add(key)
                    products.append(product)

        browser.close()

    return products


def group_products_for_excel(products, source_url):
    grouped = defaultdict(lambda: {
        "company": "",
        "brands": set(),
        "categories": set(),
        "products": set(),
        "sources": set(),
    })

    for item in products:
        manufacturer = clean_text(item.get("manufacturer"))
        brand = clean_text(item.get("brand"))
        name = clean_text(item.get("name"))
        category = clean_text(item.get("category")) or "Каталог 5ka"
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

        rows.append({
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
        })

    rows.sort(key=lambda x: x["Юрлицо / компания"])

    return rows


def parse_5ka_url(url, scroll_steps=10):
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
