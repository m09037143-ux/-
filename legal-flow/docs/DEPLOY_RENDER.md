# Деплой на Render.com — пошагово

Всё делается кликами в браузере, консоль не нужна. Render читает файл
`legal-flow/render.yaml` и сам создаёт три компонента: backend API, базу
PostgreSQL и статику фронтенда. Redis нужно завести отдельно (см. шаг 3).

**Фоновый worker (`app/worker/runner.py`) на Render не разворачивается** —
у Render нет бесплатного тарифа для Background Worker (только платный,
от ~$7/мес.). По решению владельца проекта worker продолжает работать
**на локальном компьютере**, как и раньше, но подключается к той же базе
данных и тому же Redis, что и API на Render — просто по «внешним»
(External) адресам вместо локальных `localhost`. Это значит: чтобы сбор
материалов реально работал, компьютер с запущенным worker'ом должен быть
включён и подключён к интернету. Если это неудобно — можно позже
перевести worker на платный тариф Render (см. `render.yaml`, там
достаточно вернуть блок `type: worker`) или на другой хостинг.

## 1. Регистрация и подключение репозитория

1. Зайдите на **render.com**, зарегистрируйтесь (можно через GitHub-аккаунт —
   это упростит следующий шаг).
2. В панели Render нажмите **New** → **Blueprint**.
3. Укажите репозиторий `https://github.com/m09037143-ux/-` (или подключите
   GitHub-аккаунт и выберите его из списка).
4. Ветка — **`claude/ecstatic-turing-lhf6m3`**.
5. Blueprint Path — **`legal-flow/render.yaml`**.
6. Render покажет список ресурсов: `pravovoy-potok-db`,
   `pravovoy-potok-api`, `pravovoy-potok-frontend`. Поля с переменными
   окружения (DATABASE_URL, REDIS_URL, YANDEX_*...) можно оставить
   пустыми — заполним их после создания. Нажмите **Apply**.

## 2. База данных — internal URL для Render, external — для своего компьютера

1. Когда `pravovoy-potok-db` создастся, откройте её страницу.
2. Скопируйте **Internal Database URL** (вида
   `postgresql://pravovoy_potok:...@...internal:5432/pravovoy_potok`).
   Замените в начале `postgresql://` на `postgresql+asyncpg://` (backend
   на Render требует асинхронный драйвер) и вставьте как `DATABASE_URL`
   в **pravovoy-potok-api** → Environment.
3. Отдельно скопируйте **External Database URL** (тоже с заменой
   `postgresql://` на `postgresql+asyncpg://`) — эту версию впишите в
   **свой локальный** `legal-flow\.env` в переменную `DATABASE_URL`
   (замените то, что было раньше для локального Postgres). Worker у вас
   на компьютере будет писать материалы прямо в эту же, общую с Render,
   базу.

## 3. Redis — создать отдельно, тоже нужен и internal, и external адрес

1. **New** → **Key Value** (это и есть Redis у Render), тариф — бесплатный.
2. Скопируйте **Internal Redis URL** → вставьте как `REDIS_URL` в
   **pravovoy-potok-api** → Environment.
3. Скопируйте **External Redis URL** → вставьте как `REDIS_URL` в свой
   локальный `.env` (замените локальный Redis).
   Если у бесплатного тарифа Render Key Value нет внешнего адреса —
   Render явно об этом напишет на странице сервиса; тогда пришлите мне
   скриншот, разберёмся (возможно, придётся поднять свой Redis локально
   и синхронизировать очередь иначе, либо всё же перевести worker на
   Render на платный тариф).

## 4. Адреса сервисов — связать frontend и API между собой

1. Откройте **pravovoy-potok-api** — сверху её адрес вида
   `https://pravovoy-potok-api-XXXX.onrender.com`. Скопируйте.
2. Откройте **pravovoy-potok-frontend** — скопируйте её адрес.
3. У **pravovoy-potok-api** → Environment → `FRONTEND_ORIGIN` — вставьте
   адрес фронтенда (без слэша на конце).
4. Пришлите мне оба адреса в чат — я поправлю `frontend/index.html`
   (мета-тег `pp-api-base`) на адрес API и запушу; Render сам
   передеплоит статику.

## 5. YandexGPT Pro 5.1 — реальный ключ

Заполните в Environment у **pravovoy-potok-api** (вручную, ключ никогда
не должен попасть в git) и в локальном `.env` у worker'а — одинаковые
значения в обоих местах:
- `YANDEX_FOLDER_ID`
- `YANDEX_MODEL_URI` — ровно `gpt://<ваш YANDEX_FOLDER_ID>/yandexgpt-5.1`
- `YANDEX_API_KEY`

## 6. Локальный worker — переключить на Render

На компьютере, в окне с worker'ом:
1. `Ctrl+C`, чтобы остановить.
2. Открыть `legal-flow\.env`, заменить `DATABASE_URL` и `REDIS_URL` на
   external-адреса из шагов 2–3, сохранить.
3. Запустить заново: `python -m app.worker.runner`.

Backend (uvicorn) и статику фронтенда на локальном компьютере после этого
можно больше не запускать — их роль теперь играет Render. Работает
только worker плюс venv с зависимостями проекта.

## 7. Проверка

1. Дождитесь **Live** у всех сервисов на Render.
2. Откройте адрес **pravovoy-potok-frontend** в браузере.
3. Пройдите обычный путь: регистрация → поток → сбор → редактирование →
   публикация. «Приступить к сбору» на сайте создаст задание в Render
   Redis — заберёт и обработает его именно ваш локальный worker.
4. Если при входе куки не сохраняются — проверьте `COOKIE_SAMESITE=none`
   у API (уже задано в `render.yaml` по умолчанию).

## Что нельзя перенести автоматически

- **Почта** (восстановление пароля) и **платёжный провайдер** — по-прежнему
  не подключены (см. `docs/ROADMAP.md`).
- **Яндекс.Диск** для экспорта — не подключён.
- Домен по умолчанию — `*.onrender.com`. При покупке своего домена можно
  вернуть `COOKIE_SAMESITE=lax` и общий `COOKIE_DOMAIN`, если завести API
  и frontend на поддоменах одного домена — это безопаснее, чем `none`.
