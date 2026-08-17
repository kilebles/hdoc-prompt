# hdoc-prompts bot

Telegram-бот на aiogram 3 (polling), многослойная архитектура.

## Структура

```
main.py                  # точка входа (запуск polling)
data/prompts/             # сохранённые промпты (JSON-файлы), без БД
data/logs/                # логи бота (bot.log, ротация 10MB × 5 файлов)
src/bot/
├── config.py            # pydantic-settings (BOT_*, LOG_*, GOOGLE_*)
├── core/
│   ├── loader.py         # сборка Bot/Dispatcher, DI, middlewares
│   ├── commands.py        # BotCommand — единый источник команд
│   └── logging.py
├── handlers/              # роутеры по доменам (commands/start, add_prompt, prompts, errors)
├── keyboards/              # inline-клавиатуры + CallbackData factories
├── middlewares/             # логирование, троттлинг
├── models/                   # доменные Pydantic-модели промпта (i2v-шаблон)
├── states/                    # FSM StatesGroup
├── services/                   # бизнес-логика за Protocol-интерфейсами
│   ├── prompt_parser_service.py  # текст -> структурированный промпт (Gemini)
│   └── prompt_storage_service.py # файловое хранилище промптов
├── texts/                        # тексты сообщений отдельно от логики
└── utils/html.py                  # безопасные HTML-хелперы (parse_mode=HTML)
```

Слои: `config → core (bootstrap) → middlewares → handlers → services → keyboards/texts/utils`.
Хендлеры не содержат бизнес-логики — только вызывают `services` и рендерят ответ.
Новый домен = новый router в `handlers/` + одна строка в `handlers/__init__.py`.

### Промпты

`/add_prompt` → выбор модели (`i2v`/`f2v`/`t2v`, пока реализован только `i2v`) → бот просит
прислать бриф файлом `.txt` → текст парсится через Gemini (`gemini-2.5-flash`, structured
output) в JSON по схеме `bot.models.prompt.I2VPrompt` → сохраняется в `data/prompts/<id>.json`.

`/prompts` показывает сохранённые промпты инлайн-клавиатурой, где текст кнопки — `title`
промпта; нажатие открывает детали.

## Запуск локально

```bash
cp .env.example .env   # заполнить BOT_TOKEN и GOOGLE_API_KEY
uv sync
uv run python main.py
```

## Запуск в Docker

```bash
cp .env.example .env
docker compose up --build
```

## Линтеры

```bash
uv run ruff check src
uv run mypy src/bot
```
