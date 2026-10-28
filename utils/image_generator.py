"""Генерация изображений через OpenAI Images API."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import OpenAI

from utils.ai_processor import get_openai_client
from utils.logging_setup import get_logger

load_dotenv()
logger = get_logger("image")

# Хосты, которым доверяем при скачивании изображений по URL от API
_ALLOWED_IMAGE_HOST_SUFFIXES = (
    "openai.com",
    "oaiusercontent.com",
    "blob.core.windows.net",
    "proxyapi.ru",
)


def generate_design_image(
    prompt: str,
    output_path: str | Path,
    *,
    size: str | None = None,
) -> Path:
    """
    Генерирует изображение по промпту через OpenAI и сохраняет PNG на диск.

    Модель берётся из OPENAI_IMAGE_MODEL (по умолчанию gpt-image-1).
    При недоступности модели выполняется fallback на dall-e-3.
    """
    if not prompt or not prompt.strip():
        raise ValueError("Промпт для генерации изображения пуст")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    client = get_openai_client()
    model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
    image_size = size or os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")

    try:
        image_bytes = _request_image(client, model=model, prompt=prompt, size=image_size)
    except Exception:
        if model == "dall-e-3":
            raise
        logger.exception(
            "Модель изображений %s недоступна — fallback на dall-e-3", model
        )
        image_bytes = _request_image(
            client,
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
        )

    output_path.write_bytes(image_bytes)
    return output_path.resolve()


def _request_image(client: OpenAI, *, model: str, prompt: str, size: str) -> bytes:
    kwargs: dict = {
        "model": model,
        "prompt": prompt.strip(),
        "size": size,
        "n": 1,
    }

    if model == "dall-e-3":
        kwargs["response_format"] = "b64_json"
        kwargs["quality"] = "standard"

    try:
        result = client.images.generate(**kwargs)
    except TypeError:
        kwargs.pop("response_format", None)
        kwargs.pop("quality", None)
        result = client.images.generate(**kwargs)

    item = result.data[0]
    b64 = getattr(item, "b64_json", None)
    if b64:
        return base64.b64decode(b64)

    url = getattr(item, "url", None)
    if url:
        return _download_bytes(url)

    raise ValueError("OpenAI не вернул изображение (ни b64_json, ни url)")


def _is_allowed_image_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in _ALLOWED_IMAGE_HOST_SUFFIXES)


def _download_bytes(url: str) -> bytes:
    import urllib.request

    if not _is_allowed_image_url(url):
        raise ValueError(f"Отказ в скачивании изображения: недоверенный URL host")

    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
        return response.read()
