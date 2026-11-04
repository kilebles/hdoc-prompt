# fl-prompts

Промпты картинка + видео по сценарию: веб-модуль для сайта Motion Pr (`src/promptgen`, ниже) и Telegram-бот (`src/bot`, запасной вариант).

## Telegram-бот

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

---

# Модуль промптов для сайта (`src/promptgen`)

Вкладка «Промпты» сайта Motion Pr: по сценарию и шаблону ниши пишет промпты картинка + видео.
Работает через подписки — **agy** (Google Antigravity) по умолчанию, **Claude Code** подключается
в `config.local.json`. Свой API (`/api/prompts`, порт 8040) и отдельный **worker** с очередью.
Telegram-бот выше не меняется и продолжает работать как раньше.

## Как идёт работа

1. **Шаблон ниши.** У каждой ниши два места под мастер-промпт: «Обычный» и «Аватар» (Avatar → видео).
   Шаблон хранится целиком — это инструкция для модели. При загрузке он один раз разбирается: сколько
   сцен на абзац и какие, какие блоки дописываются к промпту дословно (стиль, VLOG PERSPECTIVE, негативы),
   лимиты длины, правила реплик. Каждый «дословный» блок сверяется с текстом шаблона; заглушки
   (`[insert here …]`) и правила для автора («handle death … through implication», названия сериалов)
   в промпты не попадают — это видно в предупреждениях шаблона.
2. **База брифа** — на выбор:
   - `template` — шаблон ниши (+ паспорт ниши из «Сценария» как фон);
   - `passport` — паспорт ниши + встроенный универсальный шаблон (`src/promptgen/defaults`).
3. **Сценарий** — готовый проект вкладки «Сценарий» (script.json) или файл .docx / .txt / .md.
   Плюс поле «Дополнительно» — важное, что модель должна учесть в первую очередь.
4. **Бриф — один вызов на весь сценарий:** эпоха и под-периоды, у аватара — одна линия одежды и голос
   ведущей, фиксированные описания персонажей и мест, особые ограничения по абзацам, а по каждому
   абзацу — под-период, место, что происходит (вместо посабзацной «сводки») и до 3 предметов для стока
   (вместо отдельного подбора объектов). Бриф можно проверить и поправить до генерации
   (`review_brief`).
5. **Промпты — пачками и параллельно.** Модель пишет только сцену; фиксированные блоки и негативы
   дописывает код. Каждый абзац проверяется кодом; абзацы с серьёзными замечаниями переписываются
   отдельно (до 2 раз), что не исправилось — помечается в листе «Check».

Проверки кода: число сцен, длина сцены против лимита шаблона, заглушки и служебные слова, годы, которых
нет ни в абзаце, ни в его эпохе, слова, на которых режет модерация Google (жёсткие — переписать, мягкие —
пометить), звук в промпте картинки; у аватара — линия одежды слово в слово, описание лица, длина реплик,
повтор реплики, реплика, переписанная из сценария дословно. Код сам убирает «..», «Avoid: avoid»,
тире и дефисы-пунктуацию (если шаблон этого требует).

## Формат результата

Одна строка = одна сцена. Номер `абзац.сцена` (`12.3`), абзацы сквозные по всему сценарию.

| Где | Что |
|---|---|
| `<название>.xlsx`, лист **Prompts** | `number`, `img_prompt`, `vid_prompt`, `stock_query_1..3` (только в первой строке абзаца, с эпохой абзаца), `paragraph` — те же колонки, что у бота |
| лист **Brief** | эпоха, под-периоды (материальная культура, палитра, свет, фактуры), ведущий, персонажи, места, ограничения, по абзацам: период · место · что происходит |
| лист **Check** | сцены с неисправленными замечаниями и пометками |
| `<название>.tsv` | колонки листа Prompts — вставить в Google Sheets одним копированием |
| `result.json` (API `/result`) | `rows`: `number`, `paragraph_n`, `scene_n`, `shot`, `img`, `vid`, `stock`, `paragraph`, `problems`, `flags`; `brief`; `summary` |

Аватар-файлы получают суффикс `_avatar`.

## Установка на Windows (130.17.13.222), под пользователем `footage`

agy должен работать с входом пользователя `footage`, поэтому и API, и worker запускаются под ним.

1. `git clone` / `git pull` в папку программы, затем двойной щелчок `setup_web.bat` (создаст `.venv`).
2. В `.env`: `API_TOKEN=<тот же, что у сайта>`, `PORT=8040`, `SCENARIO_API_URL=http://127.0.0.1:8020`.
3. Два окна: `start_worker.bat` и `start_api.bat`.
4. Проверка: `http://127.0.0.1:8040/health` → `"worker_online": true`.
5. Брандмауэр — порт только для Linux-сервера:
   `New-NetFirewallRule -DisplayName "fl-prompts API 8040" -Direction Inbound -Protocol TCP -LocalPort 8040 -RemoteAddress 89.127.192.164 -Action Allow`
6. Caddy на Linux (перед правилом для `/api/*`): `handle /api/prompts/* { reverse_proxy 130.17.13.222:8040 }`

Свои значения настроек — в `config.local.json` рядом с `config.json` (не в git): модели по этапам
(`backends.antigravity.stage_models`), число параллельных вызовов (`parallel`), размер пачки (`batch`),
допуски проверок (`checks`). Claude Code включается так: `"backends": {"claude": {"enabled": true,
"config_dir": "C:\\Users\\footage\\.claude-prompts"}}` и выбор модели Claude в панели запуска.

## API `/api/prompts` (токен `Authorization: Bearer …`)

| Метод | Путь | Что |
|---|---|---|
| GET | `/health` (без префикса и токена) | `worker_online` |
| GET | `/options` | исполнители и модели, режимы, базы, варианты числа пар |
| GET | `/niches`, `/niches/{slug}` | ниши «Сценария» + состояние шаблонов, паспорт |
| GET / PUT / DELETE | `/niches/{slug}/templates/{regular\|avatar}` | шаблон ниши (PUT `{text, name}`) |
| POST | `/niches/{slug}/templates/{kind}/file` | шаблон файлом .txt/.md/.docx |
| POST | `/niches/{slug}/templates/{kind}/analyze` | разобрать заново |
| GET | `/templates/builtin/{kind}` | встроенный шаблон |
| GET | `/scripts?niche=` | готовые сценарии «Сценария» |
| POST | `/files/script` | файл сценария → абзацы |
| GET / POST | `/projects` | список / создать (`niche`, `mode`, `base`, `pairs`, `notes`, `model`, `review_brief`, `script`) |
| GET / DELETE | `/projects/{id}` | проект с линией этапов бриф → промпты → проверка → готово |
| GET / PUT | `/projects/{id}/brief` | бриф / исправленный бриф |
| POST | `/projects/{id}/launch` · `stop` · `resume` · `rebrief` | запустить промпты · остановить · продолжить · бриф заново |
| GET | `/projects/{id}/result` · `log` · `files/{name}` | строки · ход работы · xlsx/tsv |
| GET | `/events` | SSE: `project.changed`, `template.changed`, `worker.changed` |

Тесты: `pip install -r requirements-web.txt pytest && python -m pytest` (тестовый исполнитель, без подписки).
