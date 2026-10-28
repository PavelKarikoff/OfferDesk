"""Запрос к GigaChat. Ключ читается только из .env и в браузер не попадает.

Схема та же, что в проекте «Интенсив_ИИ_Консалт»: Authorization Key,
scope физического лица и отключённая проверка сертификата Минцифры,
чтобы запросы шли без VPN и без корпоративного прокси.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from gigachat import GigaChat
from gigachat.exceptions import (
    AuthenticationError,
    BadRequestError,
    ForbiddenError,
    GigaChatException,
    NotFoundError,
    RateLimitError,
    RequestEntityTooLargeError,
)
from gigachat.models import Chat, Messages, MessagesRole

DEFAULT_SCOPE = "GIGACHAT_API_PERS"
DEFAULT_MODEL = "GigaChat-2"


class GigaChatConfigError(ValueError):
    """Ключ или настройки в .env не позволяют отправить запрос."""


def complete_chat(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.2,
) -> str:
    """Один chat-запрос. Возвращает текст ответа модели."""
    request = Chat(
        model=_setting("GIGACHAT_MODEL", DEFAULT_MODEL),
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=system_prompt),
            Messages(
                role=MessagesRole.USER,
                content=(
                    f"{user_prompt.rstrip()}\n\n"
                    "Верни только один JSON-объект без пояснений и без markdown."
                ),
            ),
        ],
        temperature=temperature,
        max_tokens=_max_tokens(),
    )
    try:
        with _build_client() as client:
            response = client.chat(request)
    except AuthenticationError as exc:
        raise GigaChatConfigError(
            "GigaChat не принял ключ доступа. Проверьте GIGACHAT_CREDENTIALS "
            "и GIGACHAT_SCOPE в файле .env."
        ) from exc
    except ForbiddenError as exc:
        raise GigaChatConfigError(
            "Доступ к GigaChat запрещён. Проверьте GIGACHAT_SCOPE в файле .env: "
            "для физического лица обычно GIGACHAT_API_PERS."
        ) from exc
    except NotFoundError as exc:
        raise GigaChatConfigError(
            "Указанная модель не найдена. Проверьте GIGACHAT_MODEL. "
            "Частый вариант — GigaChat-2."
        ) from exc
    except RateLimitError as exc:
        raise RuntimeError(
            "GigaChat получил слишком много запросов. Подождите минуту и повторите."
        ) from exc
    except RequestEntityTooLargeError as exc:
        raise RuntimeError("Текст запроса слишком длинный для GigaChat.") from exc
    except BadRequestError as exc:
        raise GigaChatConfigError(
            "GigaChat не принял запрос. Проверьте GIGACHAT_SCOPE "
            "(для физического лица — GIGACHAT_API_PERS) и GIGACHAT_MODEL (часто GigaChat-2)."
        ) from exc
    except GigaChatException as exc:
        if _is_ssl_error(exc):
            raise GigaChatConfigError(_ssl_message()) from exc
        raise RuntimeError(
            "Сервис GigaChat вернул ошибку. Проверьте интернет и файл .env."
        ) from exc
    except GigaChatConfigError:
        raise
    except Exception as exc:
        if _is_ssl_error(exc):
            raise GigaChatConfigError(_ssl_message()) from exc
        if _is_timeout(exc):
            raise TimeoutError("GigaChat не ответил вовремя.") from exc
        if _is_connection_error(exc):
            raise ConnectionError("Не удалось связаться с GigaChat.") from exc
        raise
    return _response_text(response)


def _build_client() -> GigaChat:
    credentials = os.getenv("GIGACHAT_CREDENTIALS", "").strip()
    if not credentials:
        raise GigaChatConfigError(
            "Не найден ключ GigaChat. Впишите Authorization Key в GIGACHAT_CREDENTIALS."
        )

    ca_bundle = os.getenv("GIGACHAT_CA_BUNDLE_FILE", "").strip()
    if ca_bundle and not Path(ca_bundle).is_file():
        raise GigaChatConfigError(
            "Файл сертификата из GIGACHAT_CA_BUNDLE_FILE не найден. "
            "Проверьте путь или оставьте строку пустой, а GIGACHAT_VERIFY_SSL_CERTS=false."
        )

    return GigaChat(
        credentials=credentials,
        scope=_setting("GIGACHAT_SCOPE", DEFAULT_SCOPE),
        model=_setting("GIGACHAT_MODEL", DEFAULT_MODEL),
        timeout=_timeout(),
        verify_ssl_certs=_verify_ssl(ca_bundle),
        ca_bundle_file=ca_bundle or None,
    )


def _setting(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def _timeout() -> float:
    raw = os.getenv("GIGACHAT_TIMEOUT", "").strip()
    if not raw:
        return 90.0
    try:
        timeout = float(raw)
    except ValueError:
        return 90.0
    return timeout if timeout > 0 else 90.0


def _max_tokens() -> int:
    raw = os.getenv("GIGACHAT_MAX_TOKENS", "").strip()
    if not raw:
        return 4000
    try:
        value = int(raw)
    except ValueError:
        return 4000
    return value if value > 0 else 4000


def _verify_ssl(ca_bundle: str) -> bool:
    raw = os.getenv("GIGACHAT_VERIFY_SSL_CERTS", "").strip().lower()
    if raw in {"1", "true", "yes", "y", "да"}:
        return True
    if raw in {"0", "false", "no", "n", "нет"}:
        return False
    # Пустое значение, как в интенсиве: без сертификата Минцифры проверка выключена.
    return bool(ca_bundle)


def _ssl_message() -> str:
    return (
        "Не удалось установить защищённое соединение с GigaChat. "
        "В файле .env оставьте GIGACHAT_VERIFY_SSL_CERTS=false "
        "или укажите путь к корневому сертификату в GIGACHAT_CA_BUNDLE_FILE."
    )


def _response_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError("В ответе GigaChat нет текста.") from exc
    text = str(content or "").strip()
    if not text:
        raise ValueError("Пустой ответ GigaChat.")
    return text


def _is_ssl_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = f"{type(current).__name__} {current}".lower()
        if "certificate" in text or "ssl" in text or "tls" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def _is_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name or "timeout" in str(exc).lower()


def _is_connection_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    return "connect" in name or "network" in name
