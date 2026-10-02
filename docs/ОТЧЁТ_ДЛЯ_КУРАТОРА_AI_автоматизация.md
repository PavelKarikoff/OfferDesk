# Отчёт о выполненной работе  
## Тема урока: «AI-автоматизация»

**Кому:** куратор Анна  
**Проект:** OfferDesk — рабочее место ОП, автогенерация коммерческих предложений (КП)  
**Репозиторий:** https://github.com/PavelKarikoff/OfferDesk  
**Контекст:** автоматизация подготовки документов отдела продаж строительной компании «Дом-Мастер» на основе транскрибации клиентского звонка и технологий ИИ  
**Дата актуализации отчёта:** 4 августа 2026  

---

## 1. Цель работы

Изучить и применить на практике цепочку **AI-автоматизации документов**:

> транскрибация диалога → анализ LLM (структурированный JSON) → HTML-шаблон (Jinja2) → PDF (WeasyPrint) → доставка менеджеру (Telegram)

Практически — сократить ручную работу менеджера ОП после звонка: вместо ручной сборки сметы и КП OfferDesk за минуты выдаёт готовый пакет PDF.

Дополнительно по актуальному ДЗ урока: **аудит кода с ИИ (Cursor)**, правки, README и зависимости, **OpenAPI**, **Dockerfile**, локальный запуск в контейнере; по желанию — **Docker Hub** и развёртывание на **VPS**.

---

## 2. Краткий итог (актуальное состояние)

Проект — сквозной прототип с несколькими точками входа:

| Точка входа | Назначение |
|---|---|
| `bot.py` | Telegram-бот для менеджера: диалог → проверка данных → КП → АР/ИР → PDF / ZIP |
| `main.py` + `flask_app.py` | CLI и HTTP API на Python (Flask): `/health`, `/api/report`, `/api/kp` |
| `go_server/` | Тот же HTTP API на **Go** + Python-bridge для генерации PDF |
| Docker / Compose | Образы Flask/bot и Go API; запуск локально и на сервере |

Стек: **OpenAI, Jinja2, WeasyPrint, python-dotenv, Flask, aiogram 3, pypdf**, плюс **Go**, **Docker**, **OpenAPI 3.1**.

**Публикация:** код и документация в GitHub (коммит `51283e9` от 04.08.2026).  
**Деплой (по желанию ДЗ):** образы в Docker Hub (`pavelkoff/ai-auogeneration`, `pavelkoff/ai-auogeneration-go`); Go API на VPS отвечает `http://…:5002/health` → `{"status":"ok"}`.

---

## 3. Этапы выполнения

### Этапы A–E (ранее) — ядро продукта

Кратко (подробности сохранены в логике проекта):

| Этап | Содержание |
|---|---|
| **A** | Базовый пайплайн: транскрибация → OpenAI JSON → Jinja2 → WeasyPrint PDF (A4, DejaVu), Flask |
| **B** | Предметная область «Дом-Мастер», эталон `sample_dialog.txt`, отчёты client / design / АР / ИР |
| **C** | Три варианта КП (базовый / средний / средний+), сводный PDF, опции ФЗ и ИР |
| **D** | Telegram-бот (FSM): sufficiency → выбор КП → АР/ИР → скачать / объединить / ZIP |
| **E** | Логи, README, About, MIT, скриншоты, публикация на GitHub |

### Этап F. Работа 4 августа 2026 (текущее ДЗ) — подробно

Ниже — что делали **сегодня**, **зачем** и **чем подтверждается**.

#### F1. Аудит кода с помощью ИИ в Cursor + правки + проверка запуска

**Зачем:** найти баги, риски безопасности и слабые места прототипа до «упаковки» в Docker и выкладки на сервер; выполнить требование урока «аудит → правки → проверить запуск».

**Что сделано:**

1. Аудит пакета `utils/` и всего проекта (`bot.py`, `main.py`, `flask_app.py`, шаблоны).
2. Зафиксирован отчёт аудита: [`AUDIT.md`](../AUDIT.md).
3. Применены правки, в том числе:
   - allowlist Telegram (`TELEGRAM_ALLOWED_IDS`);
   - токен HTTP API (`FLASK_API_TOKEN` / `X-API-Token`), без токена — только localhost;
   - `debug=False` у Flask, лимит тела запроса;
   - имя заказчика из LLM (не хардкод «Иван»);
   - согласование семантики цен в сводной смете с флагами АР/ИР;
   - fail-closed при ошибках merge/отчётов; меньше утечек текста exception в UX;
   - модули `utils/config.py`, `utils/money.py`; логирование вместо «голых» print.
