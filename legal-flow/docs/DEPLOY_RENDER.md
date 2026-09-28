# Деплой на Render.com — пошагово

Всё делается кликами в браузере, консоль не нужна. Render читает файл
`render.yaml` в корне репозитория и сам создаёт четыре компонента:
backend API, фоновый worker, базу PostgreSQL и статику фронтенда.
Redis нужно завести отдельно (см. шаг 3) — Render не создаёт его
автоматически из `render.yaml` в этой версии Blueprint.

## 1. Регистрация и подключение репозитория

1. Зайдите на **render.com**, зарегистрируйтесь (можно через GitHub-аккаунт —
   это упростит следующий шаг).
2. В панели Render нажмите **New** → **Blueprint**.
3. Подключите GitHub, если не подключили при регистрации, и выберите
   репозиторий `m09037143-ux/-`.
4. Укажите ветку **`claude/ecstatic-turing-lhf6m3`**.
5. Render найдёт `render.yaml` и покажет список ресурсов на создание:
   `pravovoy-potok-db`, `pravovoy-potok-api`, `pravovoy-potok-worker`,
   `pravovoy-potok-frontend`. Нажмите **Apply** — начнётся создание
   (несколько минут).

## 2. База данных — подключить к API и worker

1. Когда `pravovoy-potok-db` создастся, откройте её страницу в Render.
2. Найдите **Internal Database URL** — скопируйте её. Она выглядит так:
   `postgresql://pravovoy_potok:...@...:5432/pravovoy_potok`.
3. **Важно:** в начале скопированной строки замените `postgresql://` на
   `postgresql+asyncpg://` (без этого backend не подключится — драйвер
   должен быть асинхронным).
4. Откройте сервис **pravovoy-potok-api** → вкладка **Environment** →
   найдите переменную `DATABASE_URL` → вставьте туда исправленную строку.
5. Повторите то же самое для **pravovoy-potok-worker**.

## 3. Redis — создать отдельно

1. **New** → **Key Value** (это и есть Redis у Render).
2. Тариф — бесплатный, имя — например `pravovoy-potok-redis`.
3. После создания скопируйте **Internal Redis URL** (выглядит как
   `redis://...` или `rediss://...` — менять ничего не нужно, вставляется
   как есть).
4. Вставьте в переменную `REDIS_URL` у **pravovoy-potok-api** и у
   **pravovoy-potok-worker**.

## 4. Адреса сервисов — связать frontend и API между собой

1. Откройте **pravovoy-potok-api** — сверху будет её адрес вида
   `https://pravovoy-potok-api-XXXX.onrender.com`. Скопируйте.
2. Откройте **pravovoy-potok-frontend** — тоже скопируйте её адрес
   (`https://pravovoy-potok-frontend-XXXX.onrender.com`).
3. У **pravovoy-potok-api** → Environment → `FRONTEND_ORIGIN` — вставьте
   адрес фронтенда (без слэша на конце).
4. Пришлите мне оба адреса (API и frontend) в чат — я поправлю
   `frontend/index.html` (строку `<meta name="pp-api-base" ...>`) на
   правильный адрес API и запушу изменение; Render сам передеплоит
   статику после пуша.

## 5. YandexGPT Pro 5.1 — реальный ключ

У **pravovoy-potok-api** и у **pravovoy-potok-worker** заполните в
Environment (вручную, ключ никогда не должен попасть в git):
- `YANDEX_FOLDER_ID`
- `YANDEX_MODEL_URI` — ровно `gpt://<ваш YANDEX_FOLDER_ID>/yandexgpt-5.1`
- `YANDEX_API_KEY`

## 6. Проверка

1. Дождитесь, пока у всех четырёх сервисов статус станет **Live** (зелёный).
2. Откройте адрес **pravovoy-potok-frontend** в браузере — должна
   открыться главная страница «Правовой Поток».
3. Пройдите обычный путь: регистрация → поток → сбор → редактирование →
   публикация.
4. Если при регистрации/входе видите ошибку сети или куки не сохраняются —
   проверьте, что у API выставлена `COOKIE_SAMESITE=none` (это уже задано
   в `render.yaml` по умолчанию, но если меняли вручную — сверьте).

## Что нельзя перенести на Render автоматически

- **Почта** (восстановление пароля) и **платёжный провайдер** — по-прежнему
  не подключены, как и на локальном запуске (см. `docs/ROADMAP.md`).
- **Яндекс.Диск** для экспорта — не подключён.
- Домен по умолчанию — `*.onrender.com`. Если купите свой домен, в Render
  это делается в настройках каждого сервиса (**Settings → Custom Domain**);
  тогда можно вернуть `COOKIE_SAMESITE=lax` и указать `COOKIE_DOMAIN` на
  общий родительский домен, если заведёте API и frontend на поддоменах
  одного домена — так безопаснее, чем `none`.
