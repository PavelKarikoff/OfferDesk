"""Загрузка документов knowledge_base для RAG / промптов КП."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge_base"

# Канонические документы для этапа «Стройка» / КП
COMPANY_STANDARDS_PATH = KNOWLEDGE_DIR / "company_standards.md"
COMPANY_COMPLECTATIONS_PATH = KNOWLEDGE_DIR / "company_complectations.md"
COMPLECTATIONS_SHORT_PATH = KNOWLEDGE_DIR / "complectations_short.md"
ETALON_PROTOCOL_PATH = KNOWLEDGE_DIR / "etalon_protocol.md"
TIMBER_STANDARDS_PATH = KNOWLEDGE_DIR / "timber" / "standards.md"
TIMBER_KP_TEMPLATE_PATH = KNOWLEDGE_DIR / "timber" / "kp_template.md"
TIMBER_COMPANY_FOREST_PATH = KNOWLEDGE_DIR / "timber" / "company_forest.md"


@lru_cache(maxsize=16)
def load_knowledge_doc(name: str) -> str:
    """Читает markdown из knowledge_base по имени файла."""
    path = KNOWLEDGE_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Документ базы знаний не найден: {path}")
    return path.read_text(encoding="utf-8")


def load_company_standards() -> str:
    """Стандарты «Дом-Мастер» (цена м², газобетон, состав тёплого контура)."""
    return load_knowledge_doc("company_standards.md")


def load_company_complectations() -> str:
    """Виды комплектаций: тёплый контур, White Box, под ключ."""
    return load_knowledge_doc("company_complectations.md")


def load_complectations_short() -> str:
    """Краткая справка по комплектациям для быстрой вставки в КП."""
    return load_knowledge_doc("complectations_short.md")


def load_timber_standards() -> str:
    """Правила КП домов из клееного бруса (не газобетон «Дом-Мастер»)."""
    return load_knowledge_doc("timber/standards.md")


def load_timber_kp_template() -> str:
    """Jinja-макет КП клееного бруса."""
    return load_knowledge_doc("timber/kp_template.md")


def load_timber_company_forest() -> str:
    """Карточка компании контура клееного бруса для переменных шаблона."""
    return load_knowledge_doc("timber/company_forest.md")


def _excerpt(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n\n[…обрезано…]"


def company_standards_excerpt(max_chars: int = 6000) -> str:
    """Укороченный фрагмент стандартов для системного промпта."""
    return _excerpt(load_company_standards(), max_chars)


def company_complectations_excerpt(max_chars: int = 5000) -> str:
    """Укороченный фрагмент комплектаций для системного промпта."""
    return _excerpt(load_company_complectations(), max_chars)


def complectations_short_excerpt(max_chars: int = 2000) -> str:
    """Краткая таблица комплектаций — приоритетный блок для КП."""
    return _excerpt(load_complectations_short(), max_chars)


def list_knowledge_docs() -> list[str]:
    if not KNOWLEDGE_DIR.is_dir():
        return []
    return sorted(
        str(p.relative_to(KNOWLEDGE_DIR))
        for p in KNOWLEDGE_DIR.rglob("*.md")
        if p.is_file()
    )
