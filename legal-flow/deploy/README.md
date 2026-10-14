# Развёртывание «Правового Потока» на своём сервере (VPS)

Результат: сайт работает на `https://pravpotok.ru` на одном сервере, постоянно работает воркер
(сбор по расписанию, автоматические черновики), есть резервные копии базы. Страница сама получает
HTTPS-сертификат. Работает на любом VPS с Ubuntu 22.04/24.04 (Рег.ру, Timeweb Cloud и др.).

```
интернет → Caddy (порты 80/443, HTTPS) → app (сайт + API) ─┬→ PostgreSQL (внутри сети)
                                           worker (сбор, черновики) ─┘→ Redis (внутри сети)
```

Общий (shared) хостинг для этого **не подходит**: нужны PostgreSQL, Redis и постоянно работающий процесс.

## 1. Закажите сервер
- Ubuntu 24.04 LTS (или 22.04), виртуализация KVM, **2 ядра, 2 ГБ памяти, 30–40 ГБ SSD**, публичный IPv4.
- При заказе выберите вход **по SSH-ключу** (создание ключа — в шаге 2) и запишите IP-адрес сервера.

## 2. SSH-ключ на вашем компьютере (Windows, PowerShell)
```
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\pp_server
```
Публичный ключ — файл `pp_server.pub` (его содержимое вставляется в панель хостинга при заказе сервера).
Закрытый ключ (`pp_server` без `.pub`) никому не отправляйте.

## 3. Первичная защита сервера
Подключитесь: `ssh -i $env:USERPROFILE\.ssh\pp_server root@IP_СЕРВЕРА`, затем на сервере:
```
adduser deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh && cp ~/.ssh/authorized_keys /home/deploy/.ssh/ && chown -R deploy:deploy /home/deploy/.ssh
```
Проверьте вход под `deploy` (в новом окне: `ssh -i ... deploy@IP`), и только потом закройте вход по паролю и под root:
```
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config && sudo systemctl restart ssh
```
Файрвол, автообновления безопасности и файл подкачки:
```
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw --force enable
```
```
sudo apt-get update && sudo apt-get install -y unattended-upgrades git
```
```
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 4. Docker
```
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker deploy
```
Выйдите и зайдите снова, чтобы группа `docker` применилась.

## 5. Код проекта
Сервер читает репозиторий по **deploy-ключу только для чтения**:
```
ssh-keygen -t ed25519 -f ~/.ssh/pp_deploy -N ""
cat ~/.ssh/pp_deploy.pub
```
Добавьте этот публичный ключ на GitHub: репозиторий → Settings → Deploy keys → Add deploy key (галочку «Allow write access» **не** ставьте). Затем:
```
printf 'Host github.com\n  IdentityFile ~/.ssh/pp_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
```
```
sudo mkdir -p /opt/pravpotok && sudo chown deploy:deploy /opt/pravpotok && git clone -b claude/ecstatic-turing-lhf6m3 git@github.com:m09037143-ux/-.git /opt/pravpotok
```
(Ветка `claude/ecstatic-turing-lhf6m3` — текущая рабочая; когда вольёте её в `main`, переключите: `git checkout main`.)
Рабочая папка дальше: `cd /opt/pravpotok/legal-flow/deploy`.

## 6. Настройки (секреты)
```
cp .env.production.example .env && chmod 600 .env
```
Откройте `.env` (`nano .env`) и заполните:
- `POSTGRES_PASSWORD` и `CSRF_SECRET` — случайные значения: `openssl rand -hex 32` (два раза, по значению на переменную).
- `SECRETS_ENCRYPTION_KEY` — ключ шифрования ИИ-ключей из админки: `python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`. **Сохраните копию отдельно** (менеджер паролей): без него ключи, введённые в админке, не расшифровать.
- `YANDEX_FOLDER_ID`, `YANDEX_MODEL_URI`, `YANDEX_API_KEY` — **те же значения, что сейчас на Render** (скопируйте их из панели Render вручную; в чат/почту их отправлять не нужно).
Остальное уже задано под `pravpotok.ru`.

## 7. DNS-записи домена
У Рег.ру: личный кабинет → Домены → `pravpotok.ru` → «Управление DNS-зоной» (если домен делегирован на `ns1.reg.ru`/`ns2.reg.ru`).
Если домен сейчас указывает на `ns1.hosting.reg.ru`/`ns2.hosting.reg.ru` (серверы имён хостинга), записями управляют в ispmanager (раздел «Доменные имена» → `pravpotok.ru` → DNS), либо в личном кабинете сначала переключите домен на DNS Рег.ру.

| Тип | Имя | Значение |
|---|---|---|
| A | `@` (корень домена) | IP вашего сервера |
| A | `www` | IP вашего сервера |

Старые записи `A`/`AAAA` для `@` и `www`, ведущие на прежний хостинг, удалите; почтовые `MX` и `TXT` не трогайте.
Проверка (через 5–30 минут): `nslookup pravpotok.ru` должен показать IP сервера.

## 8. Перенос данных с Render (чтобы остались пользователи, материалы, ключи)
Если начинать с пустой базы — пропустите шаг и сразу перейдите к шагу 9.

1. Панель Render → ваша база PostgreSQL → запишите **версию** PostgreSQL. В `docker-compose.prod.yml` стоит `postgres:18-alpine` (на Render сейчас PostgreSQL 18); версия сервера не должна быть ниже версии на Render. Если у вас на Render другая версия, поменяйте тег образа (для версий до 18 том монтируется на `/var/lib/postgresql/data`, для 18 и новее — на `/var/lib/postgresql`) и в команде `pg_dump` ниже замените `postgres:18-alpine` на ту же версию.
2. На странице базы возьмите **External Database URL** (Connect → External). Если Render запрещает внешний доступ, добавьте IP вашего сервера в «Access Control».
3. На сервере (адрес вводится скрытно и не остаётся в истории команд):
```
read -rs RENDER_DB_URL && export RENDER_DB_URL
```
```
docker run --rm -e RENDER_DB_URL postgres:18-alpine sh -c 'pg_dump --no-owner --no-acl "$RENDER_DB_URL"' | gzip > ~/render.sql.gz && unset RENDER_DB_URL
```
Размер `~/render.sql.gz` должен быть больше нуля (`ls -lh ~/render.sql.gz`).

## 9. Запуск
Перенос данных (если делали шаг 8):
```
./restore.sh ~/render.sql.gz
```
Запуск без переноса (или после него):
```
docker compose up -d --build
```
Проверка: `docker compose ps` — все сервисы `running`/`healthy`; журналы: `docker compose logs -f app`.
Откройте `https://pravpotok.ru`. После переезда браузер попросит войти заново (куки привязаны к домену) — логин и пароль прежние.

