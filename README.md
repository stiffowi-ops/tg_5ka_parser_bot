# Telegram-бот парсер 5ka в Excel

Бот принимает ссылку на страницу 5ka.ru, собирает товары, бренды и производителей/поставщиков, формирует Excel и отправляет файл пользователю.

## Структура проекта

```text
tg_5ka_parser_bot/
├── bot.py
├── parser_5ka.py
├── excel_writer.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Установка локально

```bash
python -m venv venv
```

Windows CMD:

```bash
venv\Scripts\activate
```

Linux / macOS:

```bash
source venv/bin/activate
```

Установить зависимости:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Настройка

Создай файл `.env` рядом с `bot.py`:

```env
BOT_TOKEN=ВАШ_ТОКЕН_БОТА
```

Токен брать у BotFather в Telegram.

Важно: файл `.env` нельзя загружать на GitHub.

## Запуск

```bash
python bot.py
```

## Как пользоваться

1. Отправь боту `/start`.
2. Отправь ссылку на страницу 5ka.ru.
3. Бот соберёт данные и отправит Excel-файл.

Лучше отправлять ссылку на конкретную категорию или поисковую страницу, а не главную страницу.

## Установка на сервер Ubuntu

```bash
git clone https://github.com/USERNAME/tg_5ka_parser_bot.git
cd tg_5ka_parser_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Создать `.env`:

```bash
nano .env
```

Вставить:

```env
BOT_TOKEN=твой_токен_от_BotFather
```

Запуск:

```bash
python bot.py
```

## Запуск через systemd

Создать сервис:

```bash
sudo nano /etc/systemd/system/tg-5ka-parser.service
```

Пример, замени пользователя и путь на свои:

```ini
[Unit]
Description=Telegram 5ka Parser Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/tg_5ka_parser_bot
Environment="PATH=/home/ubuntu/tg_5ka_parser_bot/venv/bin"
ExecStart=/home/ubuntu/tg_5ka_parser_bot/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Запустить сервис:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tg-5ka-parser
sudo systemctl start tg-5ka-parser
```

Проверить логи:

```bash
sudo journalctl -u tg-5ka-parser -f
```

## Важно

Если в Excel указано `Не найдено на карточке товара`, значит сайт не отдал производителя/поставщика в доступных данных.
Для юридически точного поставщика нужны карточка товара, этикетка, договорные данные сети или данные маркировки.
