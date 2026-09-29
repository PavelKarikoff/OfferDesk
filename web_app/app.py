import os
import json
import sqlite3
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from functools import wraps
from datetime import datetime
import hashlib

from dotenv import load_dotenv

# Подхватываем .env из корня репо и web_app/ (на VPS web_app/.env → ../.env)
_WEB_DIR = Path(__file__).resolve().parent
load_dotenv(_WEB_DIR.parent / '.env')
load_dotenv(_WEB_DIR / '.env')

try:
    from utils.config import apply_outbound_proxy_env

    _proxy = apply_outbound_proxy_env()
    if _proxy:
        print(f'OpenAI/outbound proxy: {_proxy.split("@")[-1]}')
except Exception:
    pass

from routes_deals import deals_bp
from routes_admin import admin_bp
from routes_ingest import ingest_bp
from db_utils import connect_db
from etalon_score import etalon_match_score
from pricing import PRICE_PER_M2, apply_tk_cost
from authz import is_service_admin


app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')


@app.context_processor
def inject_pricing():
    """Ставка тёплого контура, каталог бруса и роль во всех шаблонах CRM."""
    try:
        from utils.timber_catalog import CATALOG_PROJECTS, catalog_label
        timber_catalog = CATALOG_PROJECTS
    except Exception:
        timber_catalog = ()
        catalog_label = lambda p: p.get("name", "")  # noqa: E731
    return {
        "price_per_m2": PRICE_PER_M2,
        "price_per_m2_fmt": f"{PRICE_PER_M2:,}".replace(",", "\u00a0"),
        "is_admin": is_service_admin(),
        "timber_catalog": timber_catalog,
        "catalog_label": catalog_label,
    }


@app.template_filter('from_json')
def from_json_filter(value):
    """Парсит JSON-строку в шаблонах: {{ deal.transcript_data|from_json }}."""
    if value is None or value == '':
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        data = json.loads(value)
        return data if isinstance(data, (dict, list)) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


# === База данных ===
def get_db():
    return connect_db('deals.db')

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица сделок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deals (
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
        )
    ''')
    conn.commit()
    # ensure extra columns (budget, plot, ...)
    from db_utils import ensure_deal_columns
    ensure_deal_columns(conn)
    
    # Добавляем тестового пользователя (если нет)
    cursor.execute('SELECT * FROM users WHERE username = "admin"')
    if not cursor.fetchone():
        password_hash = hashlib.sha256('admin123'.encode()).hexdigest()
        cursor.execute(
            'INSERT INTO users (username, password, name) VALUES (?, ?, ?)',
            ('admin', password_hash, 'Администратор')
        )
    
    conn.commit()
    conn.close()


def _deal_with_etalon(row: sqlite3.Row) -> dict:
    from models import normalize_status, status_label

    deal = dict(row)
    deal["status"] = normalize_status(deal.get("status"))
    deal["status_label"] = status_label(deal["status"])
    match = etalon_match_score(deal)
    deal["etalon_score"] = match["score"]
    deal["etalon_grade"] = match["grade"]
    deal["etalon_missing"] = match["missing"]
    apply_tk_cost(deal)
    return deal

# === Декоратор для проверки авторизации ===
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# === Маршруты ===
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            return render_template('login.html', error='Введите логин и пароль')
        
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        conn = get_db()
        user = conn.execute(
            'SELECT * FROM users WHERE username = ? AND password = ?',
            (username, password_hash)
        ).fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['user_name'] = user['name']
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Неверный логин или пароль')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/help')
@login_required
def help_page():
    from etalon_score import KP_THRESHOLD

    return render_template('help.html', kp_threshold=KP_THRESHOLD)

@app.route('/dashboard')
@login_required
def dashboard():
    from analytics import build_dashboard_stats

    conn = get_db()
    rows = conn.execute('''
        SELECT * FROM deals 
        WHERE user_id = ? OR user_id IS NULL
        ORDER BY created_at DESC
    ''', (session['user_id'],)).fetchall()
    conn.close()
    deals = [_deal_with_etalon(row) for row in rows]
    stats = build_dashboard_stats(deals)

    return render_template(
        'dashboard.html',
        username=session.get('user_name') or session.get('username'),
        deals=deals,
        stats=stats,
        chart=stats.get('chart') or {'labels': [], 'counts': []},
        funnel=stats.get('funnel') or {},
        status_counts=stats.get('status_counts') or {},
    )

@app.route('/deal/<int:deal_id>')
@login_required
def deal_detail(deal_id):
    """Совместимость: старый URL → карточка сделки в blueprint."""
    return redirect(url_for('deals.deal_detail', deal_id=deal_id))

@app.route('/health')
def health():
    """Liveness. ?deep=1 — проверка SQLite и наличия OPENAI_PROXY."""
    deep = request.args.get('deep', '').strip().lower() in {'1', 'true', 'yes'}
    payload = {"status": "ok", "service": "dommaster-crm"}
    if not deep:
        return jsonify(payload)

    checks: dict = {}
    try:
        conn = get_db()
        conn.execute('SELECT 1').fetchone()
        conn.close()
        checks['db'] = 'ok'
    except Exception as exc:  # noqa: BLE001 — health не должен падать
        checks['db'] = f'fail:{type(exc).__name__}'
        payload['status'] = 'degraded'

    proxy = (
        os.getenv('OPENAI_PROXY', '').strip()
        or os.getenv('HTTPS_PROXY', '').strip()
        or os.getenv('HTTP_PROXY', '').strip()
    )
    checks['openai_proxy'] = 'configured' if proxy else 'missing'
    if not proxy:
        # не degraded: CRM жив без прокси, но мониторинг видит пробел
        checks['openai_proxy_hint'] = 'OPENAI_PROXY пуст'

    payload['checks'] = checks
    code = 200 if payload['status'] == 'ok' else 503
    return jsonify(payload), code

# === Инициализация и запуск ===
app.register_blueprint(deals_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(ingest_bp)

if __name__ == '__main__':
    init_db()
    # Waitress стабильнее встроенного сервера Flask на VPS (меньше обрывов соединений).
    try:
        from waitress import serve

        print('Starting Waitress on 0.0.0.0:5001')
        # channel_timeout: генерация КП + OpenAI через прокси может занимать >60с
        serve(
            app,
            host='0.0.0.0',
            port=5001,
            threads=8,
            channel_timeout=180,
            connection_limit=100,
        )
    except ImportError:
        app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
