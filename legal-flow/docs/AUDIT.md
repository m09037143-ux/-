# Аудит: экран прототипа → API → состояние реализации

Прототип: `pravovoy-potok-editorial-registration.html` (загруженный вместе с ТЗ).
Ниже — таблица «экран интерфейса → серверный API → состояние», требуемая ТЗ,
этап A. Легенда: ✅ реализовано и покрыто тестами · 🟡 реализовано частично /
как заглушка · ⛔ не реализовано (осознанно, см. `ROADMAP.md`).

| Экран прототипа | Серверный API | Состояние |
|---|---|---|
| `/`, `/features`, `/pricing`, `/faq`, `/contacts`, `/privacy` (публичные страницы) | `GET /api/v1/plans` (для `/pricing`); остальное — статический контент, серверных данных не требует | ✅ `/plans`; остальное вне бэкенда |
| `/register` | `POST /api/v1/auth/register` | ✅ Argon2id, старт триала, сессия+CSRF |
| `/login` | `POST /api/v1/auth/login` | ✅ |
| `/forgot` (восстановление) | `POST /api/v1/auth/forgot-password`, `POST /api/v1/auth/reset-password` | ✅ логика полная; **отправка письма — `NOT_CONFIGURED`** (почтовый сервис не выбран, ТЗ §15) |
| Кнопка «Выйти» | `POST /api/v1/auth/logout` | ✅ |
| `/app` (дашборд, баннер триала/тарифа) | `GET /api/v1/access` | ✅ триал считается сервером, UI лишь показывает |
| `/app/setup` (мастер: сайты → тема → расписание → лимит) | `POST /api/v1/flows`, `PATCH /api/v1/flows/{id}` | ✅ лимиты по тарифу проверяются (`PLAN_LIMIT`) |
| `/app/sources` (список сайтов, вкл/выкл, добавить/удалить) | `POST/DELETE /api/v1/flows/{id}/sources` | ✅ |
| `/app/schedule` + кнопка «Приступить к сбору» | `POST /api/v1/flows/{id}/scan-jobs`, `GET /api/v1/scan-jobs/{id}` | ✅ идемпотентно (`Idempotency-Key`), обрабатывается отдельным worker'ом |
| «Демонстрационный агент» (прогресс сбора) | `GET /api/v1/scan-jobs/{id}` (poll) | ✅ статус `pending/running/done/failed`; реального прогресса в % сервер не считает — только шаги статуса |
| `/app/news` (список, фильтры, поиск) | `GET /api/v1/news` | ✅ фильтр по статусу/домену/тексту, пагинация |
| `/app/detail` → вкладка «Текст для публикации» | `PATCH /api/v1/news/{id}/draft` | ✅ optimistic locking (`expected_version`/`VERSION_CONFLICT`) |
| `/app/detail` → «Источник обнаружения» | `GET /api/v1/news/{id}` (поле `discovery_domain`/`discovery_original_fragment`) | ✅ только во внутренней выдаче, никогда в публичной/XML |
| `/app/detail` → «Официальные источники» | `PUT /api/v1/news/{id}/official-document` | ✅ https-валидация URL, сброс отметок проверки при правке |
| `/app/detail` → «Паспорт фактов» | `PUT /api/v1/news/{id}/facts` | ✅ статусы `confirmed/unknown/needs_review/contradictory` |
| Кнопка «Проверка редактором» | `POST /api/v1/news/{id}/review` | ✅ ставит `READY_FOR_REVIEW` только при обеих отметках |
| Кнопка «Утвердить материал» | `POST /api/v1/news/{id}/approve` | ✅ требует документ + обе отметки |
| Кнопка «Отклонить» | `POST /api/v1/news/{id}/reject` | ✅ причина обязательна |
| `/app/detail` → «Предпросмотр для читателя» | `GET /api/v1/news/{id}/public-preview` | ✅ `PublicNews`, без сайта обнаружения |
| `/app/detail` → «История» | `GET /api/v1/activity` (по workspace); версии текста — `news_versions` (отдельного API листинга версий пока нет) | 🟡 полная история конкретного материала (только версии текста) не выведена отдельным эндпоинтом — есть в БД (`news_versions`), нужен `GET /news/{id}/versions` (не запрошен явно в ТЗ §12, добавить по необходимости) |
| «Демонстрационный агент» / реальный сбор | `FixtureSourceProvider` (в проде), `HttpSourceProvider`/`fetch_url` (примитив) | 🟡 Fixture — полностью рабочий и промаркирован `DEMO_FIXTURE`. Реальный сетевой сбор: **SSRF-безопасный fetch реализован и протестирован**, но извлечение новостей конкретно с garant.ru/pravo.ru (парсер разметки) не реализовано — нет согласованных правил сайта (ТЗ §8, §15) |
| Кнопка «Оплатить картой» | `POST /api/v1/orders/{id}/pay` | ⛔ намеренно: всегда `PAYMENT_NOT_CONFIGURED`, деньги никогда не списываются |
| `/app/exports` (XML) | `POST/GET /api/v1/exports`, `GET /api/v1/exports/{id}/download` | ✅ `NOT_SENT`, идемпотентно по хешу содержимого |
| «Яндекс.Диск» | — | ⛔ не подключён (ТЗ §15 — нужны OAuth/путь/правила перезаписи от заказчика) |
| `/app/activity` | `GET /api/v1/activity` | ✅ |
| `/app/settings` | `GET /api/v1/auth/me`, (смена имени потока — `PATCH /api/v1/flows/{id}`) | 🟡 смена имени пользователя/профиля как отдельный эндпоинт не добавлена (не входит в обязательный список ТЗ §12) |
| `/app/admin` | `GET/POST/PATCH /api/v1/admin/source-policies`, `GET /api/v1/admin/users`, `GET/PATCH /api/v1/admin/plans` | ✅ доступ только `platform_admin`, отдельно от клиентского кабинета |
| ИИ-функции (в прототипе нет отдельного экрана — учтено на будущее по ТЗ §10) | `POST /api/v1/news/{id}/ai/draft`, `POST /api/v1/news/{id}/ai/critique` | 🟡 YandexGPT Pro 5.1 provider реализован полностью (конфигурация, лимиты, журнал вызовов, `LLM_NOT_CONFIGURED`), но без реального `YANDEX_API_KEY`/`YANDEX_FOLDER_ID` работает только в `LLM_FIXTURE_MODE` — см. `ROADMAP.md` |

## Подключение фронтенда (Этап F)

Изначально этап F был отложен (ТЗ §1 предупреждает не восстанавливать
интеграцию «на глаз» без актуального HTML от заказчика), но затем выполнен
по отдельному запросу пользователя. Исходный `pravovoy-potok-editorial-
registration.html` был чистым frontend-макетом на localStorage без единого
сетевого запроса; итоговая версия — `legal-flow/frontend/index.html` +
`app.js` — та же вёрстка/CSS без изменений, но с логикой, полностью
переписанной поверх реального API (`fetch(..., {credentials:'include'})` +
`X-CSRF-Token`). Живая проверка через Playwright прогнала полный
пользовательский путь и вскрыла (и позволила исправить) несколько реальных
ошибок — см. `README.md` → «Frontend» и `ROADMAP.md` → «Этап F».
