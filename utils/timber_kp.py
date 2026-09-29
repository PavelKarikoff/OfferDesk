"""КП домов из клееного бруса.

Не использует ставку тёплого контура «Дом-Мастер» (75 000 ₽/м², газобетон).
Итог = сумма разделов сметы + накладные.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from utils.config import DEFAULT_CLIENT_NAME, sanitize_client_name
from utils.money import format_money
from utils.pdf_generator import REPORTS_DIR, generate_pdf_report

INCL = "incl"
DASH = None

ACCENT = "#3d5a40"
ACCENT_SOFT = "#eef3ee"

OVERHEAD_PCT_DEFAULT = 5

WATERMARK_LABELS = {
    "draft": "ЧЕРНОВИК",
    "approved": "УТВЕРЖДЕНО",
}

KP_DIR = REPORTS_DIR / "kp" / "timber"
FOREST_ASSETS = Path(__file__).resolve().parent.parent / "templates" / "assets" / "dom_forest"

_PROTOCOL_RE = re.compile(r"№\s*(\d{1,2}\s*/\s*\d{2})")


def is_timber_material(raw: Any) -> bool:
    """Стены из бруса / клееного бруса — контур КП timber, не газобетон."""
    text = str(raw or "").lower()
    return "клеен" in text or "брус" in text


def _protocol_number(transcript: Any) -> str:
    match = _PROTOCOL_RE.search(str(transcript or ""))
    if not match:
        return "XX/XX"
    return re.sub(r"\s+", "", match.group(1))

COMPANY_FOREST: dict[str, Any] = {
    "company_name": "Дома из клееного бруса",
    "company_legal": "Дома из клееного бруса",
    "company_tagline": "Дома из клееного бруса · Московская область",
    "phone": "+7 (499) 877-55-33",
    "phone_free": "8 800 234-20-96",
    "email": "info@dom-forest.ru",
    "website": "https://dom-forest.ru",
    "address": "119002, г. Москва, переулок Денежный 8/10",
    "region": "Московской области",
    "logo_url": str(FOREST_ASSETS / "logo.png"),
    "socials": [
        {
            "name": "ВКонтакте",
            "url": "https://vk.com/domforest43",
            "icon": str(FOREST_ASSETS / "icon_vk.svg"),
        },
        {
            "name": "Telegram",
            "url": "https://t.me/domforest43",
            "icon": str(FOREST_ASSETS / "icon_telegram.svg"),
        },
        {
            "name": "WhatsApp",
            "url": "https://wa.me/74998775533",
            "icon": str(FOREST_ASSETS / "icon_whatsapp.svg"),
        },
        {
            "name": "Дзен",
            "url": "https://dzen.ru/domforest43",
            "icon": str(FOREST_ASSETS / "icon_zen.svg"),
        },
        {
            "name": "YouTube",
            "url": "https://www.youtube.com/channel/UCN-8Er3ZZdI3LS_CAD3sZAQ",
            "icon": str(FOREST_ASSETS / "icon_youtube.svg"),
        },
    ],
}

VARIANTS_DEFAULT = (
    {"name": "Базовый", "selected": False},
    {"name": "Стандарт", "selected": True},
    {"name": "Комфорт", "selected": False},
)

INCLUDED_DEFAULT = [
    "Фундамент свайный железобетонный",
    "Стеновой комплект из клееного бруса (профиль финский)",
    "Утеплённая кровля (металлочерепица)",
    "Черновой утеплённый пол",
    "Силовой каркас внутренних стен",
    "Обсадные коробки, окна Rehau/Veka, входная дверь",
    "Доставка и разгрузка материалов",
    "Биозащитная обработка бруса",
    "Технадзор",
]

EXCLUDED_DEFAULT = [
    "Чистовая внутренняя отделка",
    "Инженерные системы (водоснабжение, канализация, отопление)",
    "Ландшафт, забор, отмостка",
    "Септик / ЛОС",
    "Подключение газа",
]

WARRANTY_DEFAULT = [
    "5 лет — на конструктив (фундамент, стены, несущие элементы)",
    "2 года — на кровельные и монтажные работы",
    "2 года — на окна и двери",
    "Гарантия фиксируется в договоре подряда",
]

NEXT_STEPS_DEFAULT = [
    "Выберите вариант КП (Базовый / Стандарт / Комфорт)",
    "Согласуем материалы и объёмы",
    "Подготовим договор и календарный план",
    "Выезд на участок для финального обмера",
]

# Учебный срез сметы «Сириус 2.0» / вариант «Стандарт» (макет шаблона).
# Итоги разделов — как в исходном КП, не пересчёт суммы строк.
SIRIUS_STANDARD_SECTIONS: list[dict[str, Any]] = [
    {
        "title": "2.1. Фундамент свайный железобетонный забивной",
        "short": "Фундамент",
        "total": 644_100,
        "items": [
            ("Разбивка осей, вынос точек до 150 м", 1, "ед.", DASH, 15_000, 15_000),
            ("Сваи 200x200x3000", 83, "шт.", 3_600, DASH, 298_800),
            ("Пластина на ЖБС", 83, "шт.", 500, DASH, 41_500),
            ("Монтаж пластины", 83, "шт.", DASH, 500, 41_500),
            ("Установка сваи 200x200x3000", 83, "шт.", DASH, 2_600, 215_800),
            ("Доставка ЖБС", 1, "шт.", DASH, 10_000, 10_000),
            ("Доставка сваебойной машины 140 км", 1, "шт.", DASH, 15_000, 15_000),
            ("Расходные материалы", 1, "компл.", 5_000, DASH, 5_000),
            ("Выезд технадзора", 1, "шт.", DASH, INCL, INCL),
        ],
    },
    {
        "title": "2.2. Стеновой комплект (клееный брус)",
        "short": "Стеновой комплект",
        "total": 5_652_119,
        "items": [
            ("Гидроизоляция Стеклоизол Технониколь", 2, "рул.", 1_700, 2_500, 3_400),
            ("Монтаж бруса обвязки 150x200x6000", 12.42, "м³", 25_000, 7_500, 310_500),
            ("Балки 1 этажа. Доска обрезная 100x200x6000", 8.76, "м³", 25_000, 7_500, 219_000),
            ("Балки клееные 370x205 мм", 1.82, "м³", 70_000, 7_500, 127_400),
            ("Брус профилированный клееный 205x185 мм", 75.61, "м³", 51_500, DASH, 3_893_915),
            ("Раскрой материала, нарезка чаш", 75.61, "м³", DASH, 1_650, 124_757),
            ("Фрезеровка пазов под обсадные коробки", 75.61, "м³", DASH, 900, 68_049),
            ("Биозащитная обработка Lignofix", 75.61, "м³", 900, 800, 68_049),
            ("Обработка торцов TEKNOS", 75.61, "м³", 350, 350, 26_464),
            ("Монтаж стенового комплекта", 75.61, "м³", DASH, 9_500, 718_295),
            ("Утеплитель межвенцовый политерм", 1994, "мп", 20, DASH, 39_880),
            ("Пружинный узел Ted Wood", 1330, "шт.", 125, DASH, 166_250),
            ("Компенсаторы усадки", 9, "шт.", 1_500, 2_500, 13_500),
        ],
    },
    {
        "title": "2.3. Силовой каркас внутренних стен",
        "short": "Силовой каркас",
        "total": 396_450,
        "items": [
            ("Доска 45x140x6000", 2.57, "м³", 35_000, DASH, 89_950),
            ("Монтаж скользящего каркаса", 110, "м²", DASH, 750, 82_500),
            ("Утеплитель RockWool 150 мм", 330, "м²", 270, 150, 89_100),
            ("Пароизоляция Grand Line", 220, "м²", 200, 120, 44_000),
        ],
    },
    {
        "title": "2.4. Устройство утеплённой кровли",
        "short": "Кровля",
        "total": 3_243_360,
        "items": [
            ("Монтаж кровли (полный комплекс)", 304, "м²", DASH, 5_100, 1_550_400),
            ("Доска обрезная 50x200x6000", 8.88, "м³", 25_000, DASH, 222_000),
            ("Доска обрезная 23x150x6000", 8.1, "м³", 25_000, DASH, 202_500),
            ("Брусок 50x50x3000", 2.22, "м³", 25_000, DASH, 55_500),
            ("Металлочерепица классик 0,5", 1, "компл.", 1_109_600, DASH, 1_109_600),
            ("Крепёж", 304, "м²", 340, DASH, 103_360),
        ],
    },
    {
        "title": "2.5. Черновой утеплённый пол (1 этаж)",
        "short": "Черновой пол",
        "total": 710_640,
        "items": [
            ("Мембрана супердифузионная Grand Line", 186, "м²", 220, 200, 40_920),
            ("Утеплитель RockWool 200 мм", 744, "м²", 290, 100, 215_760),
            ("Пароизоляция Grand Line", 186, "м²", 240, 120, 44_640),
            ("Доска 23x150", 186, "м²", 600, 200, 111_600),
            ("Доска 40x150", 186, "м²", 500, 100, 93_000),
        ],
    },
    {
        "title": "2.6. Обсадные коробки, окна, двери",
        "short": "Окна, двери",
        "total": 1_066_450,
        "items": [
            ("Обсадные коробки", 18, "ед.", DASH, 7_500, 135_000),
            ("Окна Rehau/Veka 70 мм", 1, "компл.", 845_000, DASH, 845_000),
            ("Входная дверь металлическая", 1, "компл.", 35_000, DASH, 35_000),
        ],
    },
    {
        "title": "2.7. Доставка и такелаж",
        "short": "Доставка и такелаж",
        "total": 806_000,
        "items": [
            ("Доставка материалов", 3, "ед.", DASH, 100_000, 300_000),
            ("Разгрузка манипулятором", 3, "ед.", DASH, 37_000, 111_000),
            ("Услуги крана", 8, "смена", DASH, 35_000, 280_000),
            ("Аренда бытовки", 1, "компл.", DASH, 80_000, 80_000),
            ("Аренда биотуалета", 1, "компл.", DASH, 35_000, 35_000),
        ],
    },
]


def _qty_fmt(qty: int | float) -> str:
    if isinstance(qty, float):
        text = f"{qty:.2f}".rstrip("0").rstrip(".")
        return text.replace(".", ",")
    if isinstance(qty, int) and abs(qty) >= 1000:
        return format_money(qty)
    return str(qty)


def _money_cell(value: Any) -> str:
    if value is DASH or value is None:
        return "—"
    if value == INCL:
        return "вкл."
    return format_money(value)


def format_sections(raw_sections: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Готовит разделы сметы к подстановке в Jinja-шаблон."""
    result: list[dict[str, Any]] = []
    for section in raw_sections:
        rows = []
        for row in section["items"]:
            if isinstance(row, dict):
                name, qty, unit, material, work, total = (
                    row["name"],
                    row["qty"],
                    row["unit"],
                    row.get("material"),
                    row.get("work"),
                    row.get("total"),
                )
            else:
                name, qty, unit, material, work, total = row
            rows.append(
                {
                    "name": name,
                    "qty": qty,
                    "qty_fmt": _qty_fmt(qty),
                    "unit": unit,
                    "material_fmt": _money_cell(material),
                    "work_fmt": _money_cell(work),
                    "total_fmt": _money_cell(total),
                }
            )
        total = int(section["total"])
        result.append(
            {
                "title": section["title"],
                "short": section["short"],
                "total": total,
                "total_fmt": format_money(total),
                "rows": rows,
            }
        )
    return result


