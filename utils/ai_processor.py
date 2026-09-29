"""Взаимодействие с OpenAI для анализа диалогов с клиентами."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

CLIENT_REPORT_PROMPT = """
Ты — аналитик клиентских коммуникаций.
По транскрибации диалога извлеки структурированные данные для отчёта.

Верни ТОЛЬКО валидный JSON без markdown и пояснений со следующими полями:
{
  "client_name": "имя клиента или 'Не указано'",
  "topic": "тема разговора",
  "main_request": "основной запрос клиента",
  "mood": "настроение клиента (например: спокойный, раздражённый, заинтересованный)",
  "desired_timeline": "желаемые сроки клиента (дедлайн, период запуска, когда нужно готово)",
  "desired_cost": "желаемая или обсуждаемая стоимость / бюджет",
  "product_wishes": "что точно должно быть в финальном продукте: основные пожелания и обязательные требования",
  "next_steps": "рекомендуемые следующие шаги"
}

Если сроки, стоимость или пожелания не названы явно — пиши "Не указано".
Для product_wishes перечисли ключевые пункты через перенос строки или "; ".
""".strip()

DESIGN_REPORT_PROMPT = """
Ты — арт-директор и аналитик заказов на дизайн сайтов.
По транскрибации диалога подготовь данные для отчёта по дизайн-заказу.

Верни ТОЛЬКО валидный JSON без markdown и пояснений со следующими полями:
{
  "client_name": "имя клиента или 'Не указано'",
  "project_name": "название проекта / сайта или краткое имя",
  "site_type": "тип сайта (лендинг, корпоративный, магазин и т.п.)",
  "target_audience": "целевая аудитория",
  "style": "визуальный стиль и настроение дизайна",
  "color_palette": "цвета / палитра, если названы, иначе предложи уместную на основе диалога",
  "key_sections": "основные блоки/страницы сайта списком через перенос строки",
  "desired_timeline": "желаемые сроки",
  "desired_cost": "желаемая стоимость / бюджет",
  "product_wishes": "что точно должно быть в финальном дизайне (обязательные пожелания)",
  "image_prompt": "DETAILED English prompt for an AI image model: a realistic website UI mockup / hero landing page screenshot matching the brief. Include layout, style, colors, typography mood, device frame (desktop). No text watermarks, no logos of real brands unless mentioned. High quality, clean modern web design presentation."
}

Правила для image_prompt:
- пиши на английском;
- это должен быть готовый промпт для генерации примера дизайна сайта;
- отражай стиль, цвета, структуру и аудиторию из диалога;
- длина 2–5 предложений.
Если сроки/бюджет не названы — "Не указано".
""".strip()


def get_openai_client() -> OpenAI:
    """Публичный доступ к OpenAI-клиенту (ключ из OPENAI_API_KEY, опционально прокси)."""
    from utils.config import apply_outbound_proxy_env, openai_proxy_url

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "your_openai_api_key_here":
        raise ValueError(
            "Не задан OPENAI_API_KEY. Укажите ключ в файле .env"
        )

    apply_outbound_proxy_env()
    proxy = openai_proxy_url()
    # Через прокси запросы дольше; жёсткий лимит, чтобы CRM не «висел».
    # max_retries=0 обязательно: дефолт SDK = 2, и 35с×3 ≈ 105с — curl/Waitress
    # обрывают запрос раньше, чем pipeline успеет уйти в regex-fallback.
    timeout_s = float(os.getenv("OPENAI_TIMEOUT", "35" if proxy else "60"))
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": timeout_s,
        "max_retries": 0,
    }
    if proxy:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Для OPENAI_PROXY нужен пакет httpx (pip install httpx)"
            ) from exc
        # openai.proxy = {...} устарело; в SDK v1 — httpx Client(proxy=...)
        client_kwargs["http_client"] = httpx.Client(proxy=proxy, timeout=timeout_s)
    return OpenAI(**client_kwargs)


# Обратная совместимость для внутренних импортов
_get_client = get_openai_client


def _extract_json(text: str) -> dict[str, Any]:
    """Достаёт JSON из ответа модели, даже если он обёрнут в markdown."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Жадный захват внутри fence — иначе вложенные объекты обрезаются
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Не удалось распарсить JSON из ответа модели:\n{text}")


