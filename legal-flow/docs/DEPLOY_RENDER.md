# Деплой на Render.com — пошагово

Всё делается кликами в браузере, консоль не нужна. Render читает файл
`legal-flow/render.yaml` и создаёт два компонента: backend API (он же
отдаёт frontend — один домен, без CORS/куки-сложностей) и PostgreSQL.
Redis нужно завести отдельно (см. шаг 2).

**Фоновый worker (`app/worker/runner.py`) на Render не разворачивается** —
у Render нет бесплатного тарифа для Background Worker (только платный,
от ~$7/мес.). По решению владельца проекта worker продолжает работать
**на локальном компьютере**, подключаясь к той же базе данных и тому же
Redis, что и API на Render — по «внешним» (External) адресам вместо
локальных `localhost`. Значит: чтобы сбор материалов реально работал,
компьютер с запущенным worker'ом должен быть включён и подключён к
интернету.

## 1. Регистрация, репозиторий, деплой

1. Зайдите на **render.com**, зарегистрируйтесь.
2. **New** → **Blueprint**.
3. Репозиторий: `https://github.com/m09037143-ux/-` (поле «Public Git
   Repository» → Continue).
4. Ветка — **`claude/ecstatic-turing-lhf6m3`**.
5. Blueprint Path — **`legal-flow/render.yaml`**.
6. Render покажет `pravovoy-potok-db` и `pravovoy-potok-api`. Поля с
   переменными окружения можно оставить пустыми — заполним после
   создания. Нажмите **Apply**.

## 2. Redis — создать отдельно

1. **New** → **Key Value**, тариф — бесплатный.
2. После создания скопируйте **Internal Redis URL** → вставьте в
   **pravovoy-potok-api** → Environment → `REDIS_URL`.
3. Отдельно скопируйте **External Redis URL** — эту версию впишите в
   **свой локальный** `legal-flow\.env`, переменная `REDIS_URL` (замените
   то, что было для локального Redis). Если у бесплатного тарифа Render
   Key Value нет внешнего адреса — Render явно напишет об этом на
   странице сервиса; тогда пришлите мне скриншот, разберёмся.

## 3. База данных — internal для Render, external — для своего компьютера

1. Откройте `pravovoy-potok-db`, скопируйте **Internal Database URL**
   (вида `postgresql://...@...internal:5432/pravovoy_potok`). Замените
   `postgresql://` на `postgresql+asyncpg://` и вставьте как
   `DATABASE_URL` в **pravovoy-potok-api** → Environment.
2. Скопируйте **External Database URL** (тоже с заменой на
   `postgresql+asyncpg://`) → вставьте в локальный `.env`, переменная
   `DATABASE_URL`. Worker у вас на компьютере будет писать материалы
   прямо в эту же, общую с Render, базу.

## 4. YandexGPT Pro 5.1 — реальный ключ

Заполните одинаковые значения в Environment у **pravovoy-potok-api** на
Render (вручную, ключ никогда не должен попасть в git) и в локальном
`.env` у worker'а:
- `YANDEX_FOLDER_ID`
- `YANDEX_MODEL_URI` — ровно `gpt://<ваш YANDEX_FOLDER_ID>/yandexgpt-5.1`
- `YANDEX_API_KEY`

## 5. Локальный worker — переключить на Render

На компьютере, в окне с worker'ом:
1. `Ctrl+C`, чтобы остановить.
2. Открыть `legal-flow\.env`, заменить `DATABASE_URL` и `REDIS_URL` на
   external-адреса из шагов 2–3, сохранить.
3. Запустить заново: `python -m app.worker.runner`.

Backend (uvicorn) и статику фронтенда на локальном компьютере после этого
запускать больше не нужно — их роль теперь играет Render. Локально нужен
только worker (плюс venv с зависимостями проекта).

## 6. Проверка

1. Дождитесь **Live** у `pravovoy-potok-db` и `pravovoy-potok-api`.
2. Откройте адрес **pravovoy-potok-api** в браузере — откроется главная
   страница «Правовой Поток» (frontend отдаётся тем же сервисом).
3. Пройдите обычный путь: регистрация → поток → сбор → редактирование →
   публикация. «Приступить к сбору» создаст задание в Render Redis —
   заберёт и обработает его ваш локальный worker.

## Что нельзя перенести автоматически

- **Почта** (восстановление пароля) и **платёжный провайдер** — по-прежнему
  не подключены (см. `docs/ROADMAP.md`).
- **Яндекс.Диск** для экспорта — не подключён.
- Домен по умолчанию — `*.onrender.com`. Свой домен подключается в
  **Settings → Custom Domain** сервиса `pravovoy-potok-api`.
