"""Каталог типовых домов из клееного бруса.

Маркетинговая «цена от» — ориентир каталога, не смета тёплого контура в КП.
"""

from __future__ import annotations

from typing import Any

CATALOG_URL = "https://dom-forest.ru/katalog/doma-iz-kleenogo-brusa/"
INDIVIDUAL_PROJECT = "Индивидуальный"

# Длинные имена раньше коротких при поиске в тексте («Сириус 2.0» раньше «Сириус»).
CATALOG_PROJECTS: tuple[dict[str, Any], ...] = (
    {"name": "Астерия", "area_m2": 190.7, "floors": 1, "size": "11,7 × 19,0", "price_from": None},
    {"name": "Альтаир", "area_m2": 322.31, "floors": 1, "size": "26,35 × 15,25", "price_from": None},
    {"name": "Эмбер", "area_m2": 171, "floors": 1, "size": "16,1 × 13,6", "price_from": None},
    {"name": "Гранд", "area_m2": 161, "floors": 1, "size": "20,7 × 11", "price_from": 10_720_000},
    {"name": "Сириус 2.0", "area_m2": 200, "floors": 1, "size": "18 × 16", "price_from": 12_014_000},
    {"name": "Сириус", "area_m2": 218, "floors": 1, "size": "17 × 19", "price_from": 10_819_000},
    {"name": "Грэй", "area_m2": 167, "floors": 2, "size": "13 × 12", "price_from": 10_566_000},
    {"name": "Фён", "area_m2": 208, "floors": 2, "size": "11,16 × 13,33", "price_from": 9_564_000},
    {"name": "Провансаль", "area_m2": 190, "floors": 2, "size": "10 × 8", "price_from": 6_888_000},
    {"name": "Альба", "area_m2": 89.43, "floors": 1, "size": "9 × 11", "price_from": 4_721_000},
    {"name": "Оптимус", "area_m2": 115, "floors": 1, "size": "14 × 8", "price_from": 6_164_000},
    {"name": "Прион", "area_m2": 183.17, "floors": 1, "size": "19 × 13", "price_from": 10_548_000},
    {"name": "Велес", "area_m2": 319, "floors": 2, "size": "22 × 12", "price_from": 18_023_000},
    {"name": "Орфей", "area_m2": 87, "floors": 1, "size": "9 × 10", "price_from": 4_987_000},
    {"name": "Бруно", "area_m2": 138, "floors": 1, "size": "10 × 16", "price_from": 9_223_000},
    {"name": "Арси", "area_m2": 144, "floors": 1, "size": "14 × 14", "price_from": 8_580_000},
    {"name": "Феникс", "area_m2": 203, "floors": 2, "size": "13 × 14", "price_from": 11_171_000},
    {"name": "Скат", "area_m2": 169, "floors": 1, "size": "9,4 × 16,2", "price_from": 7_381_000},
    {"name": "Орион", "area_m2": 182.58, "floors": 1, "size": "", "price_from": 10_255_000},
    {"name": "Вест", "area_m2": 75, "floors": 1, "size": "12,1 × 8,6", "price_from": None},
    {"name": "Эдельвейс", "area_m2": 237, "floors": 2, "size": "18 × 9", "price_from": 13_313_000},
    {"name": "Таврус", "area_m2": 121, "floors": 1, "size": "14 × 12", "price_from": 7_272_000},
    {"name": "Симфония", "area_m2": 130, "floors": 1, "size": "9 × 19", "price_from": None},
    {"name": "Пегас", "area_m2": 334, "floors": 2, "size": "16 × 25", "price_from": None},
    {"name": "Вега", "area_m2": None, "floors": 1, "size": "15 × 13", "price_from": None},
    {"name": "Сена", "area_m2": 177.3, "floors": 1, "size": "15 × 16", "price_from": None},
    {"name": "Монтель", "area_m2": 163.1, "floors": 2, "size": "9 × 11", "price_from": 10_655_000},
    {"name": "Сеат", "area_m2": 124, "floors": 1, "size": "15 × 9", "price_from": None},
    {"name": "Ворслея", "area_m2": 207, "floors": 2, "size": "12 × 11", "price_from": None},
    {"name": "Бриз", "area_m2": 226.64, "floors": 2, "size": "14 × 15", "price_from": None},
)

_NAME_ORDER = tuple(
    sorted(CATALOG_PROJECTS, key=lambda p: len(p["name"]), reverse=True)
)


def _norm(text: str) -> str:
    return (
        str(text or "")
        .lower()
        .replace("ё", "е")
        .replace("«", "")
        .replace("»", "")
        .replace('"', "")
        .replace("'", "")
    )


def find_catalog_project(name: str | None) -> dict[str, Any] | None:
    """Точное имя из каталога (без учёта регистра и ё)."""
    needle = _norm(name or "")
    if not needle:
        return None
    if needle == _norm(INDIVIDUAL_PROJECT):
        return {"name": INDIVIDUAL_PROJECT, "area_m2": None, "floors": None, "size": "", "price_from": None}
    for item in CATALOG_PROJECTS:
        if _norm(item["name"]) == needle:
            return item
    return None


def match_catalog_project(text: str | None) -> dict[str, Any] | None:
    """Ищет название типового проекта в протоколе / поле сделки."""
    blob = _norm(text or "")
    if not blob:
        return None
    for item in _NAME_ORDER:
        if _norm(item["name"]) in blob:
            return item
    return None


def catalog_label(item: dict[str, Any]) -> str:
    area = item.get("area_m2")
    area_s = f"{area} м²" if area else "площадь уточняется"
    floors = item.get("floors")
    floor_s = f"{floors} эт." if floors else ""
    bits = [item["name"], area_s]
    if floor_s:
        bits.append(floor_s)
    if item.get("size"):
        bits.append(item["size"])
    return " · ".join(bits)
