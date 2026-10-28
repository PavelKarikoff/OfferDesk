"""Выгрузка сделки OfferDesk в Битрикс24 через входящий вебхук.

В CRM уходят заявка (лид), транскрибация звонка и файл КП.
Адрес вебхука читается из B24_URL или BITRIX_URL и в журнал и текст ошибок не попадает.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
SOURCE_NOTE = "OfferDesk"
_PATRONYMIC_ENDINGS = ("ович", "евич", "овна", "евна", "ична", "инична", "ич")
_MAX_COMMENT = 12_000
_MAX_FILE_BYTES = 6_000_000

Transport = Callable[[str, dict[str, Any]], Any]


class BitrixError(Exception):
    """Ошибка Битрикс24, которую можно показать менеджеру."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class CrmPublishResult:
    """Куда легла заявка."""

    entity_type: str
    entity_label: str
    entity_id: int
    lead_id: int
    task_id: int | None = None
    warning: str | None = None


def bitrix_configured() -> bool:
    """True, если в окружении задан адрес входящего вебхука."""
    _load_env()
    return bool(_raw_url())


def publish_deal(
    deal: dict[str, Any],
    *,
    audit: dict[str, Any] | None = None,
    kp_meta: dict[str, Any] | None = None,
    kp_pdf: Path | None = None,
    notify: bool = False,
    transport: Transport | None = None,
    user_id: int | None = None,
) -> CrmPublishResult:
    """Создаёт или обновляет лид и кладёт транскрибацию и КП в таймлайн.

    transport и user_id нужны тестам: без них адрес берётся из B24_URL.
    Повторный вызов с bitrix_lead_id обновляет тот же лид, а не заводит второй.
    """
    if transport is None:
        base_url, webhook_user = _webhook()

        def transport(method: str, payload: dict[str, Any], _base: str = base_url) -> Any:
            return _call(_base, method, payload)

        user_id = webhook_user
    if user_id is None:
        raise BitrixError("Не задан сотрудник Битрикс24 для задачи и уведомления.")

    audit = audit or {}
    fields = _lead_fields(deal, audit)
    lead_id, payload = _upsert_lead(transport, fields, _as_int(deal.get("bitrix_lead_id")))
    stage = _stage(transport, lead_id, payload)

    entity_type = "lead"
    entity_id = lead_id
    entity_label = "лид"
    binding = f"L_{lead_id}"
    warnings: list[str] = []

    if stage == "CONVERTED" or (deal.get("bitrix_entity_type") == "deal" and _as_int(deal.get("bitrix_entity_id"))):
        stored_deal = (
            _as_int(deal.get("bitrix_entity_id"))
            if deal.get("bitrix_entity_type") == "deal"
            else None
        )
        deal_id = stored_deal or _find_deal(transport, lead_id)
        if deal_id is None:
            warnings.append(
                "Лид конвертировался в сделку, но саму сделку найти не удалось. "
                "Комментарий добавлен к лиду."
            )
        else:
            entity_type = "deal"
            entity_id = deal_id
            entity_label = "сделка"
            binding = f"D_{deal_id}"

    comment, files = _application_comment(deal, audit)
    _add_comment(
        transport,
        entity_id,
        entity_type,
        comment,
        files,
        warnings,
        "Карточка заявки и транскрибация в таймлайне не сохранились",
    )

    if kp_meta or kp_pdf:
        kp_comment, kp_files, kp_note = _kp_comment(deal, kp_meta, kp_pdf)
        if kp_note:
            warnings.append(kp_note)
        _add_comment(
            transport,
            entity_id,
            entity_type,
            kp_comment,
            kp_files,
            warnings,
            "Коммерческое предложение в таймлайне не сохранилось",
        )

    reasons = _reasons(audit)
    task_id = _as_int(deal.get("bitrix_task_id"))
    if reasons and task_id is None:
        holder: dict[str, int] = {}

        def _create_task() -> None:
            created = transport(
                "tasks.task.add",
                {
                    "fields": {
                        "TITLE": _clip(f"Разобрать эскалацию: {deal.get('client_name') or 'заявка'}", 240),
                        "DESCRIPTION": _task_description(deal, audit),
                        "RESPONSIBLE_ID": user_id,
                        "UF_CRM_TASK": [binding],
                    }
                },
            )
            parsed = _task_id(created)
            if parsed is not None:
                holder["id"] = parsed

        if _step(warnings, "Задача сотруднику не создана", _create_task):
            task_id = holder.get("id", task_id)

    if notify or not deal.get("bitrix_lead_id"):
        grade = str(audit.get("lead_grade") or "—")
        title = fields["title"]
        _step(
            warnings,
            "Уведомление сотруднику не отправлено",
            lambda: transport(
                "im.notify.system.add",
                {
                    "USER_ID": user_id,
                    "MESSAGE": (
                        f"Заявка OfferDesk: {title}. Лид {grade}. "
                        f"{entity_label[:1].upper()}{entity_label[1:]} №{entity_id}."
                    ),
                },
            ),
        )

    warning = " ".join(warnings) if warnings else None
    return CrmPublishResult(
        entity_type=entity_type,
        entity_label=entity_label,
        entity_id=entity_id,
        lead_id=lead_id,
        task_id=task_id,
        warning=warning,
    )