4. Проверен запуск CLI / бота / API (локально и затем в Docker).

**Для куратора:** смотреть `AUDIT.md` + diff в репозитории.

#### F2. README, зависимости, документация, OpenAPI

**Зачем:** чтобы проект можно было воспроизвести без устных пояснений; OpenAPI — контракт HTTP API для проверки эндпоинтов.

**Что сделано:**

| Артефакт | Назначение |
|---|---|
| `README.md` | быстрый старт: бот, CLI, Flask, Go, Docker |
| `ABOUT.md` + поле About на GitHub | краткое описание репозитория |
| `requirements.txt` / `requirements.lock.txt` | зависимости (диапазоны и lock) |
| `docs/DOCUMENTATION.md` | полная документация |
| `docs/openapi.yaml` | OpenAPI 3.1 (`/health`, `/api/report`, `/api/kp`) |
| `docs/DOCKER.md`, `docs/DOCKER_HUB.md` | локальный Docker и перенос на сервер |

#### F3. Dockerfile, локальная сборка и запуск контейнера

**Зачем:** одинаковое окружение (Python + системные библиотеки WeasyPrint) без ручной настройки на каждой машине; требование урока «написать Dockerfile → собрать → запустить локально → проверить сервис».

**Что сделано:**

1. Корневой `Dockerfile` (Flask API + бот), `docker-compose.yml`, `.dockerignore`, `scripts/docker-entrypoint.sh`.
2. Отдельный `go_server/Dockerfile` для Go API.
3. Локально (macOS): порты хоста **5001** (Flask) и **5002** (Go), т.к. 5000 часто занят AirPlay.
4. Проверка: `curl http://127.0.0.1:5002/health` → `{"status":"ok"}`.
5. Скрипт проверки эндпоинтов: `scripts/check_endpoints.sh`.

#### F4. Go HTTP API (расширение проекта)

**Зачем:** тот же контракт API на Go (учебный/инженерный слой), генерация PDF через Python-bridge к существующему `utils/` — без дублирования всей логики документов.

**Что сделано:** каталог `go_server/` (модульная структура: routes, handlers, middleware, bridge), README, OpenAPI-копия. Эндпоинты совместимы с Flask.

#### F5. Docker Hub + развёртывание на VPS (по желанию ДЗ)

**Зачем:** показать полный цикл «собрал образ → опубликовал → поднял на сервере», как в боевом сценарии поставки.

**Что сделано 04.08.2026:**

1. Аккаунт Docker Hub, `docker login`, скрипт `scripts/push_dockerhub.sh`.
2. Сборка и push образов:
   - `pavelkoff/ai-auogeneration:latest`
   - `pavelkoff/ai-auogeneration-go:latest`
3. VPS Ubuntu 24.04 (`109.71.246.185`): установка Docker, клон репозитория, `.env` с секретами **только на сервере** (не в git / не в образ).
4. Запуск: `./scripts/pull_and_run.sh go-api`.
5. Проверки:
   - на сервере: `curl http://127.0.0.1:5002/health` → ok;
   - с Mac / в браузере: `http://109.71.246.185:5002/health` → `{"status":"ok"}`;
   - POST `/api/kp` с заголовком `X-API-Token` (токен из серверного `.env`).
6. Уточнение для проверки: в браузере виден только `/health` (JSON) — это **API**, не сайт; UI менеджера — Telegram-бот.

#### F6. Синхронизация с GitHub

**Зачем:** единый источник правды для куратора и для сервера (`git pull` вместо ручного `scp`).

**Что сделано:** коммит и push всех изменений (аудит, Docker, Go, docs, OpenAPI) в  
https://github.com/PavelKarikoff/OfferDesk  
Обновлены README и About репозитория. На VPS выполнен `git fetch` / `git reset --hard origin/main` → `HEAD` на `51283e9`.

---

## 4. Соответствие заданию урока (чек-лист)

| Пункт задания | Статус | Где смотреть / как проверить |
|---|---|---|
| Аудит кода с ИИ в Cursor | ✅ | `AUDIT.md` |
| Применить правки | ✅ | `bot.py`, `flask_app.py`, `utils/`, … |
| Проверить запуск | ✅ | локально + Docker + VPS `/health` |
| README | ✅ | `README.md` |
| Файл зависимостей | ✅ | `requirements.txt`, `requirements.lock.txt` |
| OpenAPI (по желанию) | ✅ | `docs/openapi.yaml` |
| Dockerfile | ✅ | `Dockerfile`, `go_server/Dockerfile` |
| Собрать образ и запустить локально | ✅ | `docker compose`, health на `:5002` |
| Docker Hub (по желанию) | ✅ | `pavelkoff/ai-auogeneration(-go)` |
| Развернуть на сервере (по желанию) | ✅ | VPS, порт 5002, `/health` = ok |

