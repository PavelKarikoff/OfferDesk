"""Локальный парсер транскрибации + проверка обязательных полей эталона."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from etalon_score import KP_THRESHOLD, etalon_match_score

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from file_parser import extract_text_from_file
except ImportError:  # pragma: no cover — прямой запуск вне web_app
    extract_text_from_file = None


class TranscriptParser:
    """
    Парсит протокол/транскрибацию регулярками и считает % заполнения
    обязательных полей эталона.
    """

    # Обязательные поля эталона (ключи CRM / БД)
    # Telegram и бюджет — опциональны; email обязателен наравне с телефоном.
    required_fields = [
        "client_phone",
        "client_email",
        "plot",
        "area",
        "material",
        "timeline",
        "funding_source",
    ]

    # Рекомендуемые (не входят в completion_percent)
    optional_fields = [
        "client_telegram",
        "budget",
    ]

    field_names = {
        "client_phone": "Телефон",
        "client_email": "Email",
        "client_telegram": "Telegram",
        "plot": "Участок (сотки/га)",
        "budget": "Бюджет",
        "area": "Площадь дома (м²)",
        "material": "Материал стен",
        "timeline": "Сроки строительства",
        "funding_source": "Финансирование",
        "catalog_project": "Проект каталога",
        # алиасы из чернового API
        "phone": "Телефон",
        "email": "Email",
        "plot_size": "Участок (сотки/га)",
        "deadline": "Сроки строительства",
        "financing": "Финансирование",
        "name": "Имя клиента",
    }

    # Алиасы → ключи CRM
    _ALIASES = {
        "phone": "client_phone",
        "email": "client_email",
        "plot_size": "plot",
        "deadline": "timeline",
        "financing": "funding_source",
        "name": "client_name",
    }

    def __init__(self, kp_threshold: int | None = None):
        self.kp_threshold = kp_threshold if kp_threshold is not None else KP_THRESHOLD
        self.patterns = {
            # (?<!\d)/(?!\d) — не резать номер; компактные и форматированные формы
            "phone": re.compile(
                r"(?<!\d)(?:"
                r"\+?7[\s\-]*\(?\d{3}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}|"  # +7 (916) 123-45-67
                r"8[\s\-]*\(?\d{3}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}|"     # 8-903-555-12-34 / 89161234567
                r"\+?7[\s\-]*\(?\d{3}\)?[\s\-]*\d{3}[\s\-]*\d{4}|"              # +7(903)555-1234
                r"8[\s\-]*\(?\d{3}\)?[\s\-]*\d{3}[\s\-]*\d{4}|"                 # 8(903)555-1234
                r"9\d{9}"                                                        # 9035551234 (без кода страны)
                r")(?!\d)"
            ),
            "email": re.compile(
                r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
            ),
            "plot_size": re.compile(
                r"(?:участок|земельный участок)\s*:?\s*([^\n]{3,80})",
                re.IGNORECASE,
            ),
            "plot_size_alt": re.compile(
                r"(\d+[–\-]?\d*\s*соток[^\n.,;]{0,50})",
                re.IGNORECASE,
            ),
            "budget": re.compile(
                r"(\d+[–\-]?\d*\s*млн\s*руб\.?)",
                re.IGNORECASE,
            ),
            "budget_labeled": re.compile(
                r"бюджет\s*:?\s*(\d+[–\-]?\d*\.?\d*)\s*(млн|тыс|руб|₽)?",
                re.IGNORECASE,
            ),
            "area": re.compile(r"(\d{2,3}[–\-]\d{2,3}\s*м²?)"),
            "area_single": re.compile(r"(\d{2,3}\s*м²?)"),
            "area_labeled": re.compile(
                r"площадь\s*:?\s*(\d+[–\-]?\d*\.?\d*)\s*(м²|м2|кв\.?\s*м)?",
                re.IGNORECASE,
            ),
            "material": re.compile(
                r"материал(?:\s*стен)?\s*:?\s*([а-яёА-ЯЁa-zA-Z\s\-]+?)(?:\n|\.|,|$)",
                re.IGNORECASE,
            ),
            "deadline": re.compile(
                r"сроки?\s*:?\s*([а-яёА-ЯЁ0-9\s\.\-–]+?)(?:\n|\.|,|$)",
                re.IGNORECASE,
            ),
            "financing": re.compile(
                r"финансирование\s*:?\s*([а-яёА-ЯЁ0-9\s%\.\+]+?)(?:\n|\.|,|$)",
                re.IGNORECASE,
            ),
            "name": re.compile(
                r"(?:потенциальный\s+заказчик|менеджер|клиент|заказчик)\s*[:—\-]\s*"
                r"([А-ЯЁа-яёA-Za-z]+)",
                re.IGNORECASE,
            ),
            "telegram": re.compile(
                r"(?:Telegram|Телеграм)\s*[:—\-]?\s*(@?[A-Za-z0-9_]{3,})",
                re.IGNORECASE,
            ),
            "telegram_handle": re.compile(r"(?<!\w)(@[A-Za-z0-9_]{3,})"),
        }

    def _empty_result(self) -> dict[str, Any]:
        return {
            "client_name": None,
            "client_phone": None,
            "client_email": None,
            "client_telegram": None,
            "plot": None,
            "budget": None,
            "area": None,
            "material": None,
            "timeline": None,
            "work_scope": None,
            "funding_source": None,
            "catalog_project": None,
            "status": "new",
            # алиасы чернового API
            "phone": "",
            "email": "",
            "plot_size": "",
            "deadline": "",
            "financing": "",
            "name": "",
            "completion_percent": 0,
            "is_complete": False,
            "missing_fields": list(self.required_fields),
            "missing_fields_names": [
                self.field_names.get(f, f) for f in self.required_fields
            ],
        }

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip(" .;,\t\r\n")
        return text or None

    @staticmethod
    def _clean_phone(phone: str) -> str:
        """Нормализация телефона к формату +7XXXXXXXXXX."""
        cleaned = re.sub(r"[^\d+]", "", phone)

        if cleaned.startswith("+") and len(cleaned) in [11, 12]:
            return cleaned

        if cleaned.startswith("8") and len(cleaned) == 11:
            return "+7" + cleaned[1:]

        if cleaned.startswith("7") and len(cleaned) == 11:
            return "+" + cleaned

        if cleaned.startswith("8") and len(cleaned) == 10:
            return "+7" + cleaned[1:]

        if cleaned.startswith("9") and len(cleaned) == 10:
            return "+7" + cleaned

        if cleaned.startswith("9") and len(cleaned) == 9:
            return "+7" + cleaned

        return cleaned

    def parse_text(self, text: str) -> dict[str, Any]:
        """Парсинг текста: поля CRM + % заполнения + список недостающих."""
        result = self._empty_result()
        if not text or not str(text).strip():
            return result

        text = str(text)

        # --- Имя ---
        name_match = self.patterns["name"].search(text)
        if name_match:
            result["client_name"] = self._clean(name_match.group(1))

        # --- Телефон ---
        phone_match = self.patterns["phone"].search(text)
        if phone_match:
            result["client_phone"] = self._clean_phone(phone_match.group(0).strip())

        # --- Email ---
        email_match = self.patterns["email"].search(text)
        if email_match:
            result["client_email"] = self._clean(email_match.group(0))

        # --- Telegram ---
        tg_match = self.patterns["telegram"].search(text)
        if not tg_match:
            tg_match = self.patterns["telegram_handle"].search(text)
        if tg_match:
            handle = tg_match.group(1).strip()
            if not handle.startswith("@"):
                handle = "@" + handle
            result["client_telegram"] = handle

        # --- Участок ---
        plot_alt = self.patterns["plot_size_alt"].search(text)
        if plot_alt:
            result["plot"] = self._clean(plot_alt.group(0))
        else:
            plot_match = self.patterns["plot_size"].search(text)
            if plot_match:
                result["plot"] = self._clean(plot_match.group(1))

        # --- Бюджет ---
        budget_match = self.patterns["budget"].search(text)
        if budget_match:
            result["budget"] = self._clean(budget_match.group(1))
        else:
            labeled = self.patterns["budget_labeled"].search(text)
            if labeled:
                num, unit = labeled.group(1), (labeled.group(2) or "").strip()
                result["budget"] = self._clean(f"{num} {unit}".strip())

        # --- Площадь ---
        area_match = self.patterns["area"].search(text)
        if not area_match:
            area_match = self.patterns["area_single"].search(text)
        if area_match:
            result["area"] = self._clean(area_match.group(1))
        else:
            labeled = self.patterns["area_labeled"].search(text)
            if labeled:
                num, unit = labeled.group(1), (labeled.group(2) or "м²").strip()
                result["area"] = self._clean(f"{num} {unit}")

        # --- Материал ---
        if "клеен" in text.lower() and "брус" in text.lower():
            result["material"] = "клееный брус"
        else:
            material_keywords = [
                "газобетон",
                "кирпич",
                "брус",
                "пеноблок",
                "керамоблок",
            ]
            for material in material_keywords:
                if material in text.lower():
                    result["material"] = material
                    break
            if not result["material"]:
                mat_match = self.patterns["material"].search(text)
                if mat_match:
                    candidate = self._clean(mat_match.group(1))
                    if candidate and any(k in candidate.lower() for k in material_keywords):
                        result["material"] = candidate

        # --- Проект каталога (клееный брус) ---
        try:
            from utils.timber_catalog import match_catalog_project

            hit = match_catalog_project(text)
            if hit:
                result["catalog_project"] = hit["name"]
        except Exception:
            pass

        # --- Сроки ---
        months = [
            "январь",
            "февраль",
            "март",
            "апрель",
            "май",
            "июнь",
            "июль",
            "август",
            "сентябрь",
            "октябрь",
            "ноябрь",
            "декабрь",
        ]
        lower = text.lower()
        found_months = [m for m in months if m in lower]
        if found_months:
            result["timeline"] = found_months[-1]
            year_match = re.search(r"(20\d{2})", text)
            if year_match:
                result["timeline"] = f"{result['timeline']} {year_match.group(1)}"
        else:
            for season in ("весна", "лето", "осень", "зима"):
                if season in lower:
                    result["timeline"] = season
                    break
        if not result["timeline"] and re.search(r"через\s+месяц", lower):
            result["timeline"] = "через месяц"
        if not result["timeline"]:
            dl = self.patterns["deadline"].search(text)
            if dl:
                candidate = self._clean(dl.group(1))
                if candidate:
                    cl = candidate.lower()
                    looks_like_date = (
                        any(m in cl for m in months)
                        or any(s in cl for s in ("весна", "лето", "осень", "зима"))
                        or bool(re.search(r"20\d{2}", candidate))
                    )
                    if looks_like_date:
                        result["timeline"] = candidate

        # --- Финансирование ---
        if "накопл" in lower or re.search(r"свои\s+(средств|деньг|накоп)", lower) or "мои деньги" in lower:
            result["funding_source"] = "собственные средства"
        elif "маткапитал" in lower:
            result["funding_source"] = "маткапитал"
        elif "ипотек" in lower:
            result["funding_source"] = "ипотека"
        else:
            fin = self.patterns["financing"].search(text)
            if fin:
                result["funding_source"] = self._clean(fin.group(1))

        return self._with_completion(result)

    def _with_completion(self, result: dict[str, Any]) -> dict[str, Any]:
        """Считает % заполнения, missing и алиасы."""
        fields = list(self.required_fields)
        try:
            from pricing import is_timber_material
        except ImportError:
            is_timber_material = lambda raw: "брус" in str(raw or "").lower() or "клеен" in str(raw or "").lower()
        if is_timber_material(result.get("material")):
            if "catalog_project" not in fields:
                fields.append("catalog_project")
        filled_count = sum(
            1
            for field in fields
            if result.get(field) and str(result[field]).strip()
        )
        total_required = len(fields)
        percent = (
            round((filled_count / total_required) * 100) if total_required else 0
        )

        missing = [
            field
            for field in fields
            if not result.get(field) or not str(result[field]).strip()
        ]

        result["completion_percent"] = percent
        result["is_complete"] = percent >= self.kp_threshold
        result["missing_fields"] = missing
        result["missing_fields_names"] = [
            self.field_names.get(field, field) for field in missing
        ]

        # Алиасы чернового API (phone / plot_size / …)
        result["phone"] = result.get("client_phone") or ""
        result["email"] = result.get("client_email") or ""
        result["plot_size"] = result.get("plot") or ""
        result["deadline"] = result.get("timeline") or ""
        result["financing"] = result.get("funding_source") or ""
        result["name"] = result.get("client_name") or ""

        return result

    def parse_file(self, file_path: str) -> dict[str, Any]:
        """Парсинг файла (.txt, .docx, .pdf) → parse_text()."""
        if extract_text_from_file is not None:
            text = extract_text_from_file(file_path)
            if text is None:
                ext = os.path.splitext(file_path)[1].lower()
                raise ValueError(f"Unsupported or unreadable file format: {ext}")
            return self.parse_text(text)

        # Fallback без file_parser
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".txt":
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
        elif ext == ".docx":
            import docx

            doc = docx.Document(file_path)
            text = "\n".join(para.text for para in doc.paragraphs)
        elif ext == ".pdf":
            import PyPDF2

            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = "".join(page.extract_text() or "" for page in reader.pages)
        else:
            raise ValueError(f"Unsupported file format: {ext}")
        return self.parse_text(text)


def parse_transcript_local(text: str) -> dict:
    """
    Парсит транскрибацию с помощью регулярных выражений (без OpenAI)
    по полям эталонного протокола.
    """
    return TranscriptParser().parse_text(text)


def validate_against_etalon(
    data: str | Mapping[str, Any],
    *,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Сравнивает транскрибацию (или уже распарсенные поля) с эталоном.

    Возвращает результат etalon_match_score плюс parsed-поля:
    - score, grade, missing, questions, can_generate_kp, is_complete
    - parsed: dict извлечённых/переданных полей
    - completion_percent / missing_fields из TranscriptParser (критичные 8)
    """
    parser = TranscriptParser()

    if isinstance(data, str):
        parsed: dict[str, Any] = parser.parse_text(data)
    else:
        parsed = parser._with_completion(dict(data))

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            # поддержка алиасов phone → client_phone и т.п.
            crm_key = TranscriptParser._ALIASES.get(key, key)
            parsed[crm_key] = text
        parsed = parser._with_completion(parsed)

    match = etalon_match_score(parsed)
    return {
        **match,
        "parsed": parsed,
        "completion_percent": parsed.get("completion_percent", match["score"]),
        "missing_fields": parsed.get("missing_fields", match.get("missing_keys", [])),
        "missing_fields_names": parsed.get(
            "missing_fields_names", match.get("missing", [])
        ),
    }