def _load_env() -> None:
    if ENV_PATH.is_file():
        load_dotenv(ENV_PATH, override=False)


def _raw_url() -> str:
    """B24_URL — имя из кода; BITRIX_URL — то, что уже записано в .env."""
    for key in ("B24_URL", "BITRIX_URL"):
        value = os.getenv(key, "").strip().strip('"').strip("'")
        if value:
            return value
    return ""


def _webhook() -> tuple[str, int]:
    _load_env()
    raw = _raw_url()
    if not raw:
        raise BitrixError(
            "В файле .env не заполнен B24_URL или BITRIX_URL. Сделка в OfferDesk сохранена, "
            "в Битрикс24 заявка не отправлена."
        )
    parsed = urlparse(raw)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or "rest" not in parts
        or len(parts) < parts.index("rest") + 3
    ):
        raise BitrixError(
            "B24_URL в файле .env не похож на входящий вебхук Битрикс24. "
            "Сделка в OfferDesk сохранена, в CRM заявка не отправлена."
        )
    user_index = parts.index("rest") + 1
    user_raw = parts[user_index]
    token = parts[user_index + 1]
    if not user_raw.isdigit() or not token:
        raise BitrixError(
            "B24_URL в файле .env не похож на входящий вебхук Битрикс24. "
            "Сделка в OfferDesk сохранена, в CRM заявка не отправлена."
        )
    base = f"https://{parsed.netloc}/rest/{user_raw}/{token}/"
    return base, int(user_raw)