---

## 5. Архитектура решения (актуально)

```text
Транскрибация (.txt / Telegram)
        │
        ▼
┌───────────────────┐
│  OpenAI (LLM)     │  sufficiency / JSON / промпты АР·ИР
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  Jinja2 templates │  kp / ar / engineering / summary
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐     ┌──────────────┐
│  WeasyPrint PDF   │────▶│  pypdf merge │
└─────────┬─────────┘     └──────────────┘
          │
          ├──────────────▶ Telegram / ZIP / reports/
          │
          ▼
   HTTP API (Flask или Go)  ── Docker ──▶ Docker Hub ──▶ VPS
```

**Ключевые модули `utils/`:**  
`ai_processor`, `pdf_generator`, `report_service`, `image_generator`, `kp_generator`, `engineering_generator`, `sufficiency`, `package_builder`, `combined_document`, `logging_setup`, `config`, `money`.

---

## 6. Демонстрационные материалы

| Материал | Путь / адрес |
|---|---|
| Эталон диалога | `sample_dialog.txt` |
| Аудит | `AUDIT.md` |
| OpenAPI | `docs/openapi.yaml` |
| Docker / Hub → сервер | `docs/DOCKER.md`, `docs/DOCKER_HUB.md` |
| Скриншоты бота | `docs/screenshots/` |
| Пример сводного КП | `docs/KP_ALL_combined_*.pdf` (если приложен в репо) |
| Live API (health) | `http://<VPS>:5002/health` |

---

## 7. Как воспроизвести результат

### Локально (Python)

```bash
git clone https://github.com/PavelKarikoff/OfferDesk.git
cd OfferDesk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, FLASK_API_TOKEN

python main.py sample_dialog.txt --type ar
python main.py --kp
python bot.py
```

### Локально (Docker)

```bash
cp .env.example .env   # заполнить ключи
docker compose up -d --build go-api
curl -sS http://127.0.0.1:5002/health
```

### С Docker Hub на сервере

```bash
# на машине сборки:
export DOCKERHUB_USER=pavelkoff
./scripts/push_dockerhub.sh

# на сервере:
git clone https://github.com/PavelKarikoff/OfferDesk.git
cd OfferDesk && cp .env.example .env   # секреты
export DOCKERHUB_USER=pavelkoff
./scripts/pull_and_run.sh go-api
```

Подробные шаги — в `docs/DOCKER_HUB.md`.

---

## 8. Чему научился по теме «AI-автоматизация» (+ DevOps-слой ДЗ)

1. **Промпт-инжиниринг** под строгий JSON и разные роли (аналитик, архитектор, инженер, completeness).
2. **Разделение LLM и рендера:** модель даёт данные; шаблоны и WeasyPrint — печатный вид.
3. **Продуктовый контур:** документ доводится до менеджера в Telegram с выбором вариантов.
4. **Аудит с ИИ:** систематический разбор рисков (auth, цены, утечки ошибок) и внесение правок.
5. **Контейнеризация:** Dockerfile / Compose снимают «у меня не ставится WeasyPrint».
6. **Поставка:** образ в Docker Hub → pull на VPS → `.env` только на сервере; API с токеном.
7. **Ограничения прототипа:** прайсы ориентировочные; e-mail — stub; без HTTPS на демо-порту.

---

## 9. Возможное развитие

1. БД / 1С / ERP — актуальные прайс-листы.  
2. Реальная отправка КП по e-mail.  
3. Выгрузка в CRM.  
4. ТЗ для внешней инженерной компании.  
5. HTTPS (Caddy/nginx) и ограничение firewall по IP для API.

---

## 10. Заключение для куратора Анны

По теме **«AI-автоматизация»** выполнен сквозной проект: от PDF-отчёта по диалогу до Telegram-бота с пакетом КП / АР / ИР.

**4 августа 2026** дополнительно закрыто актуальное задание урока:

- аудит в Cursor → правки → проверка запуска;  
- README, зависимости, OpenAPI;  
- Dockerfile, локальные контейнеры;  
- (по желанию) Docker Hub и рабочий API на VPS.

Решение демонстрирует паттерн: **извлечь структуру → оформить документ → доставить в рабочий канал**, плюс базовый контур поставки через контейнеры.

Готово к проверке по репозиторию  
https://github.com/PavelKarikoff/OfferDesk  
(`AUDIT.md`, `docs/`, Docker-файлы, `go_server/`, скриншоты в `docs/screenshots/`).
