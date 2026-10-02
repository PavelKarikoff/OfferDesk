# Ops: OfferDesk (production)

**Дата:** 2026-08-14  
**Хосты:** BlueTerbium `194.67.103.144` · NL-прокси `193.233.174.4`  
**CRM URL:** http://194.67.103.144:5001  
**GitHub:** https://github.com/PavelKarikoff/OfferDesk  
**Код на сервере:** `/root/AI_Bot_avto_KP` (исторический путь каталога, не путать с именем репозитория)

Продукт: **OfferDesk** (для «Дом-Мастер»). Юниты systemd пока `dommaster-*` — исторические имена, не переименовывать на проде без миграции.

Руководство менеджера: [`РУКОВОДСТВО_МЕНЕДЖЕРА.md`](РУКОВОДСТВО_МЕНЕДЖЕРА.md).

---

## 1. Архитектура

```text
Менеджер (браузер)
    → BlueTerbium :5001  (Flask/Waitress CRM, systemd: dommaster-crm)
    → SQLite web_app/deals.db
    → OpenAI API ──через──→ NL tinyproxy :8888 (только с IP BlueTerbium)
Telegram client / bot
    → dommaster-bot (polling, aiogram)
Health
    → timer dommaster-healthcheck (каждые 5 мин)
```

| Компонент | Где | Порт | Unit |
|-----------|-----|------|------|
| CRM UI/API | BlueTerbium | 5001 | `dommaster-crm.service` |
| Telegram-бот | BlueTerbium | — | `dommaster-bot.service` |
| Health-check | BlueTerbium | — | `dommaster-healthcheck.timer` |
| OpenAI HTTP proxy | NL | 8888 | `tinyproxy` + ufw |

---

## 2. Конфигурация (.env на VPS)

Файл: `/root/AI_Bot_avto_KP/.env` (не в git).

| Переменная | Назначение |
|------------|------------|
| `OPENAI_API_KEY` | генерация текстов КП |
| `OPENAI_PROXY` | `http://dommaster:***@193.233.174.4:8888` |
| `TELEGRAM_BOT_TOKEN` | бот + отправка КП |
| `TELEGRAM_ALLOWED_IDS` | allowlist менеджеров + health-алерты |
| `ETALON_KP_THRESHOLD` | порог % для КП (по умолчанию 80) |
| `CRM_PUBLIC_URL` | `http://194.67.103.144:5001` (ссылки, outbox) |
| `SMTP_*` | отправка КП на email |
| `SECRET_KEY` | сессии Flask |
| `HEALTH_ALERT_CHAT_ID` | опционально: куда слать FAIL health |
| `HEALTH_ALERT_COOLDOWN_MIN` | антиспам алертов (30) |

Локальные SSH-секреты BlueTerbium — в `.env` разработчика (`BLUETERBIUM_SSH_*`), не на сервере.

NL root / proxy BasicAuth: `~/.config/dommaster/nl_proxy.env` на машине админа.

---

## 3. Systemd

Unit-файлы в репо: `deploy/systemd/`.

```bash
# статус
systemctl status dommaster-crm dommaster-bot
systemctl list-timers dommaster-healthcheck.timer

# логи
journalctl -u dommaster-crm -u dommaster-bot -f
tail -f /root/AI_Bot_avto_KP/logs/app.log
tail -f /root/AI_Bot_avto_KP/logs/bot.log

# рестарт
systemctl restart dommaster-crm dommaster-bot
```

Установка/обновление unit-ов с Mac:

```bash
./scripts/install_systemd.sh
```

Особенности CRM unit:

- `Restart=always`, `ExecStartPre` создаёт `logs/` и освобождает `:5001`;
- stdout/stderr → `logs/app.log`;
- `PYTHONPATH` = корень репо (доступ к `utils/`).

Не запускайте параллельно nohup `python3 app.py` — будет конфликт порта (health увидит старый процесс).

---

## 4. Деплой кода

С Mac из корня репо:

```bash
./scripts/update_server.sh
```

Скрипт: rsync кода → бэкап `deals.db` → `systemctl restart` (если unit есть) → проверка `/health`.

Только git pull на сервере:

```bash
./scripts/update_server.sh --pull-only
```

После смены unit/logrotate/health:

```bash
./scripts/install_systemd.sh
```

---

## 5. Health / мониторинг

Скрипт: `scripts/health_check.sh`.

Проверяет:

1. `systemd` active для crm/bot  
2. `GET /health` и `GET /health?deep=1` (SQLite + наличие proxy)  
3. OpenAI через `OPENAI_PROXY` (ожидается HTTP 401 на тестовом ключе)  
4. Telegram `getMe`

```bash
# на сервере
bash /root/AI_Bot_avto_KP/scripts/health_check.sh
bash /root/AI_Bot_avto_KP/scripts/health_check.sh --json
bash /root/AI_Bot_avto_KP/scripts/health_check.sh --alert   # Telegram при FAIL
```

Timer: каждые 5 минут, oneshot `dommaster-healthcheck.service` с `--alert`.  
Лог: `logs/healthcheck.log`, state: `logs/healthcheck.state`.

Deep health вручную:

```bash
curl -sS 'http://127.0.0.1:5001/health?deep=1'
# {"status":"ok","service":"dommaster-crm","checks":{"db":"ok","openai_proxy":"configured"}}
```

---

## 6. Логротация

Конфиг: `deploy/logrotate/dommaster` → `/etc/logrotate.d/dommaster`.

- файлы: `logs/*.log`
- weekly **или** size 20M, 8 архивов, `copytruncate`, `su root root`
- каталог `logs/` должен быть `755` (не world-writable)

```bash
logrotate -d /etc/logrotate.d/dommaster
# принудительно (тест):
# logrotate -f /etc/logrotate.d/dommaster
```

---

## 7. NL-прокси (OpenAI)

| Параметр | Значение |
|----------|----------|
| Host | `193.233.174.4` |
| Service | tinyproxy `:8888` |
| Auth | BasicAuth `dommaster` / (см. nl_proxy.env) |
| Allow | `127.0.0.1` + `194.67.103.144` |
| Firewall | ufw: SSH 22; 8888 только с BlueTerbium |

```bash
ssh NL-Proxy   # Host в ~/.ssh/config
systemctl status tinyproxy
ufw status numbered
curl -x "$OPENAI_PROXY" -H 'Authorization: Bearer sk-test' \
  https://api.openai.com/v1/models
# ожидаем 401 = прокси и сеть живы
```

После ротации BasicAuth обновить `OPENAI_PROXY` на BlueTerbium и `systemctl restart dommaster-crm dommaster-bot`.

---

## 8. CRM: ключевые эндпоинты

| Метод | Путь | Auth | Назначение |
|-------|------|------|------------|
| GET | `/health` | нет | liveness |
| GET | `/health?deep=1` | нет | db + proxy flag |
| POST | `/ingest` | `X-Api-Token` если задан `FLASK_API_TOKEN` | публичный вход в квалификатор; ответ: CRM-блок + `action` (inbox-контракт) |
| GET | `/login` | — | UI |
| GET | `/help` | session | справка менеджера |
| GET | `/deals/` | session | список |
| GET | `/deals/<id>` | session | карточка |
| POST | `/deals/<id>/generate-kp` | session | генерация КП |
| POST | `/deals/<id>/approve-kp` | session | утверждение |
| POST | `/deals/<id>/send-kp` | session | email/telegram |
| GET | `/deals/<id>/kp.pdf` | session | скачать PDF |

Эталон полей и порог: `web_app/etalon_score.py` + `ETALON_KP_THRESHOLD`.

---

## 9. Telegram с BlueTerbium

VPS РФ часто не достучится до основного A-record `api.telegram.org`.  
В деплое закрепляется рабочий DC в `/etc/hosts` (`scripts/fix_telegram_access.sh`, встроен в `update_server.sh`).

```bash
getent hosts api.telegram.org
bash scripts/fix_telegram_access.sh
```

---

## 10. Бэкапы

- При каждом `update_server.sh`: `web_app/deals.db.backup_YYYYMMDD_HHMMSS`
- Рекомендуется периодически копировать `deals.db` и `.env` off-server

Восстановление:

```bash
systemctl stop dommaster-crm
cp web_app/deals.db.backup_… web_app/deals.db
systemctl start dommaster-crm
```

---

## 11. Troubleshooting

| Симптом | Что проверить |
|---------|----------------|
| `/health` снаружи timeout | ufw / панель REG.RU, слушает ли `0.0.0.0:5001` |
| КП без AI / таймаут | `OPENAI_PROXY`, NL tinyproxy, `health_check` openai |
| Бот молчит | `dommaster-bot`, `/etc/hosts` api.telegram.org, токен |
| Старый UI после деплоя | шаблоны на диске; hard refresh; не завис ли старый `python3 app.py` |
| Два процесса на 5001 | `ss -tlnp \| grep 5001`, убить nohup, `systemctl restart dommaster-crm` |
| logrotate skip | `chmod 755 logs`, `su root root` в конфиге |
| Email не уходит | `SMTP_*` в `.env`, логи app.log |

Быстрая диагностика:

```bash
bash /root/AI_Bot_avto_KP/scripts/health_check.sh --json
ss -tlnp | grep 5001
pgrep -af 'python3.*(app|bot)\.py'
systemctl is-enabled dommaster-crm dommaster-bot dommaster-healthcheck.timer
```

---

## 12. Связанные файлы

| Путь | Роль |
|------|------|
| `deploy/systemd/*.service` | unit-ы |
| `deploy/logrotate/dommaster` | ротация логов |
| `scripts/update_server.sh` | деплой |
| `scripts/install_systemd.sh` | установка unit + logrotate + timer |
| `scripts/health_check.sh` | мониторинг |
| `scripts/fix_telegram_access.sh` | пин Telegram DC |
| `web_app/app.py` | CRM entry + `/health` + `/help` |
| `utils/config.py` | `OPENAI_PROXY` |
| `docs/РУКОВОДСТВО_МЕНЕДЖЕРА.md` | UX для ОП |
| `docs/DOCUMENTATION.md` | обзор продукта / CLI / Flask API |