def _lead_fields(deal: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    name = str(deal.get("client_name") or "").strip() or "без имени"
    grade = str(audit.get("lead_grade") or "").strip()
    title = f"Заявка: {name}" + (f" — лид {grade}" if grade else "")
    fields: dict[str, Any] = {
        "title": _clip(title, 240),
        "sourceDescription": SOURCE_NOTE,
    }
    fm: list[dict[str, str]] = []
    phone = str(deal.get("client_phone") or "").strip()
    email = str(deal.get("client_email") or "").strip()
    if phone:
        fm.append({"typeId": "PHONE", "valueType": "WORK", "value": phone})
    if email:
        fm.append({"typeId": "EMAIL", "valueType": "WORK", "value": email})
    if fm:
        fields["fm"] = fm
    fields.update(_split_name(name if name != "без имени" else ""))
    return fields


def _split_name(full_name: str) -> dict[str, str]:
    parts = full_name.split()
    if not parts:
        return {}
    if len(parts) == 1:
        return {"name": parts[0]}
    if len(parts) == 2:
        return {"name": parts[0], "lastName": parts[1]}
    if _is_patronymic(parts[2]) and not _is_patronymic(parts[1]):
        return {"lastName": parts[0], "name": parts[1], "secondName": " ".join(parts[2:])}
    return {"name": parts[0], "secondName": parts[1], "lastName": " ".join(parts[2:])}


def _is_patronymic(word: str) -> bool:
    folded = word.casefold()
    return len(folded) > 4 and folded.endswith(_PATRONYMIC_ENDINGS)


def _application_comment(deal: dict[str, Any], audit: dict[str, Any]) -> tuple[str, list[list[str]]]:
    missing = deal.get("etalon_missing") or []
    if not isinstance(missing, list) or not missing:
        missing_text = "нет"
    else:
        missing_text = ", ".join(str(item) for item in missing if str(item).strip()) or "нет"
    reasons = _reasons(audit)
    lines = [
        f"OfferDesk · сделка №{deal.get('id') or '—'}",
        f"Статус: {deal.get('status_label') or deal.get('status') or '—'}",
        f"Лид: {audit.get('lead_grade') or '—'} ({audit.get('lead_score') or '—'})",
        f"Эталон: {deal.get('etalon_score') if deal.get('etalon_score') is not None else '—'}%",
        f"Источник разбора: {deal.get('extraction_source') or audit.get('audit_source') or '—'}",
        f"Эскалация: {('да → ' + str(audit.get('escalation_target') or 'менеджер') + ' (' + ', '.join(reasons) + ')') if reasons else 'нет'}",
        f"Телефон: {deal.get('client_phone') or 'не указан'}",
        f"Email: {deal.get('client_email') or 'не указан'}",
        f"Telegram: {deal.get('client_telegram') or 'не указан'}",
        f"Участок: {deal.get('plot') or 'не указан'}",
        f"Площадь: {deal.get('area') or 'не указана'}",
        f"Материал: {deal.get('material') or 'не указан'}",
        f"Бюджет: {deal.get('budget') or 'не указан'}",
        f"Срок: {deal.get('timeline') or 'не указан'}",
        f"Финансирование: {deal.get('funding_source') or 'не указано'}",
        f"Проект каталога: {deal.get('catalog_project') or 'не указан'}",
        f"Не хватает: {missing_text}",
        f"Заметки: {str(deal.get('notes') or '').strip() or 'нет'}",
    ]
    transcript = str(deal.get("transcript") or "").strip()
    body = "\n".join(lines) + "\n\nТранскрибация звонка:\n" + (transcript or "не указана")
    files: list[list[str]] = []
    attached = _text_file(f"offerdesk_deal{deal.get('id') or 'x'}_transcript.txt", transcript)
    if transcript and attached is None:
        body += "\n\nФайл транскрибации больше лимита вложения и в комментарий не приложен."
    elif attached is not None:
        files.append(attached)
    return _clip(body, _MAX_COMMENT), files


def _kp_comment(
    deal: dict[str, Any],
    kp_meta: dict[str, Any] | None,
    kp_pdf: Path | None,
) -> tuple[str, list[list[str]], str | None]:
    meta = kp_meta or {}
    watermark = str(meta.get("watermark") or "")
    state = "утверждено" if watermark == "approved" else "черновик"
    total = meta.get("total_fmt") or meta.get("grand_total_fmt") or "—"
    lines = [
        "Коммерческое предложение OfferDesk",
        f"Сделка №{deal.get('id') or '—'}",
        f"Номер: {meta.get('kp_number') or '—'}",
        f"Итого: {total}",
        f"Статус файла: {state}",
    ]
    files: list[list[str]] = []
    note = None
    if kp_pdf is not None and kp_pdf.is_file():
        raw = kp_pdf.read_bytes()
        if len(raw) > _MAX_FILE_BYTES:
            note = "Файл КП больше лимита вложения и в Битрикс24 не приложен."
            lines.append(note)
        else:
            name = (
                f"KP_timber_deal{deal.get('id')}.pdf"
                if meta.get("kp_kind") == "timber"
                else f"KP_DomMaster_deal{deal.get('id')}.pdf"
            )
            files.append([name, base64.b64encode(raw).decode("ascii")])
            lines.append("PDF приложен к комментарию.")
    else:
        note = "Файл КП на диске не найден. В таймлайн записана только карточка предложения."
        lines.append(note)
    return _clip("\n".join(lines), _MAX_COMMENT), files, note


def _task_description(deal: dict[str, Any], audit: dict[str, Any]) -> str:
    reasons = ", ".join(_reasons(audit)) or "нужен человек"
    target = str(audit.get("escalation_target") or "менеджер")
    return _clip(
        f"Сделка OfferDesk №{deal.get('id')}. Кому: {target}. Причины: {reasons}.",
        4000,
    )


def _reasons(audit: dict[str, Any]) -> list[str]:
    raw = audit.get("escalation_reasons") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _upsert_lead(
    transport: Transport,
    fields: dict[str, Any],
    lead_id: int | None,
) -> tuple[int, Any]:
    if lead_id:
        try:
            updated = transport(
                "crm.item.update",
                {"entityTypeId": 1, "id": lead_id, "fields": fields},
            )
        except BitrixError:
            updated = None
        else:
            return lead_id, updated
        try:
            fetched = transport("crm.item.get", {"entityTypeId": 1, "id": lead_id})
        except BitrixError:
            fetched = None
        if fetched is not None:
            return lead_id, fetched
    created = transport("crm.item.add", {"entityTypeId": 1, "fields": fields})
    return _item_id(created), created


def _stage(transport: Transport, lead_id: int, payload: Any) -> str:
    stage = _stage_of(payload)
    if stage:
        return stage
    fetched = transport("crm.item.get", {"entityTypeId": 1, "id": lead_id})
    return _stage_of(fetched)


def _stage_of(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    item = payload.get("item")
    if not isinstance(item, dict):
        return ""
    return str(item.get("stageId") or item.get("statusId") or "")


def _find_deal(transport: Transport, lead_id: int) -> int | None:
    try:
        listed = transport(
            "crm.item.list",
            {"entityTypeId": 2, "select": ["id"], "filter": {"leadId": lead_id}},
        )
    except BitrixError:
        listed = None
    deal_id = _first_id(listed, "items", "id")
    if deal_id is not None:
        return deal_id
    try:
        deals = transport(
            "crm.deal.list",
            {"filter": {"LEAD_ID": lead_id}, "select": ["ID"]},
        )
    except BitrixError:
        return None
    return _first_id(deals, None, "ID")


def _first_id(payload: Any, list_key: str | None, id_key: str) -> int | None:
    rows: Any
    if list_key is None:
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get(list_key)
    else:
        rows = None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    raw = rows[0].get(id_key) or rows[0].get(id_key.lower()) or rows[0].get("id")
    if raw in (None, ""):
        return None
    return int(raw)


def _item_id(payload: Any) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("item"), dict):
        raise BitrixError("Битрикс24 создал лид, но не вернул его номер.")
    raw = payload["item"].get("id")
    if raw in (None, ""):
        raise BitrixError("Битрикс24 создал лид, но не вернул его номер.")
    return int(raw)


def _task_id(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
    raw = task.get("id") or task.get("ID")
    if raw in (None, ""):
        return None
    return int(raw)


def _add_comment(
    transport: Transport,
    entity_id: int,
    entity_type: str,
    comment: str,
    files: list[list[str]],
    warnings: list[str],
    label: str,
) -> None:
    fields = {
        "ENTITY_ID": entity_id,
        "ENTITY_TYPE": entity_type,
        "COMMENT": comment,
    }
    if files:
        try:
            transport("crm.timeline.comment.add", {"fields": {**fields, "FILES": files}})
            return
        except BitrixError as exc:
            warnings.append(f"Файл к комментарию не приложен. {exc.message}")
    if not _step(
        warnings,
        label,
        lambda: transport("crm.timeline.comment.add", {"fields": fields}),
    ):
        return


def _step(warnings: list[str], label: str, action: Any) -> bool:
    try:
        action()
    except BitrixError as exc:
        warnings.append(f"{label}. {exc.message}")
        return False
    return True


def _text_file(name: str, text: str) -> list[str] | None:
    if not text:
        return None
    raw = text.encode("utf-8")
    if len(raw) > _MAX_FILE_BYTES:
        return None
    return [name, base64.b64encode(raw).decode("ascii")]


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _call(base_url: str, method: str, payload: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        f"{base_url}{method}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return _parse(raw, base_url, http_failed=True)
    except urllib.error.URLError:
        raise BitrixError(
            "Не удалось связаться с Битрикс24. Проверьте интернет и повторите выгрузку."
        )
    except TimeoutError:
        raise BitrixError("Битрикс24 не ответил вовремя. Повторите выгрузку через минуту.")
    return _parse(raw, base_url, http_failed=False)


def _parse(raw: bytes, base_url: str, *, http_failed: bool) -> Any:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BitrixError("Битрикс24 вернул ответ в неожиданном виде.")
    if not isinstance(data, dict):
        raise BitrixError("Битрикс24 вернул ответ в неожиданном виде.")
    error = data.get("error")
    if error or http_failed:
        raise BitrixError(
            _error_message(str(error or ""), str(data.get("error_description") or ""), base_url)
        )
    return data.get("result")


def _error_message(code: str, description: str, base_url: str) -> str:
    known = {
        "expired_token": "Срок вебхука Битрикс24 истёк. Обновите B24_URL в файле .env.",
        "INVALID_CREDENTIALS": "Битрикс24 не принял вебхук. Проверьте B24_URL в файле .env.",
        "NO_AUTH_FOUND": "Битрикс24 не принял вебхук. Проверьте B24_URL в файле .env.",
        "insufficient_scope": "У вебхука не хватает прав. Нужны CRM, задачи и уведомления.",
        "ACCESS_DENIED": "У сотрудника, создавшего вебхук, не хватает прав на это действие в CRM.",
        "QUERY_LIMIT_EXCEEDED": "Битрикс24 ограничил частоту запросов. Подождите минуту и повторите.",
        "OPERATION_TIME_LIMIT": "Битрикс24 не успел обработать запрос. Повторите через минуту.",
    }
    if code in known:
        return known[code]
    safe = _redact(description, base_url)
    if safe and "http" not in safe.casefold() and len(safe) <= 180:
        return f"Битрикс24 вернул ошибку: {safe}"
    return "Битрикс24 вернул ошибку. Сделка в OfferDesk сохранена."


def _redact(text: str, base_url: str) -> str:
    token = [part for part in urlparse(base_url).path.split("/") if part]
    secret = token[-1] if token else ""
    cleaned = text.replace(base_url, "")
    if secret:
        cleaned = cleaned.replace(secret, "")
    return " ".join(cleaned.split())


def _clip(text: str, limit: int) -> str:
    compact = " ".join(text.split()) if "\n" not in text else text.strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"
