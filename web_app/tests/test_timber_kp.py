"""Тесты шаблона КП домов из клееного бруса."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web_app"
for _p in (str(ROOT), str(WEB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from utils.knowledge_base import (  # noqa: E402
    load_timber_company_forest,
    load_timber_kp_template,
    load_timber_standards,
)
from utils.pdf_generator import render_html  # noqa: E402
from utils.timber_kp import (  # noqa: E402
    COMPANY_FOREST,
    OVERHEAD_PCT_DEFAULT,
    SIRIUS_STANDARD_SECTIONS,
    build_timber_kp_context,
    calc_totals,
    generate_timber_kp_pdf,
)


class TimberKpTests(unittest.TestCase):
    def test_knowledge_base_files(self):
        standards = load_timber_standards()
        template = load_timber_kp_template()
        company = load_timber_company_forest()
        self.assertIn("клееного бруса", standards)
        self.assertIn("Не использовать", standards)
        self.assertIn("extends \"base_kp.html\"", template)
        self.assertIn("company_legal", template)
        self.assertIn("Дома из клееного бруса", company)
        self.assertIn("клееного бруса", company)

    def test_protocol_19_08_parses_to_timber_kp(self):
        from transcript_parser_local import parse_transcript_local
        from etalon_score import etalon_match_score

        text = (
            ROOT / "knowledge_base" / "timber" / "demo_protocol_19_08.txt"
        ).read_text(encoding="utf-8")
        parsed = parse_transcript_local(text)
        self.assertEqual(parsed.get("client_name"), "Дмитрий")
        self.assertTrue(parsed.get("client_phone"))
        self.assertIn("@", parsed.get("client_email") or "")
        self.assertIn("брус", (parsed.get("material") or "").lower())
        self.assertEqual(parsed.get("catalog_project"), "Сириус 2.0")
        self.assertIn("200", parsed.get("area") or "")
        self.assertTrue(parsed.get("area"))
        self.assertTrue(parsed.get("plot"))
        self.assertTrue(parsed.get("timeline"))
        self.assertTrue(parsed.get("funding_source"))
        self.assertIn("собствен", (parsed.get("funding_source") or "").lower())
        match = etalon_match_score(parsed)
        self.assertGreaterEqual(match["score"], 80)
        self.assertTrue(match["can_generate_kp"])

        ctx = build_timber_kp_context(
            client_name=parsed["client_name"],
            project_name=parsed.get("catalog_project") or "Индивидуальный жилой дом из клееного бруса",
            protocol_number="19/08",
        )
        self.assertEqual(ctx["grand_total"], 13_145_075)
        self.assertEqual(ctx["company_name"], "Дома из клееного бруса")

    def test_sirius_totals_match_mock(self):
        subtotal, overhead, grand = calc_totals(SIRIUS_STANDARD_SECTIONS, OVERHEAD_PCT_DEFAULT)
        self.assertEqual(subtotal, 12_519_119)
        self.assertEqual(overhead, 625_956)
        self.assertEqual(grand, 13_145_075)

    def test_context_uses_company_variables(self):
        ctx = build_timber_kp_context(
            client_name="Иван Петров",
            project_name="Сириус 2.0",
            watermark="draft",
        )
        self.assertEqual(ctx["company_name"], COMPANY_FOREST["company_name"])
        self.assertEqual(ctx["client_name"], "Иван Петров")
        self.assertEqual(ctx["project_name"], "Сириус 2.0")
        self.assertEqual(ctx["grand_total"], 13_145_075)
        self.assertEqual(ctx["selected_variant"], "Стандарт")
        self.assertEqual(len(ctx["sections"]), 7)
        self.assertNotIn("Дом-Мастер", ctx["company_legal"])
        self.assertNotIn("75 000", ctx["company_tagline"])

    def test_html_renders_forest_not_dommaster_rate(self):
        ctx = build_timber_kp_context(client_name="Тест", project_name="Сириус 2.0")
        html = render_html(ctx, template_name="kp_timber_template.html")
        self.assertIn("Дома из клееного бруса", html)
        self.assertIn("клееного бруса", html)
        self.assertIn("13 145 075", html)
        self.assertIn("Сириус 2.0", html)
        self.assertNotIn("75 000", html)
        self.assertIn("header-logo", html)
        self.assertIn("dom_forest/logo.png", html)
        self.assertIn("vk.com/domforest43", html)
        self.assertIn("t.me/domforest43", html)
        self.assertIn("wa.me/74998775533", html)
        self.assertIn("dzen.ru/domforest43", html)
        self.assertIn("youtube.com/channel", html)
        self.assertIn("contacts-brand", html)
        self.assertIn("+7 (499) 877-55-33", html)

    def test_pdf_generate(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = generate_timber_kp_pdf(
                output_path=Path(tmp) / "kp_timber_test.pdf",
                client_name="Тест",
                project_name="Сириус 2.0",
                watermark="draft",
            )
            pdf = Path(meta["pdf_path"])
            self.assertTrue(pdf.is_file())
            self.assertGreater(pdf.stat().st_size, 5_000)
            self.assertEqual(meta["grand_total"], 13_145_075)


if __name__ == "__main__":
    unittest.main()
