# Правовой Поток — серверная часть

Реализация серверной части сервиса «Правовой Поток» по ТЗ
(`pravovoy-potok-tz-claude-code-one-model.md`). Статус реализации по этапам
ТЗ §15, что работает / что заглушка / что требует решения заказчика — см.
[`docs/ROADMAP.md`](docs/ROADMAP.md). Соответствие «экран прототипа → API» —
[`docs/AUDIT.md`](docs/AUDIT.md).

## Стек

Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2 (async, `asyncpg`), Alembic,
PostgreSQL, Redis, pytest. Единственная модель ИИ — **YandexGPT Pro 5.1**
(`app/providers/yandex_gpt.py`), без тихой замены на другую модель.

## Запуск через Docker Compose

```bash
cp .env.example .env
# отредактируйте .env — при желании подключить реальный YandexGPT Pro 5.1

docker compose up --build
# API:      http://localhost:8000  (OpenAPI: /docs)
# worker:   отдельный контейнер, обрабатывает scan_jobs из Redis
```

Миграции применяются вручную (или добавьте `command` в docker-compose при
желании автоприменения):

```bash
docker compose exec api alembic upgrade head
```

## Локальный запуск без Docker

Требуются локально запущенные PostgreSQL 16 и Redis.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # поправьте DATABASE_URL/REDIS_URL под свою среду

alembic upgrade head          # схема + сиды тарифов и source_policies
uvicorn app.main:create_app --factory --reload
python -m app.worker.runner   # в отдельном терминале — обработчик scan_jobs
```

## Тесты

Тесты используют **настоящие** PostgreSQL и Redis (не SQLite/фейки) — тестовая
база создаётся автоматически при первом запуске сессии тестов (пересоздаёт
схему через `Base.metadata`, не через Alembic, для скорости).

```bash
createdb -U postgres pravovoy_potok_test   # один раз, если ещё не создана
pytest -v
```

По состоянию на последний прогон в этой сессии: **69 пройдено, 0 упало, 0
пропущено** (команда: `python -m pytest tests/ -q`, локально
PostgreSQL 16 + Redis 7, без Docker — см. `docs/ROADMAP.md` про окружение
разработки).

SSRF-тесты (`tests/test_ssrf_protection.py`) не делают реальных сетевых
запросов — резолвер и сокет подменяются, чтобы детерминированно проверить
блокировку loopback/private/link-local/metadata-адресов, поведение при
DNS-неоднозначности и на редиректах.

## Frontend (Этап F)

`frontend/index.html` + `frontend/app.js` — тот же интерфейс из исходного
прототипа (визуально не изменён), но подключённый к реальному API через
`fetch(..., {credentials:'include'})` вместо localStorage. Session-cookie
(HttpOnly) + CSRF-cookie выставляет backend; JS читает `pp_csrf` из
`document.cookie` и шлёт его в заголовке `X-CSRF-Token` на любой
не-GET-запрос.

Локальный запуск рядом с backend:

```bash
cd frontend && python3 -m http.server 5500
# откройте http://127.0.0.1:5500/index.html
```

В `.env` backend'а укажите `FRONTEND_ORIGIN=http://127.0.0.1:5500` (или
адрес вашего статического сервера) — CORS настроен на явный allow-list, а не
на `*`, потому что используются credentialed-запросы. Адрес самого API
задаётся в `frontend/index.html` через `<meta name="pp-api-base" content="...">`
(по умолчанию `http://127.0.0.1:8000`).

Для продакшена на разных доменах (Netlify + отдельный API-домен) нужно либо
поставить обратный прокси на `/api/*` (тогда куки — same-origin, CORS не
нужен), либо задать `COOKIE_DOMAIN` на общий родительский домен. Подробности
и границы (что подключено, что оставлено как заглушка) — `docs/AUDIT.md`,
раздел про фронтенд, и `docs/ROADMAP.md`, этап F.

Проверено вручную через Playwright (реальный Chromium, не заглушка):
регистрация → создание потока → сбор (реальный worker) → редактирование
черновика → официальный документ → паспорт фактов → проверка редактором →
утверждение → публикация → публичный предпросмотр (без сайта обнаружения) →
экспорт XML (без сайта обнаружения) → тарифы (реальные цены с сервера) →
раздел администрирования (для обычного пользователя — «доступ только
platform_admin», для реального platform_admin — настоящие данные) → выход/
повторный вход → гостевой просмотр примера без регистрации. Найденные и
исправленные при этом тестировании реальные ошибки фронтенда: тариф «Старт»
не пропускал периодичность «По рабочим дням» без явной подсказки в мастере
настройки; счётчик опубликованных материалов на странице экспорта не
обновлялся после публикации; гостевой просмотр редиректил на `/login` вместо
показа примера; администрирование могло на долю секунды показать неверный
экран до ответа сервера.

## Структура

```
frontend/
  index.html    Разметка/CSS (без изменений от исходного прототипа)
  app.js        Вся логика: fetch-клиент, состояние, обработчики — подключено к API
app/
  models/       SQLAlchemy-модели (все таблицы из ТЗ §12)
  schemas/      Pydantic-схемы запросов/ответов
  routers/      FastAPI-роутеры (auth, access, billing, flows, news, ai, exports, admin, activity)
  services/     Доменная логика (auth, trial, billing, flow, scan, news, export, ai, activity, rate_limit)
  providers/    SourceProvider (fixture/HTTP+SSRF), YandexGPTPro51Provider
  worker/       Отдельный процесс фоновых задач + Redis-очередь
migrations/     Alembic (схема + сиды тарифов/source_policies)
tests/          pytest, покрывает регистрацию/trial/изоляцию/антидубли/SSRF/
                редакционный workflow/LLM-гейтинг/XML/платежи-заглушки/rate limit/admin
docs/
  AUDIT.md      экран прототипа → API → состояние
  ROADMAP.md    работает / mock / нужно решение заказчика
```

## Безопасность и честность реализации

- Пароли — только Argon2id-хеш; сессии — opaque-токен в HttpOnly+Secure
  cookie, сервер хранит лишь его SHA-256 хеш.
- CSRF — double-submit с HMAC от серверного секрета.
- Ничего не подписывается словами «собрано»/«оплачено»/«отправлено»/«проверено»,
  если это не так: смотрите буквально коды `DEMO_FIXTURE`, `NOT_CONFIGURED`,
  `NOT_SENT`, `PAYMENT_NOT_CONFIGURED`, `LLM_NOT_CONFIGURED` в коде и ответах API.
