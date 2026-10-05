# Персональный помощник «от текста до действия»

Соответствует варианту 1 итогового проекта Zerocoder «Вайбкодинг Профессия».

## Что это

Веб-сервис, который превращает поток входящего текста (задачи, идеи,
поручения, «надо не забыть») в **структурированные задачи и заметки**
с честной отметкой случаев, когда нужна ручная проверка.

Пользователь вставляет текст → LLM структурирует его в строгий JSON →
запись попадает в веб-панель → пользователь работает с витриной.

## Три сценария пользователя (по ТЗ)

### Сценарий 1. Входящие → задача/заметка

1. Пользователь открывает раздел **Входящие** (`/capture`).
2. Вставляет текст («Позвонить Сергею завтра в 10:00»).
3. Нажимает **Создать**.
4. Система разбирает текст через LLM и создаёт item типа `task`.
5. В таблице «Последние 5» видно: тип, приоритет, срок, источник, метка проверки.

### Сценарий 2. Витрина задач

1. Открывает раздел **Задачи** (`/tasks`).
2. Фильтрует «В работе» / «Выполнено» / «Требует проверки» / «Все».
3. Нажимает **Выполнено** — статус меняется на `done`.

### Сценарий 3. «Требует проверки»

1. На item с `needs_review=true` — красный бейдж «требует проверки».
2. Пользователь нажимает **Править** → попадает на `/tasks/<id>/review`.
3. Правит заголовок и приоритет, сохраняет.
4. Item снимается с проверки, `review_reason` очищается.

## Точки доступа (API)

| Метод | Endpoint | Что делает |
|---|---|---|
| `POST` | `/capture` | текст → item + запись в `audit_runs` |
| `GET` | `/capture` | форма ввода + последние 5 |
| `GET` | `/tasks` | витрина с фильтром `?status=open\|done\|needs_review\|all` |
| `POST` | `/tasks/<id>/done` | статус `done` |
| `GET`/`POST` | `/tasks/<id>/review` | правка title/priority + снятие needs_review |
| `GET` | `/journal` | журнал `audit_runs` |

Токен: `X-Api-Token` / `X-API-Token` / `Authorization: Bearer`
(если `FLASK_API_TOKEN` задан в `.env`).

## Когда нужна ручная проверка (needs_review=true)

- `confidence="low"` (< 0.5) — модель не уверена.
- JSON не разобрался или не проходит Pydantic-валидацию → `INVALID_JSON` / `SCHEMA_MISMATCH`.
- Вход противоречивый/слишком общий («сделай важное», «потом разберусь») → `AMBIGUOUS_INPUT`.

При `needs_review=true`:

- item получает **красный бейдж** в веб-панели;
- причина пишется в `audit_runs.error`;
- item попадает в очередь «Требует проверки»;
- в `audit_runs.status` = `needs_review`.

## База данных (SQLite)

Таблицы:

- **`items`** — задачи и заметки (item_type, title, body, priority, due_date, status, needs_review, review_reason, source, confidence, created_at, updated_at).
- **`audit_runs`** — журнал обработок (item_id, ts, source, status, input_text, result_json, error, confidence, needs_review).

## Веб-панель (3 раздела)

| Раздел | URL | Что показывает |
|---|---|---|
| **Входящие** | `/capture` | форма ввода + последние 5 items |
| **Задачи** | `/tasks` | витрина с фильтрами и кнопкой «Выполнено» |
| **Журнал** | `/journal` | таблица audit_runs с подсветкой needs_review |

### Скриншоты

| Раздел | Файл |
|---|---|
| Входящие — форма + последние 5 | [`docs/screenshots/tasks/capture.png`](screenshots/tasks/capture.png) |
| Задачи — витрина с needs_review | [`docs/screenshots/tasks/list.png`](screenshots/tasks/list.png) |
| Журнал — audit_runs | [`docs/screenshots/tasks/journal.png`](screenshots/tasks/journal.png) |
| Правка item | [`docs/screenshots/tasks/review.png`](screenshots/tasks/review.png) |

Метка «требует проверки» — красный `badge text-bg-danger` на элементах
с `needs_review=true`.

## Тестовые входы (10)

Лежат в `knowledge_base/item_tests/`:

- **4 задачи** (`task_01.txt` … `task_04.txt`);
- **4 заметки** (`note_01.txt` … `note_04.txt`);
- **1 шумный** (`noise_01.txt`) — эмоциональный, без сути;
- **1 плохой** (`bad_01.txt` — «сделай важное») → **needs_review=true**.

Прогон:

```bash
python3 scripts/run_item_tests.py
cat reports/item_tests.md
```

Результат прогона (10 кейсов):

- задачи: `task_01` → «Позвонить Сергею — смета по дому», high, 2026-10-06;
- заметки: `note_01` → «Идея: AI-квалификатор заявок ИЖС», note, confidence 0.90;
- шум: `noise_01` → needs_review=true, `AMBIGUOUS_INPUT`, confidence 0.30;
- плохой: `bad_01` → source=heuristic, needs_review=true, `AMBIGUOUS_INPUT`, confidence 0.00.

Итог: 10 входов, источники llm=9 + heuristic=1, needs_review=2.

## Критерии приёмки — соответствие

| Критерий ТЗ | Реализация | Доказательство |
|---|---|---|
| `POST /capture` создаёт task/note и возвращает корректный `item_type` | `routes_tasks.py:capture()` | curl + `/capture` UI + `reports/item_tests.md` |
| `GET /tasks` показывает созданные задачи | `routes_tasks.py:tasks_list()` | `/tasks` скриншот |
| `POST /tasks/{id}/done` реально меняет статус | `routes_tasks.py:mark_done()` | curl + скриншот «Выполнено» |
| Минимум 1 «плохой» вход → `needs_review=true` + виден в веб-панели | `item_pipeline.py:is_ambiguous()` | `bad_01.txt` в `/tasks?status=needs_review` |
| В `audit_runs` видно 10+ запусков и причины ручной проверки | таблица `audit_runs` | sqlite3 + `/journal` |

## Стек

- Backend: Flask + SQLite.
- LLM: OpenAI (gpt-4o-mini) через `utils.chat_json`, строгий JSON.
- Схема: Pydantic (`ItemExtraction`).
- Fallback: regex/heuristic при недоступности LLM.
- UI: Bootstrap 5.3.3 + Bootstrap Icons.

## Прод

**URL:** http://194.67.103.144:5001

- LLM через NL-прокси `5.129.213.88:8888` (tinyproxy)
- `POST /capture` → `source=llm`, `confidence 0.9+`
- `POST /ingest` → `source=llm`, `etalon_score 86%+`, лид A
- Fallback работает при недоступности LLM
- Инфраструктура: BlueTerbium (194.67.103.144) → NL-прокси → OpenAI
