# Перенос проекта через Docker Hub → запуск на сервере

Пошаговая инструкция: собрать образы на Mac → загрузить в Docker Hub → скачать и запустить на Linux-сервере (VPS).

---

## Что куда попадает

| Куда | Что |
|------|-----|
| **Docker Hub** | Только **образы** (код + зависимости внутри контейнера) |
| **НЕ в Hub** | Файл `.env`, ключи OpenAI/Telegram, `FLASK_API_TOKEN` |
| **На сервер** | Образы из Hub + `docker-compose.yml` + свой `.env` (+ опционально git-репозиторий) |

Образы проекта:

| Образ | Содержимое | Порт в контейнере |
|-------|------------|-------------------|
| `ВАШ_ЛОГИН/ai-auogeneration` | Flask API + Telegram-бот | `5000` |
| `ВАШ_ЛОГИН/ai-auogeneration-go` | Go API + Python bridge | `5001` |

На хосте по умолчанию:

| Сервис | URL на сервере |
|--------|----------------|
| Flask | `http://SERVER_IP:5001` |
| Go API | `http://SERVER_IP:5002` |

---

## Часть 0. Что нужно заранее

### На Mac (машина сборки)

1. Установлен и запущен **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**.
2. Аккаунт на **[hub.docker.com](https://hub.docker.com/)** (у вас логин уже есть: тот, что в `docker login`).
3. Локально открыт проект `AI_Auogeneration`, команды выполняются из **корня** репозитория.
4. В `.env` есть рабочие ключи (хотя бы для проверки у себя):
   - `OPENAI_API_KEY`
   - `FLASK_API_TOKEN` (любой длинный секрет, который вы придумали)

Проверка Docker:

```bash
docker version
docker compose version
docker info
```

### На сервере (машина запуска)

Типичный VPS: Ubuntu 22.04 / 24.04, Debian 12.

Нужно:

1. Доступ по SSH: `ssh user@SERVER_IP`
2. Права `sudo` (для установки Docker, если ещё нет)
3. Открытые в firewall порты **5001** и/или **5002** (если API должен быть снаружи)
4. Файл `.env` с секретами (создадите на сервере вручную)

**На сервере не обязательны:** Go, Python, WeasyPrint — всё внутри образов.

---

## Часть 1. Mac — логин в Docker Hub

### 1.1. Access Token (рекомендуется вместо пароля)

1. Откройте [hub.docker.com](https://hub.docker.com/) → войдите.
2. Account Settings → **Security** → **New Access Token**.
3. Имя, например `mac-push`, права **Read, Write, Delete** (или Read & Write).
4. Скопируйте токен (показывается один раз).

### 1.2. Логин в терминале

```bash
docker logout          # на всякий случай
docker login
```

- Username: ваш логин Hub (например `pavelkoff`)
- Password: **Access Token** (не пароль от сайта, если включена 2FA)

Успех:

```text
Login Succeeded
```

---

## Часть 2. Mac — сборка и push образов

Все команды из **корня проекта**:

```bash
cd /Users/pavelkoff/Desktop/AI_Auogeneration
```

### 2.1. Указать логин Hub

Подставьте **свой** логин (не плейсхолдер):

```bash
export DOCKERHUB_USER=pavelkoff
```

### 2.2. Собрать и отправить

```bash
chmod +x scripts/push_dockerhub.sh
./scripts/push_dockerhub.sh
```

Скрипт:

1. Собирает Flask/bot-образ → `pavelkoff/ai-auogeneration:latest`
2. Собирает Go-образ → `pavelkoff/ai-auogeneration-go:latest`
3. Делает `docker push` обоих

С тегом версии (удобно для откатов):

```bash
./scripts/push_dockerhub.sh v1.0.0
```

Появятся теги `v1.0.0` и `latest`.

### 2.3. Проверить на Hub

Откройте в браузере:

- `https://hub.docker.com/r/ВАШ_ЛОГИН/ai-auogeneration`
- `https://hub.docker.com/r/ВАШ_ЛОГИН/ai-auogeneration-go`

Или:

```bash
docker pull ВАШ_ЛОГИН/ai-auogeneration:latest
docker pull ВАШ_ЛОГИН/ai-auogeneration-go:latest
```

### 2.4. Типичные ошибки push

| Ошибка | Причина | Что сделать |
|--------|---------|-------------|
| `invalid reference format` | В `DOCKERHUB_USER` кириллица/плейсхолдер | `export DOCKERHUB_USER=pavelkoff` |
| `denied` / `unauthorized` | Не залогинены / чужой namespace | `docker login` своим аккаунтом |
| `repository name must be lowercase` | Заглавные буквы в имени | логин и имя репо — lowercase |
| Долгая сборка Go | Нормально (качает golang + python deps) | подождать 5–15 мин |

---

## Часть 3. Сервер — установка Docker

Подключитесь:

```bash
ssh user@SERVER_IP
```

### 3.1. Ubuntu / Debian (официальный способ, кратко)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Для Ubuntu 22.04/24.04:
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Чтобы не писать sudo каждый раз:
sudo usermod -aG docker "$USER"
# затем выйдите из SSH и зайдите снова
```

Проверка:

```bash
docker version
docker compose version
```

Альтернатива: скрипт [get.docker.com](https://get.docker.com) — только если понимаете риски.

### 3.2. Firewall (если ufw)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 5002/tcp    # Go API
sudo ufw allow 5001/tcp    # Flask (если нужен)
sudo ufw enable
sudo ufw status
```

У облачных провайдеров (Timeweb, Selectel, AWS, Hetzner и т.д.) откройте те же порты ещё и в **Security Group / Firewall панели**.

---

## Часть 4. Сервер — файлы проекта

На сервере нужны не исходники целиком для сборки, а минимум:

- `docker-compose.yml`
- `.env` (создаёте сами)
- опционально `scripts/check_endpoints.sh`, `sample_dialog.txt`

Самый простой путь — **клонировать репозиторий** (образы всё равно с Hub, локально не собираете):

```bash
cd ~
git clone https://github.com/PavelKarikoff/OfferDesk.git
cd OfferDesk
```

Если репозиторий приватный — настройте SSH-ключ или token для `git clone`.

Без git: скопируйте с Mac через `scp`:

```bash
# на Mac:
scp docker-compose.yml .env.example user@SERVER_IP:~/AI_Auogeneration/
# на сервере создайте каталог и .env вручную
```

---

## Часть 5. Сервер — `.env`

```bash
cd ~/AI_Auogeneration
cp .env.example .env
nano .env
```

Обязательно заполните (пример структуры, **не копируйте чужие ключи**):

```env
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini
OPENAI_IMAGE_MODEL=gpt-image-1
OPENAI_IMAGE_SIZE=1024x1024

FLASK_API_TOKEN=длинный-случайный-секрет-с-сервера

# для бота (если будете запускать):
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_IDS=ваш_telegram_id

# порты на хосте (можно не трогать)
API_HOST_PORT=5001
GO_API_HOST_PORT=5002

# образы с Docker Hub
DOCKERHUB_USER=pavelkoff
DOCKERHUB_IMAGE_API=pavelkoff/ai-auogeneration:latest
DOCKERHUB_IMAGE_GO=pavelkoff/ai-auogeneration-go:latest
```

Права на `.env`:

```bash
chmod 600 .env
```

Сгенерировать токен API:

```bash
openssl rand -hex 24
# вставьте результат в FLASK_API_TOKEN
```

---

## Часть 6. Сервер — pull и запуск

### 6.1. Логин на Hub (если репозитории приватные)

Для **публичных** образов логин не обязателен. Для приватных:

```bash
docker login
```

### 6.2. Запуск Go API (рекомендуется)

```bash
cd ~/AI_Auogeneration
export DOCKERHUB_USER=pavelkoff

chmod +x scripts/pull_and_run.sh
./scripts/pull_and_run.sh go-api
```

Или вручную:

```bash
export DOCKERHUB_IMAGE_API=pavelkoff/ai-auogeneration:latest
export DOCKERHUB_IMAGE_GO=pavelkoff/ai-auogeneration-go:latest

docker compose pull go-api
docker compose up -d go-api
```

### 6.3. Проверка на сервере

```bash
docker compose ps
docker compose logs -f go-api
# Ctrl+C чтобы выйти из логов

curl -sS http://127.0.0.1:5002/health
# {"status":"ok"}
```

С вашего Mac (если порт открыт):

```bash
curl -sS http://SERVER_IP:5002/health
```

Запрос с токеном:

```bash
curl -sS -X POST "http://SERVER_IP:5002/api/kp" \
  -H "Content-Type: application/json" \
  -H "X-API-Token: ТОТ_ЖЕ_ЧТО_В_ENV_НА_СЕРВЕРЕ" \
  -d '{"with_fz":false,"with_engineering":false,"client_name":"Тест"}'
```

### 6.4. Flask API (если нужен параллельно)

```bash
./scripts/pull_and_run.sh api
# http://SERVER_IP:5001/health
```

Оба сразу:

```bash
./scripts/pull_and_run.sh all
```

### 6.5. Telegram-бот на сервере

```bash
docker compose --profile bot up -d
docker compose logs -f bot
```

---

## Часть 7. Проверка эндпоинтов со скриптом

На сервере (если есть `scripts/check_endpoints.sh`):

```bash
cd ~/AI_Auogeneration
# не экспортируйте другой токен — пусть скрипт возьмёт из .env
unset FLASK_API_TOKEN
BASE_URL=http://127.0.0.1:5002 ./scripts/check_endpoints.sh --quick
BASE_URL=http://127.0.0.1:5002 ./scripts/check_endpoints.sh
# полный прогон с OpenAI:
BASE_URL=http://127.0.0.1:5002 ./scripts/check_endpoints.sh --full
```

С Mac на удалённый сервер:

```bash
unset FLASK_API_TOKEN
# скопируйте .env токен вручную или:
export FLASK_API_TOKEN='секрет_с_сервера'
BASE_URL=http://SERVER_IP:5002 ./scripts/check_endpoints.sh --quick
```

---

## Часть 8. Обновление версии на сервере

На Mac после правок кода:

```bash
export DOCKERHUB_USER=pavelkoff
./scripts/push_dockerhub.sh v1.0.1
```

На сервере:

```bash
cd ~/AI_Auogeneration
git pull   # если обновились compose/scripts
docker compose pull go-api
docker compose up -d go-api
```

Откат на старый тег — в `.env`:

```env
DOCKERHUB_IMAGE_GO=pavelkoff/ai-auogeneration-go:v1.0.0
```

затем `docker compose pull go-api && docker compose up -d go-api`.

---

## Часть 9. Полезные команды на сервере

```bash
# статус
docker compose ps

# логи
docker compose logs -f go-api
docker compose logs --tail=100 go-api

# перезапуск
docker compose restart go-api

# стоп
docker compose stop go-api

# стоп и удаление контейнеров (volumes с reports сохранятся, если не -v)
docker compose down

# место на диске
docker system df
```

Сгенерированные PDF лежат в `./reports` на сервере (volume).

---

## Часть 10. Безопасность (обязательно прочитайте)

1. **Не коммитьте `.env`** в git и не кладите ключи в образ.
2. На публичном сервере **всегда** задавайте сильный `FLASK_API_TOKEN`.
3. Не открывайте порты 5001/5002 всему интернету без необходимости — лучше:
   - nginx/Caddy + HTTPS + Basic Auth / только VPN, или
   - firewall: доступ только с вашего IP.
4. Если ключи когда-либо светились в чате/скрине — **смените** OpenAI и Telegram токены.
5. Регулярно: `docker compose pull` + обновления ОС на сервере.

---

## Часть 11. Troubleshooting

| Симптом | Что проверить |
|---------|----------------|
| `Could not connect` с Mac на сервер | firewall ufw + security group облака; `docker compose ps` |
| `401` на API | заголовок `X-API-Token` = `FLASK_API_TOKEN` из **серверного** `.env` |
| `health` ок, PDF падает | `OPENAI_API_KEY` в `.env`, логи: `docker compose logs go-api` |
| Образ не пуллится | опечатка в `DOCKERHUB_USER`; репозиторий private → `docker login` |
| Порт занят | сменить `GO_API_HOST_PORT=8080` в `.env` и `up -d` снова |
| Контейнер Restarting | `docker compose logs go-api` — часто нет `.env` или синтаксис env |

---

## Краткая шпаргалка

**Mac → Hub**

```bash
docker login
export DOCKERHUB_USER=pavelkoff
./scripts/push_dockerhub.sh
```

**Сервер → запуск**

```bash
git clone <repo> && cd AI_Auogeneration
cp .env.example .env && nano .env
export DOCKERHUB_USER=pavelkoff
./scripts/pull_and_run.sh go-api
curl http://127.0.0.1:5002/health
```

Связанные файлы: [`docker-compose.yml`](../docker-compose.yml), [`scripts/push_dockerhub.sh`](../scripts/push_dockerhub.sh), [`scripts/pull_and_run.sh`](../scripts/pull_and_run.sh).
