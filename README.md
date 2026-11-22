# Арюна — бот предзаписи + Taplink

Telegram-бот предзаписи на нутрициологический разбор и простой лендинг (Taplink).

## Онлайн (GitHub Pages)

Лендинг публикуется из папки `taplink_html/` через GitHub Actions → GitHub Pages.

После первого успешного workflow URL будет вида:

`https://kitelovkin.github.io/aryuna-bot/`

## Бот (не на GitHub)

GitHub Pages — только статика. Бот (`bot.py`, long polling) нужен отдельный хост (Render / Railway / Fly / VPS).

Локально:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # заполните BOT_TOKEN и GOOGLE_WEBAPP_URL
.venv/bin/python bot.py
```

Переменные окружения: `BOT_TOKEN`, `GOOGLE_WEBAPP_URL`, опционально `ADMIN_ID`.

## Структура

- `bot.py` — Telegram-бот (aiogram)
- `apps_script.gs` — Google Apps Script Web App
- `taplink_html/` — лендинг для Pages
- `Dockerfile` — контейнер для бота