def calc_totals(
    sections: list[Mapping[str, Any]],
    overhead_pct: int = OVERHEAD_PCT_DEFAULT,
) -> tuple[int, int, int]:
    """Сумма разделов, накладные, итог с накладными."""
    subtotal = sum(int(s["total"]) for s in sections)
    overhead = int(round(subtotal * overhead_pct / 100))
    return subtotal, overhead, subtotal + overhead


def build_timber_kp_context(
    *,
    client_name: str = "Заказчик",
    project_name: str = "Индивидуальный жилой дом",
    current_date: str | None = None,
    company: Mapping[str, Any] | None = None,
    sections: list[Mapping[str, Any]] | None = None,
    overhead_pct: int = OVERHEAD_PCT_DEFAULT,
    variants: list[Mapping[str, Any]] | None = None,
    included: list[str] | None = None,
    excluded: list[str] | None = None,
    warranty: list[str] | None = None,
    next_steps: list[str] | None = None,
    protocol_number: str = "XX/XX",
    manager: str = "",
    area_note: str = "",
    kp_number: str | None = None,
    watermark: str = "draft",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Контекст Jinja для templates/kp_timber_template.html."""
    company_data = {**COMPANY_FOREST, **(company or {})}
    formatted = format_sections(list(sections or SIRIUS_STANDARD_SECTIONS))
    subtotal, overhead, grand = calc_totals(formatted, overhead_pct)
    variant_rows = [dict(v) for v in (variants or VARIANTS_DEFAULT)]
    selected = next((v["name"] for v in variant_rows if v.get("selected")), variant_rows[0]["name"])
    today = current_date or datetime.now().strftime("%d.%m.%Y")
    wm_key = watermark if watermark in WATERMARK_LABELS else "draft"
    valid_until = (datetime.now() + timedelta(days=14)).strftime("%d.%m.%Y")

    ctx: dict[str, Any] = {
        **company_data,
        "title": "Строительство дома из клееного бруса",
        "client_name": client_name,
        "project_name": project_name or "Индивидуальный жилой дом",
        "current_date": today,
        "date": today,
        "valid_until": valid_until,
        "kp_number": kp_number or today.replace(".", ""),
        "manager": manager,
        "area_note": area_note,
        "protocol_number": protocol_number,
        "variants": variant_rows,
        "selected_variant": selected,
        "variant_label": f"Вариант «{selected}»",
        "sections": formatted,
        "overhead_pct": overhead_pct,
        "subtotal": subtotal,
        "subtotal_fmt": format_money(subtotal),
        "overhead": overhead,
        "overhead_fmt": format_money(overhead),
        "grand_total": grand,
        "grand_total_fmt": format_money(grand),
        "included": list(included or INCLUDED_DEFAULT),
        "excluded": list(excluded or EXCLUDED_DEFAULT),
        "warranty": list(warranty or WARRANTY_DEFAULT),
        "next_steps": list(next_steps or NEXT_STEPS_DEFAULT),
        "accent": ACCENT,
        "accent_soft": ACCENT_SOFT,
        "watermark": wm_key,
        "watermark_label": WATERMARK_LABELS[wm_key],
    }
    if extra:
        ctx.update(extra)
    return ctx


def generate_timber_kp_pdf(
    *,
    output_path: str | Path | None = None,
    save_html: bool = True,
    **context_kwargs: Any,
) -> dict[str, Any]:
    """Рендерит HTML+PDF КП клееного бруса. Возвращает пути и итоги."""
    context = build_timber_kp_context(**context_kwargs)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if output_path is None:
        KP_DIR.mkdir(parents=True, exist_ok=True)
        slug = (context.get("project_name") or "kp").replace(" ", "_")[:40]
        output_path = KP_DIR / f"KP_timber_{slug}_{stamp}.pdf"
    path = generate_pdf_report(
        context,
        output_path=output_path,
        template_name="kp_timber_template.html",
        save_html=save_html,
    )
    meta = {
        "pdf_path": str(path),
        "html_path": str(path.with_suffix(".html")),
        "subtotal": context["subtotal"],
        "overhead": context["overhead"],
        "grand_total": context["grand_total"],
        "grand_total_fmt": context["grand_total_fmt"],
        "company_name": context["company_name"],
        "project_name": context["project_name"],
        "watermark": context["watermark"],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.with_suffix(".json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta


def generate_timber_kp_from_deal(
    deal: Mapping[str, Any],
    *,
    watermark: str = "draft",
    manager_name: str | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """КП клееного бруса по карточке сделки (эталон + смета шаблона)."""
    deal_id = deal.get("id") or "x"
    client_name = sanitize_client_name(deal.get("client_name"), DEFAULT_CLIENT_NAME)
    area_raw = str(deal.get("area") or "").strip()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_dir) if output_dir else KP_DIR / f"deal_{deal_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"KP_timber_deal{deal_id}_{stamp}.pdf"

    from utils.timber_catalog import catalog_label, find_catalog_project

    catalog = find_catalog_project(deal.get("catalog_project"))
    if catalog and catalog.get("name"):
        project_title = catalog["name"]
        bits = [catalog_label(catalog)]
        if area_raw and str(catalog.get("area_m2") or "") not in area_raw:
            bits.append(area_raw)
        area_note = " · ".join(bits)
    else:
        project_title = str(deal.get("catalog_project") or "").strip() or (
            "Индивидуальный жилой дом из клееного бруса"
        )
        area_note = area_raw

    meta = generate_timber_kp_pdf(
        output_path=pdf_path,
        client_name=client_name,
        project_name=project_title,
        area_note=area_note,
        protocol_number=_protocol_number(deal.get("transcript")),
        manager=manager_name or "",
        watermark=watermark,
        extra={"deal_id": deal_id, "catalog_project": project_title},
    )
    meta.update(
        {
            "kp_kind": "timber",
            "deal_id": deal_id,
            "client_name": client_name,
            "area_m2": area_raw or "—",
            "total": meta["grand_total"],
            "total_fmt": f'{meta["grand_total_fmt"]} ₽',
            "kp_number": Path(meta["pdf_path"]).stem,
        }
    )
    Path(meta["pdf_path"]).with_suffix(".json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta
