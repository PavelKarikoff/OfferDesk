"""LLM-извлечение структурированных данных из транскрибации звонка.

Тонкая обёртка над utils.ai_processor.chat_json:
  1) загружает промпт из prompts/extractor_{EXTRACTOR_PROMPT}.md (по умолчанию v1);
  2) вызывает chat_json(system, user) — клиент, прокси, модель, JSON-режим
     и разбор ответа уже закрыты в utils;
  3) валидирует результат через Pydantic-модель DealExtraction.

При любой ошибке (сеть, таймаут, ValueError от get_openai_client,
невалидный JSON, Pydantic ValidationError) функция БРОСАЕТ исключение —
решение о fallback на regex принимает pipeline.py.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import ValidationError

from utils import chat_json

from .schema import DealExtraction

logger = logging.getLogger(__name__)

PROMPT_VERSION = os.getenv("EXTRACTOR_PROMPT", "v1")
PROMPT_PATH = Path(__file__).parent / "prompts" / f"extractor_{PROMPT_VERSION}.md"


def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def extract_with_llm(transcript: str) -> DealExtraction:
    """Извлекает данные из транскрибации через LLM.

    Args:
        transcript: сырой текст транскрибации звонка.

    Returns:
        DealExtraction — валидированная Pydantic-модель.

    Raises:
        ValueError: пустая транскрибация или отсутствие OPENAI_API_KEY
            (последнее — из get_openai_client внутри chat_json).
        Exception: сетевая ошибка, таймаут, невалидный JSON от модели.
        ValidationError: JSON не соответствует схеме DealExtraction.
    """
    if not transcript or not transcript.strip():
        raise ValueError("Пустая транскрибация")

    system_prompt = _load_system_prompt()
    user_prompt = f"Транскрибация звонка:\n\n{transcript}"

    # chat_json возвращает dict (json_object уже распарсен).
    # Если модель вернёт не-JSON — chat_json сам бросит исключение.
    raw_dict = chat_json(system_prompt=system_prompt, user_prompt=user_prompt)

    # Pydantic-валидация. Если что-то не так — исключение уйдёт в pipeline.
    try:
        return DealExtraction.model_validate(raw_dict)
    except ValidationError:
        logger.error(
            "Pydantic ValidationError на ответе LLM. Ключи: %s",
            list(raw_dict.keys()) if isinstance(raw_dict, dict) else type(raw_dict),
        )
        raise
