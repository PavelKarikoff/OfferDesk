"""Подмена имени, фамилии, телефона, почты и ника Telegram токенами на время одного запроса.

Словарь «токен → исходное значение» живёт только в объекте, который вернула
anonymize. Его не пишут в файл, сессию и журнал: в журнал попадают виды
токенов и уже обезличенный текст.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_EMPTY_VALUES = {
    "не указано",
    "не указан",
    "нет",
    "неизвестно",
    "—",
    "-",
}

# Слова, которые похожи на имя по форме, но в заявке ими не являются.
_NAME_STOP = {
    "августе",
    "апреле",
    "бюджете",
    "бюджет",
    "бюджета",
    "газобетоне",
    "газобетон",
    "газобетона",
    "году",
    "года",
    "декабре",
    "день",
    "договор",
    "договоре",
    "доме",
    "дому",
    "дома",
    "завтра",
    "заказчик",
    "заказчика",
    "здравствуйте",
    "ипотеке",
    "ипотека",
    "июле",
    "июне",
    "январе",
    "кирпиче",
    "кирпич",
    "ключе",
    "клиент",
    "клиента",
    "компании",
    "компания",
    "контуре",
    "контур",
    "конце",
    "коробке",
    "крае",
    "мае",
    "марте",
    "менеджер",
    "месяц",
    "месяце",
    "миллион",
    "миллионов",
    "начале",
    "ноябре",
    "области",
    "октябре",
    "отделе",
    "письме",
    "потом",
    "предложении",
    "проекте",
    "продаже",
    "работе",
    "работу",
    "районе",
    "рублях",
    "рублей",
    "сегодня",
    "сентябре",
    "смете",
    "смету",
    "соток",
    "строительстве",
    "стройка",
    "стройки",
    "суде",
    "участка",
    "участке",
    "участки",
    "феврале",
    "этом",
    "этой",
    "этот",
}


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    kind: str
    value: str


_PRIORITY = {
    "ПОЧТА": 100,
    "ТЕЛЕФОН": 90,
    "НИК": 55,
    "ИМЯ": 50,
}

_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-zА-Яа-яЁё]{2,}"
)
_PHONE = re.compile(r"(?<!\d)(?:\+7|8|7)(?:[\s\-().]*\d){10}(?!\d)")
_PHONE_LABELED = re.compile(
    r"(?im)^[ \t]*(?:телефон|тел\.?)[ \t]*:[ \t]*(.+)$"
)
_EMAIL_LABELED = re.compile(
    r"(?im)^[ \t]*(?:почта|e-mail|email|эл\.?\s*почта)[ \t]*:[ \t]*(.+)$"
)
_NAME_LABELED = re.compile(
    r"(?im)^[ \t]*(?:имя|фамилия|фио|клиент|заказчик)[ \t]*:[ \t]*(.+)$"
)
_NAME_WORD = r"[А-ЯЁ][а-яёЁё]+"
_PATRONYMIC = rf"{_NAME_WORD}(?:ович|евич|ич|овна|евна|ична|инична)"
_SURNAME = (
    rf"{_NAME_WORD}(?:ова|ева|ёва|ина|ына|ский|цкий|ская|цкая|ских|цких|"
    rf"енко|ов|ев|ёв|ин|ын|юк|ук)"
)
# (?i:...) только у подписи. Само имя обязано начинаться с заглавной,
# иначе «Клиент интересуется…» превращается в токен.
_NAME_INTRO = re.compile(
    r"(?<!\w)(?i:меня\s+зовут|мо[её]\s+имя|на\s+имя|клиент|заказчик|"
    r"заявитель|собственник|менеджер|зовут)\s*:?\s+"
    r"([А-ЯЁA-Z][а-яёa-zA-ZЁё]+(?:\s+[А-ЯЁA-Z][а-яёa-zA-ZЁё]+){0,2})"
)
_FIO = re.compile(
    rf"\b(?:{_NAME_WORD}\s+{_PATRONYMIC}(?:\s+{_NAME_WORD})?|"
    rf"{_SURNAME}\s+{_NAME_WORD}\s+{_PATRONYMIC}|"
    rf"{_NAME_WORD}\s+{_NAME_WORD}\s+{_PATRONYMIC})\b"
)
_NAME_SURNAME = re.compile(
    rf"\b(?:{_NAME_WORD}\s+{_SURNAME}|{_SURNAME}\s+{_NAME_WORD})\b"
)
_INITIALS = re.compile(
    rf"\b(?:[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*{_SURNAME}|{_SURNAME}\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.)"
)
# Скобки вокруг ника: в текст токен встаёт вместо имени, «@» и t.me/ остаются.
_NICK = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
_TME = re.compile(
    r"(?i)(?<!\w)(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{3,31})\b"
)
_NICK_LABELED = re.compile(
    r"(?im)^[ \t]*(?:telegram|телеграмм?|телега|tg|тг)\s*[:—\-]\s*(.+)$"
)
_NICK_VALUE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")


def anonymize(text: str) -> tuple[str, dict[str, str]]:
    """Возвращает текст с токенами и словарь только для этого вызова."""
    if not text:
        return text, {}
    spans = _resolve(_find_spans(text))
    mapping: dict[str, str] = {}
    identity_to_token: dict[tuple[str, str], str] = {}
    counts: dict[str, int] = {}
    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        parts.append(_token_for(span, mapping, identity_to_token, counts))
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts), mapping


def restore(text: str, mapping: dict[str, str]) -> str:
    """Подставляет исходные значения вместо токенов текущего запроса."""
    if not text or not mapping:
        return text
    for token in sorted(mapping, key=len, reverse=True):
        original = mapping[token]
        if token in text:
            text = text.replace(token, original)
            continue
        inner = re.escape(token[1:-1])
        text = re.sub(
            rf"\[\s*{inner}\s*\]|(?<!\[)\b{inner}\b(?!\])",
            lambda _: original,
            text,
        )
    return text


def restore_data(value: Any, mapping: dict[str, str]) -> Any:
    """Возвращает копию структуры ответа, где токены снова стали исходными данными."""
    if not mapping:
        return value
    if isinstance(value, str):
        return restore(value, mapping)
    if isinstance(value, list):
        return [restore_data(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: restore_data(item, mapping) for key, item in value.items()}
    return value


def _find_spans(text: str) -> list[_Span]:
    spans: list[_Span] = []
    _add_pattern(spans, text, _EMAIL, "ПОЧТА")
    _add_pattern(spans, text, _PHONE, "ТЕЛЕФОН")
    _add_labeled(spans, text, _EMAIL_LABELED, "ПОЧТА")
    _add_labeled(spans, text, _PHONE_LABELED, "ТЕЛЕФОН")
    _add_nicks(spans, text)
    _add_names(spans, text)
    return spans


def _add_nicks(spans: list[_Span], text: str) -> None:
    handles: list[str] = []
    seen: set[str] = set()

    def remember(handle: str) -> None:
        key = handle.casefold()
        if key in seen:
            return
        seen.add(key)
        handles.append(handle)

    for match in _NICK.finditer(text):
        remember(match.group(1))
    for match in _TME.finditer(text):
        remember(match.group(1))
    for match in _NICK_LABELED.finditer(text):
        piece = _slice_group(match)
        if piece is None:
            continue
        handle = _nick_handle(piece[2])
        if handle:
            remember(handle)
    for handle in handles:
        _add_nick_literal(spans, text, handle)


def _nick_handle(value: str) -> str | None:
    stripped = value.strip()
    if not stripped or stripped.casefold() in _EMPTY_VALUES:
        return None
    link = _TME.search(stripped)
    if link:
        return link.group(1)
    token = stripped.split()[0].strip(".,;:")
    match = _NICK_VALUE.fullmatch(token)
    if match:
        return match.group(1)
    return None


def _add_nick_literal(spans: list[_Span], text: str, handle: str) -> None:
    pattern = re.compile(rf"(?<!\w){re.escape(handle)}(?!\w)", re.IGNORECASE)
    for match in pattern.finditer(text):
        spans.append(_Span(match.start(), match.end(), "НИК", match.group(0)))


def _add_names(spans: list[_Span], text: str) -> None:
    values: list[str] = []
    for match in _NAME_LABELED.finditer(text):
        piece = _slice_group(match)
        if piece is None or not _kept("ИМЯ", piece[2]) or _reject_name(piece[2]):
            continue
        start, end, value = piece
        spans.append(_Span(start, end, "ИМЯ", value))
        values.append(value)
    for pattern, group in (
        (_NAME_INTRO, 1),
        (_FIO, 0),
        (_NAME_SURNAME, 0),
        (_INITIALS, 0),
    ):
        for match in pattern.finditer(text):
            value = match.group(group).strip()
            start, end = match.start(group), match.end(group)
            if not _kept("ИМЯ", value) or _reject_name(value):
                continue
            spans.append(_Span(start, end, "ИМЯ", text[start:end]))
            values.append(value)
    seen: set[str] = set()
    for value in values:
        for word in _name_words(value):
            key = word.casefold()
            if key in seen:
                continue
            seen.add(key)
            _add_literal(spans, text, word)


def _name_words(value: str) -> list[str]:
    words = [value.strip(" \t.,;:")]
    words.extend(re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", value))
    unique: list[str] = []
    seen: set[str] = set()
    for word in words:
        key = word.casefold()
        if not word or key in seen or key in _NAME_STOP or key in _EMPTY_VALUES:
            continue
        seen.add(key)
        unique.append(word)
    return unique


def _add_literal(spans: list[_Span], text: str, word: str) -> None:
    pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)
    for match in pattern.finditer(text):
        spans.append(_Span(match.start(), match.end(), "ИМЯ", match.group(0)))


def _add_pattern(
    spans: list[_Span],
    text: str,
    pattern: re.Pattern[str],
    kind: str,
    group: int = 0,
) -> None:
    for match in pattern.finditer(text):
        value = match.group(group)
        if not _kept(kind, value):
            continue
        spans.append(_Span(match.start(group), match.end(group), kind, value))


def _add_labeled(spans: list[_Span], text: str, pattern: re.Pattern[str], kind: str) -> None:
    for match in pattern.finditer(text):
        piece = _slice_group(match)
        if piece is None or not _kept(kind, piece[2]):
            continue
        if kind == "ИМЯ" and _reject_name(piece[2]):
            continue
        start, end, value = piece
        spans.append(_Span(start, end, kind, value))


def _slice_group(match: re.Match[str], group: int = 1) -> tuple[int, int, str] | None:
    raw = match.group(group)
    if raw is None:
        return None
    start = match.start(group) + (len(raw) - len(raw.lstrip()))
    end = match.end(group) - (len(raw) - len(raw.rstrip()))
    if end <= start:
        return None
    return start, end, raw.strip()


def _kept(kind: str, value: str) -> bool:
    text = value.strip()
    if not text or text.casefold() in _EMPTY_VALUES:
        return False
    if kind == "ТЕЛЕФОН" and len(re.sub(r"\D", "", text)) < 10 and len(text) < 5:
        return False
    return len(text) >= 2


def _reject_name(value: str) -> bool:
    words = re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", value)
    if not words or len(words) > 3:
        return True
    if any(word.casefold() in _NAME_STOP for word in words):
        return True
    return any(word[:1].islower() for word in words)


def _resolve(spans: list[_Span]) -> list[_Span]:
    ordered = sorted(
        spans,
        key=lambda span: (-_PRIORITY[span.kind], -(span.end - span.start), span.start),
    )
    chosen: list[_Span] = []
    for span in ordered:
        if span.end <= span.start:
            continue
        if any(span.start < other.end and other.start < span.end for other in chosen):
            continue
        chosen.append(span)
    chosen.sort(key=lambda span: span.start)
    return chosen


def _token_for(
    span: _Span,
    mapping: dict[str, str],
    identity_to_token: dict[tuple[str, str], str],
    counts: dict[str, int],
) -> str:
    identity = _identity(span.kind, span.value)
    existing = identity_to_token.get(identity)
    if existing:
        return existing
    counts[span.kind] = counts.get(span.kind, 0) + 1
    token = f"[{span.kind}_{counts[span.kind]}]"
    identity_to_token[identity] = token
    mapping[token] = span.value.strip()
    return token


def _identity(kind: str, value: str) -> tuple[str, str]:
    compact = " ".join(value.split())
    if kind == "ТЕЛЕФОН":
        digits = re.sub(r"\D", "", compact)
        if digits:
            return kind, digits[-10:] if len(digits) >= 10 else digits
    if kind == "ПОЧТА":
        return kind, compact.casefold()
    if kind == "НИК":
        return kind, compact.lstrip("@").casefold()
    return kind, compact.casefold()
