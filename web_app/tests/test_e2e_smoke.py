"""E2E smoke CRM: login → deals → create → generate KP (без AI) → PDF → approve."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(ROOT))

FULL_TRANSCRIPT = """
ПРОТОКОЛ ТЕЛЕФОННОГО РАЗГОВОРА
Клиент: Тест E2E
Телефон: +7 900 111-22-33
Email: e2e@example.com
Участок: 10 соток, Дмитровское шоссе
Площадь: 150 м²
Материал: газобетон
Сроки: август 2026
Финансирование: ипотека
"""


def _load_crm_app():
    """Загружает web_app/app.py, не пакет app/ из корня репо."""
    path = WEB / "app.py"
    spec = importlib.util.spec_from_file_location("crm_web_app", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["crm_web_app"] = mod
    spec.loader.exec_module(mod)
    return mod


class CrmE2ESmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.cwd = Path(cls._tmpdir.name)
        (cls.cwd / "reports" / "kp" / "stroika").mkdir(parents=True)
        cls._old_cwd = os.getcwd()
        os.chdir(cls.cwd)

        cls._env = mock.patch.dict(
            os.environ,
            {
                "ETALON_KP_THRESHOLD": "80",
                "SECRET_KEY": "e2e-test-secret",
                "OPENAI_API_KEY": "sk-test-not-used",
            },
            clear=False,
        )
        cls._env.start()

        cls.crm = _load_crm_app()
        # чистая БД: init_db создаст полную схему
        if Path("deals.db").exists():
            Path("deals.db").unlink()
        # обходим chicken-egg ensure_deal_columns на пустом файле
        bootstrap = sqlite3.connect("deals.db")
        bootstrap.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT,
                client_phone TEXT,
                client_email TEXT,
                client_telegram TEXT,
                transcript TEXT,
                kp_options TEXT,
                ar_pdf TEXT,
                ir_pdf TEXT,
                delivery_method TEXT,
                delivery_date TIMESTAMP,
                status TEXT DEFAULT 'new',
                last_reminder TIMESTAMP,
                next_action_date TIMESTAMP,
                notes TEXT,
                user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
            """
        )
        bootstrap.commit()
        bootstrap.close()
        cls.crm.init_db()
        cls.app = cls.crm.app
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._env.stop()
        os.chdir(cls._old_cwd)
        cls._tmpdir.cleanup()

    def test_01_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get("status"), "ok")
        r2 = self.client.get("/health?deep=1")
        self.assertEqual(r2.status_code, 200)
        body = r2.get_json()
        self.assertEqual(body.get("service"), "dommaster-crm")
        self.assertEqual((body.get("checks") or {}).get("db"), "ok")

    def test_01b_ingest_endpoint_exists(self):
        r = self.client.post("/ingest", json={})
        self.assertIn(r.status_code, (400, 401))
        self.assertIn(r.get_json().get("error"), ("transcript is required", "unauthorized"))

    def test_02_login_and_pages(self):
        r = self.client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303))
        r = self.client.get("/deals/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Список сделок".encode("utf-8"), r.data)
        r = self.client.get("/help")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Справка".encode("utf-8"), r.data)
        r = self.client.get("/dashboard")
        self.assertEqual(r.status_code, 200)

    def test_03_create_deal_generate_approve_pdf(self):
        # логин
        self.client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=True,
        )
        # создать сделку
        r = self.client.post(
            "/deals/new",
            data={
                "client_name": "Тест E2E",
                "client_phone": "+7 900 111-22-33",
                "client_email": "e2e@example.com",
                "plot": "10 соток, Дмитровское",
                "area": "150",
                "material": "газобетон",
                "timeline": "август 2026",
                "funding_source": "ипотека",
                "transcript": FULL_TRANSCRIPT,
            },
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303))
        # id из БД
        conn = sqlite3.connect("deals.db")
        deal_id = conn.execute("SELECT id FROM deals ORDER BY id DESC LIMIT 1").fetchone()[0]
        conn.close()
        self.assertGreaterEqual(deal_id, 1)

        # карточка
        r = self.client.get(f"/deals/{deal_id}")
        self.assertEqual(r.status_code, 200)

        # генерация КП без AI
        r = self.client.post(
            f"/deals/{deal_id}/generate-kp",
            json={"watermark": "draft", "use_ai": False},
        )
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:500])
        data = r.get_json()
        self.assertIn(data.get("status"), ("ok", "success"))

        # PDF
        r = self.client.get(f"/deals/{deal_id}/kp.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data[:4] == b"%PDF" or r.mimetype == "application/pdf")

        # утверждение
        r = self.client.post(f"/deals/{deal_id}/approve-kp", json={})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:500])
        self.assertIn(r.get_json().get("status"), ("ok", "success"))

        # смена статуса
        r = self.client.post(f"/deals/{deal_id}/status", json={"status": "kp_sent"})
        self.assertEqual(r.status_code, 200)

    def test_04_create_from_transcript_only(self):
        """Менеджер вставляет протокол и жмёт «Создать» — без телефона/email в форме."""
        self.client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=True,
        )
        page = self.client.get("/deals/new")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertNotRegex(html, r'name="client_phone"[^>]*\srequired')
        self.assertNotRegex(html, r'name="client_email"[^>]*\srequired')

        r = self.client.post(
            "/deals/new",
            data={"transcript": FULL_TRANSCRIPT},
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303), r.get_data(as_text=True)[:400])
        conn = sqlite3.connect("deals.db")
        row = conn.execute(
            "SELECT client_phone, client_email, area, status FROM deals ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertTrue(row[0], "телефон должен взяться из протокола")
        self.assertIn("@", row[1] or "")
        self.assertTrue(row[2])
        self.assertEqual(row[3], "new")

    def test_05_delete_one_deal_keeps_others(self):
        self.client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=True,
        )
        self.client.post("/deals/new", data={"transcript": FULL_TRANSCRIPT})
        self.client.post(
            "/deals/new",
            data={
                "client_name": "Вторая",
                "transcript": FULL_TRANSCRIPT.replace("Тест E2E", "Вторая"),
            },
        )
        conn = sqlite3.connect("deals.db")
        ids = [r[0] for r in conn.execute("SELECT id FROM deals ORDER BY id").fetchall()]
        conn.close()
        self.assertGreaterEqual(len(ids), 2)
        victim, keeper = ids[-2], ids[-1]

        r = self.client.post(f"/deals/{victim}/delete", json={})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:400])
        self.assertEqual(r.get_json().get("status"), "success")

        conn = sqlite3.connect("deals.db")
        left = [r[0] for r in conn.execute("SELECT id FROM deals ORDER BY id").fetchall()]
        conn.close()
        self.assertNotIn(victim, left)
        self.assertIn(keeper, left)

        r = self.client.get(f"/deals/{victim}")
        self.assertIn(r.status_code, (302, 303, 404))

    def test_06_manager_cannot_delete_deal(self):
        conn = sqlite3.connect("deals.db")
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password, name) VALUES (?, ?, ?)",
            ("manager", hashlib.sha256(b"manager123").hexdigest(), "Менеджер ОП"),
        )
        conn.commit()
        conn.close()

        self.client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=True,
        )
        self.client.post("/deals/new", data={"transcript": FULL_TRANSCRIPT})
        conn = sqlite3.connect("deals.db")
        deal_id = conn.execute("SELECT id FROM deals ORDER BY id DESC LIMIT 1").fetchone()[0]
        conn.close()

        self.client.get("/logout")
        r = self.client.post(
            "/login",
            data={"username": "manager", "password": "manager123"},
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303))

        page = self.client.get("/deals/")
        html = page.get_data(as_text=True)
        self.assertNotIn('onclick="clearAllDeals()"', html)
        self.assertNotIn('onclick="loadDemoData()"', html)
        self.assertIn("canDeleteDeals: false", html)

        r = self.client.post(f"/deals/{deal_id}/delete", json={})
        self.assertEqual(r.status_code, 403)
        body = r.get_json()
        self.assertEqual(body.get("status"), "error")
        self.assertIn("администратору", (body.get("message") or "").lower())

        conn = sqlite3.connect("deals.db")
        still = conn.execute("SELECT id FROM deals WHERE id = ?", (deal_id,)).fetchone()
        conn.close()
        self.assertIsNotNone(still)

    def test_07_timber_protocol_full_pipeline(self):
        protocol = (
            ROOT / "knowledge_base" / "timber" / "demo_protocol_19_08.txt"
        ).read_text(encoding="utf-8")
        self.client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=True,
        )
        r = self.client.post(
            "/deals/new",
            data={"transcript": protocol},
            follow_redirects=False,
        )
        self.assertIn(r.status_code, (302, 303), r.get_data(as_text=True)[:400])
        conn = sqlite3.connect("deals.db")
        row = conn.execute(
            "SELECT id, client_name, material, area, tk_cost, catalog_project FROM deals ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        deal_id, name, material, area, tk_cost, catalog_project = row
        self.assertIn("Дмитрий", name or "")
        self.assertIn("брус", (material or "").lower())
        self.assertIn("200", area or "")
        self.assertEqual(catalog_project, "Сириус 2.0")
        self.assertTrue(area)
        self.assertIsNone(tk_cost)

        r = self.client.post(
            f"/deals/{deal_id}/generate-kp",
            json={"watermark": "draft", "use_ai": False},
        )
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:600])
        data = r.get_json()
        self.assertEqual(data.get("status"), "success")
        kp = data.get("kp") or {}
        self.assertEqual(kp.get("kp_kind"), "timber")
        self.assertEqual(kp.get("grand_total"), 13_145_075)
        self.assertIn("клееный брус", (data.get("message") or "").lower())
        self.assertNotIn("75 000", data.get("message") or "")

        r = self.client.get(f"/deals/{deal_id}/kp.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data[:4] == b"%PDF" or r.mimetype == "application/pdf")

        page = self.client.get(f"/deals/{deal_id}?tab=kp")
        self.assertEqual(page.status_code, 200, page.get_data(as_text=True)[:800])
        html = page.get_data(as_text=True)
        self.assertIn("клееного бруса", html.lower())
        self.assertIn("13 145 075", html)
        self.assertIn("Сириус 2.0", html)
        self.assertNotIn("Internal Server Error", html)

        r = self.client.post(f"/deals/{deal_id}/approve-kp", json={})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:500])
        approved = r.get_json().get("kp") or {}
        self.assertEqual(approved.get("watermark"), "approved")
        self.assertEqual(approved.get("kp_kind"), "timber")


if __name__ == "__main__":
    unittest.main()