def _normalize_fields(data: dict[str, Any], required_keys: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in required_keys:
        value = data.get(key, "Не указано")
        if isinstance(value, list):
            value = "\n".join(str(item).strip() for item in value if str(item).strip())
        result[key] = str(value).strip() if value else "Не указано"
    return result


def chat_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Chat Completions с response_format=json_object → dict."""
    client = get_openai_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content or ""
    return _extract_json(content)


_chat_json = chat_json


def process_dialog_with_ai(text: str) -> dict[str, str]:
    """
    Клиентский отчёт: структурированные данные по диалогу.

    Returns:
        dict с ключами: client_name, topic, main_request, mood,
        desired_timeline, desired_cost, product_wishes, next_steps
    """
    if not text or not text.strip():
        raise ValueError("Текст диалога пуст")

    data = _chat_json(
        CLIENT_REPORT_PROMPT,
        (
            "Проанализируй следующий диалог с клиентом и верни JSON.\n"
            "Особое внимание удели: желаемым срокам, стоимости/бюджету "
            "и обязательным пожеланиям к финальному продукту.\n\n"
            f"{text.strip()}"
        ),
    )

    return _normalize_fields(
        data,
        (
            "client_name",
            "topic",
            "main_request",
            "mood",
            "desired_timeline",
            "desired_cost",
            "product_wishes",
            "next_steps",
        ),
    )


def process_design_order_with_ai(text: str) -> dict[str, str]:
    """
    Отчёт по заказу дизайна сайта + промпт для генерации примера изображения.

    Returns:
        dict с полями брифа и ключом image_prompt
    """
    if not text or not text.strip():
        raise ValueError("Текст диалога пуст")

    data = _chat_json(
        DESIGN_REPORT_PROMPT,
        (
            "Подготовь бриф по заказу дизайна сайта и image_prompt "
            "для генерации визуального примера UI.\n\n"
            f"{text.strip()}"
        ),
    )

    return _normalize_fields(
        data,
        (
            "client_name",
            "project_name",
            "site_type",
            "target_audience",
            "style",
            "color_palette",
            "key_sections",
            "desired_timeline",
            "desired_cost",
            "product_wishes",
            "image_prompt",
        ),
    )


AR_PROMPT = """
Ты — архитектор ИЖС. По транскрибации диалога с заказчиком подготовь черновик
раздела «Архитектурные решения» (АР) для индивидуального жилого дома.

Верни ТОЛЬКО валидный JSON:
{
  "client_name": "имя заказчика",
  "project_name": "рабочее название проекта дома",
  "house_type": "тип дома (например: одноэтажный современный)",
  "area": "площадь, м²",
  "floors": "этажность",
  "style": "архитектурный стиль и настроение",
  "plot_notes": "условия участка / ориентация / ограничения",
  "facade": "решения по фасаду и кровле",
  "layout": "описание планировки: зоны, комнаты, связи, логика зонирования день/ночь",
  "rooms": "полный список помещений с ориентировочными площадями (жилые и подсобные), каждый пункт с новой строки",
  "constructive": "рекомендации по конструктивной схеме (материал стен и т.п.)",
  "openings": "окна, двери, террасы, витражи",
  "norms": "краткие нормы/ограничения, которые учесть в АР",
  "next_steps": "что нужно для выпуска полного комплекта АР",
  "image_prompt": "DETAILED English prompt for exterior architectural visualization of this house: modern single-story home, matching style and materials from the brief, photorealistic, daytime, landscaped plot, no watermarks, no text overlays",
  "floor_plan_prompt": "DETAILED English prompt for a clean top-down 2D architectural floor plan of this single-story house ~120-140 m2. White background, black wall lines, room names labeled in Russian, approximate areas in m2, north arrow, scale bar, furniture outlines light gray, show living rooms AND utility rooms (котельная/техпомещение, санузлы, гардероб, прихожая, кухня-гостиная, спальни, гостевая, терраса). Professional architect drawing style, high clarity, no photorealism, no 3D perspective, no watermarks."
}

Правила:
- В rooms обязательно перечисли и жилые, и подсобные помещения (прихожая, котельная, санузлы, кладовая/гардероб и т.д.).
- floor_plan_prompt должен отражать тот же состав помещений, что в rooms/layout.
- Если данные не названы — предложи обоснованный вариант для семьи 3 человека + гостевая, 120–140 м².
""".strip()


def process_ar_with_ai(text: str) -> dict[str, str]:
    """Черновик архитектурных решений (АР) + промпты экстерьера и плана."""
    if not text or not text.strip():
        raise ValueError("Текст диалога пуст")

    data = _chat_json(
        AR_PROMPT,
        (
            "Сформируй АР по диалогу с заказчиком. "
            "Учти пожелания: современный одноэтажный дом, терраса/плоская или скатная кровля, "
            "тёплый контур, бюджет и участок из разговора. "
            "Отдельно подготовь floor_plan_prompt для генерации плана этажа со всеми помещениями.\n\n"
            f"{text.strip()}"
        ),
    )

    result = _normalize_fields(
        data,
        (
            "client_name",
            "project_name",
            "house_type",
            "area",
            "floors",
            "style",
            "plot_notes",
            "facade",
            "layout",
            "rooms",
            "constructive",
            "openings",
            "norms",
            "next_steps",
            "image_prompt",
            "floor_plan_prompt",
        ),
    )

    # Fallback, если модель не вернула отдельный промпт плана
    if result["floor_plan_prompt"] in {"Не указано", ""}:
        rooms_hint = result.get("rooms", "")
        result["floor_plan_prompt"] = (
            "Clean top-down 2D architectural floor plan of a modern single-story private house, "
            "about 130 square meters, white background, black wall lines, Russian room labels with areas, "
            "north arrow, scale bar, light furniture outlines. Include living zones and utility rooms: "
            f"hallway, boiler/technical room, bathrooms, wardrobe, kitchen-living, bedrooms, guest room, terrace. "
            f"Room list context: {rooms_hint}. Professional architect drawing, no 3D, no watermarks."
        )
    return result


ENGINEERING_PROMPT = """
Ты — инженер ИЖС (ОВ, ВК, отопление). По диалогу с заказчиком подготовь краткий бриф
проекта инженерных систем для индивидуального дома.

Верни ТОЛЬКО валидный JSON:
{
  "client_name": "имя",
  "project_name": "название объекта",
  "area": "площадь м²",
  "plot_notes": "условия участка (электричество, газ, скважина и т.п.)",
  "water_supply": "решение по водоснабжению (источник, разводка, ГВС)",
  "sewerage": "решение по канализации",
  "heating_gas": "отопление на газе (котёл, радиаторы/контур, учёт отсутствия магистрали)",
  "floor_heating": "тёплые полы (зоны, тип контура)",
  "ventilation": "вентиляция (приток/вытяжка, рекомендации)",
  "assumptions": "допущения базового варианта",
  "risks": "риски и что уточнить на участке",
  "next_steps": "следующие шаги до рабочего проекта"
}

Если газ на участке не подведён — предложи схему с газгольдером или этапностью.
База: дом ~120–140 м², Московская область, электричество есть.
""".strip()


def process_engineering_with_ai(text: str) -> dict[str, str]:
    """Бриф инженерных систем дома по транскрибации."""
    if not text or not text.strip():
        raise ValueError("Текст диалога пуст")

    data = _chat_json(
        ENGINEERING_PROMPT,
        (
            "Подготовь инженерный бриф (ВК, отопление газ, тёплые полы, вентиляция) "
            "под оптимальное функционирование дома из диалога.\n\n"
            f"{text.strip()}"
        ),
    )

    return _normalize_fields(
        data,
        (
            "client_name",
            "project_name",
            "area",
            "plot_notes",
            "water_supply",
            "sewerage",
            "heating_gas",
            "floor_heating",
            "ventilation",
            "assumptions",
            "risks",
            "next_steps",
        ),
    )
