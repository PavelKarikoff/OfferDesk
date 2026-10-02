# Changelog

Все значимые изменения проекта **OfferDesk** (рабочее место ОП «Дом-Мастер»).

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [SemVer](https://semver.org/lang/ru/).

## [1.1.0] — 2026-09-29

AI-квалификатор лидов для ИЖС: LLM-слой поверх CRM
(заявка -> JSON-схема -> валидация -> эскалация -> lead scoring -> сделка).

### Добавлено

- `extraction/` — LLM-извлечение заявки в JSON-схему (client / object / deal / sales_signals / confidence / missing_fields), промпты `extractor_v1` / `extractor_v2`, regex-fallback при недоступности LLM.
- `extraction/validation/` — бизнес-правила (бюджет >= 3 млн, площадь 50–500 м², цена/м² >= 60k, форматы) и эскалация: суд/юрид -> юрист + руководитель; негатив + низкий etalon_score -> руководитель.
- `extraction/scoring/` — lead scoring A/B/C по 6 факторам (A >= 0.7, B >= 0.4, C < 0.4).
- `extraction/audit/` — `audit_log` и `get_last_audit` (deal_id, source, confidence, etalon_score, lead_grade, escalation).
- Golden set (15 кейсов) + baseline regex, скрипты `run_golden_set.py` / `compare_runs.py`, отчёты в `reports/`.
- Документация: `docs/ARCHITECTURE.md`, `docs/ROI.md`, `docs/OFFER.md`, `docs/KNOWN_ISSUES.md`.
- +43 новых теста (итого 88 зелёных).

### Изменено

- CRM: разбор протокола через `extract()` вместо `parse_transcript_local`; в карточке сделки — грейд лида и эскалация.

### Исправлено

- Эскалация: убран триггер `low_confidence`, `legal_risk` определяется по сырому тексту.

## [1.0.0] — 2026-08-13

Первый продакшен-релиз: Telegram-бот + веб-CRM на VPS (BlueTerbium),
генерация КП тёплого контура, health-мониторинг.

### Добавлено

- Веб-CRM (`web_app/`): сделки, дашборд, эталон заполнения, генерация / утверждение / отправка КП.
- Проверка протокола по RAG-эталону (`knowledge_base/etalon_protocol.md`), порог КП (по умолчанию 80%).
- Страница недостающих данных со скриптом уточняющих вопросов.
- КП этапа «Стройка»: WeasyPrint, 41 000 ₽/м², водяной знак, стандарты и комплектации в RAG.
- Отправка КП по email (SMTP) и в Telegram; outbox, если API Telegram недоступен с VPS.
- Пайплайн статусов, таймлайн (`action_log`), напоминания по сделкам без действий > 3 дней.
- Вкладки карточки сделки (Основное / КП / История / Файлы) и аналитика на дашборде.
- Демо-протоколы, admin API, статистика над таблицей сделок.
- HTTP `/triage` — классификация обращений.
- Production: systemd (`dommaster-crm` / `dommaster-bot` / health timer), logrotate, NL-прокси OpenAI.
- Скрипты: `update_server.sh`, `health_check.sh`, `e2e_crm.sh`, `load_test_crm.sh`, `install_systemd.sh`.
- Документация: руководство менеджера, OPS, OpenAPI 3.1, Docker Hub.

### Исправлено

- Генерация КП через прокси: таймаут OpenAI / Waitress, повтор без AI при обрыве сети.
- Доступ VPS к Telegram API: pin рабочего DC в `/etc/hosts`.
- Стабильный pin Telegram в `update_server.sh` (без вложенного heredoc).
- График на дашборде: переменные передаются из view, а не из `{% set %}` внутри content.
- Парсер телефонов: `+7` / `8` / компактные номера.

### Безопасность и ops

- OpenAI только через NL-прокси (`OPENAI_PROXY`); Telegram — отдельным каналом.
- Health-check каждые 5 минут + алерт в Telegram при FAIL.
- Бэкап `web_app/deals.db` при каждом деплое.

## [Unreleased]

### Добавлено

- Шаблон КП домов из клееного бруса: база знаний `knowledge_base/timber/`, HTML `templates/kp_timber_template.html` (наследует `base_kp.html`), генератор `utils/timber_kp.py`. Итог = смета по этапам + 5% накладных, без ставки 75 000 ₽/м² «Дом-Мастер».
- КП клееного бруса: логотип в шапке и ряд соцсетей (VK, Telegram, WhatsApp, Дзен, YouTube) в блоке «Контакты». Шаблон «Дом-Мастер» не затронут.
- CRM: если в протоколе клееный брус, «Сгенерировать КП» собирает timber-шаблон, а не газобетон × 75 000 ₽/м². Демо-протокол: `knowledge_base/timber/demo_protocol_19_08.txt`.
- Эталон для клееного бруса: обязательное поле **проект каталога**. Без проекта КП по брусу не генерируется.

### Изменено

- Продукт закреплён под именем **OfferDesk**. «Дом-Мастер» — заказчик (бренд в КП и письмах). Telegram — канал, не название системы.
- Репозиторий GitHub: [PavelKarikoff/OfferDesk](https://github.com/PavelKarikoff/OfferDesk) (бывший `AI_Bot_avto_KP`).
- Ставка тёплого контура: **75 000 ₽/м²** (было 41 000 ₽/м²). CRM пересчитывает `tk_cost` по площади при открытии БД.
- КП п.5: коммерческие условия только связным текстом (отклоняем JSON/словарь от модели).
- КП п.6 и база знаний: уровень «холодный контур» убран. Тёплый контур = сумма КП (площадь × 75 000 ₽/м²). White Box — ориентир ~ + 2 500 000 ₽ после ТК.

### Планируется

- Актуальные прайсы из БД / 1С.
- ТЗ для внешнего инженера.
- Выгрузка в внешнюю CRM.

[1.1.0]: https://github.com/PavelKarikoff/OfferDesk/releases/tag/v1.1.0
[1.0.0]: https://github.com/PavelKarikoff/OfferDesk/releases/tag/v1.0.0