## 10. Переключение
1. Убедитесь, что на новом сайте всё работает (вход, материалы, «Приступить к сбору», «Экспорт»).
2. **Остановите воркер на своём компьютере** — он работает с базой Render, которая больше не нужна.
3. В панели Render приостановите сервис и базу (Suspend) — не удаляйте, пока не пройдёт неделя.
4. Старые ссылки и API-ключи продолжают работать на новом домене, если вы передадите новый адрес.

## 11. Резервные копии
```
crontab -e
```
Добавьте строку (копия базы каждую ночь в 03:00, хранится 14 дней):
```
0 3 * * * /opt/pravpotok/legal-flow/deploy/backup.sh >> /var/log/pp_backup.log 2>&1
```
Резервные копии лежат на том же сервере: **периодически копируйте папку `deploy/backups/` на другой диск или в облако**
(например, `scp -r deploy@IP:/opt/pravpotok/legal-flow/deploy/backups .`), иначе при потере сервера они потеряются вместе с ним.
Восстановление: `./restore.sh backups/pp_ДАТА.sql.gz`.

## 12. Обновление сайта
```
./update.sh
```
Скрипт сам делает копию базы, подтягивает код, пересобирает и перезапускает (миграции применяются при старте).

## Если что-то не работает
| Симптом | Что смотреть |
|---|---|
| Браузер не открывает сайт, нет HTTPS | DNS ещё не обновился (`nslookup pravpotok.ru`), закрыты порты 80/443 (`sudo ufw status`), `docker compose logs caddy` |
| 502 Bad Gateway | приложение не стартовало: `docker compose logs app` (частая причина — пустые/неверные значения в `.env`) |
| Сбор не идёт, материалы не появляются | `docker compose logs worker`; `docker compose ps` — воркер должен быть `running` |
| Нет места на диске | `docker system df`, `docker image prune -a`, удалить старые файлы в `backups/` |
| Забыли, что запущено | `docker compose ps` |

## Безопасность
- Порты базы и Redis наружу **не публикуются**; снаружи открыты только 80/443 (и 22 по ключу).
- Секреты хранятся только в `deploy/.env` (права 600, в git не попадает).
- Пароли, которые вы присылали в чат (хостинг u3680813), смените в панели хостинга.
- Ограничения входа и регистрации считают реальный IP клиента (`FORWARDED_ALLOW_IPS` задан, порт приложения снаружи недоступен).

## Что пока не входит
Отправка материалов на Яндекс.Диск и по e-mail (в разделе «Экспорт» помечены «Скоро»); для e-mail позже понадобится SMTP-сервис.
