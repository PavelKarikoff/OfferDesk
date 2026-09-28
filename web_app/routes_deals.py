import os
import re
import sys
import json
import sqlite3
import logging
from pathlib import Path
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    flash,
    send_file,
)
from functools import wraps
from werkzeug.utils import secure_filename
from file_parser import extract_text_from_file
from etalon_score import etalon_match_score, KP_THRESHOLD, FIELD_QUESTIONS, etalon_fields_for
from db_utils import connect_db
from pricing import apply_tk_cost, calc_tk_cost, is_timber_material
from models import (
    DEAL_STATUSES,
    STATUS_LABELS,
    list_actions,
    log_action,
    normalize_status,
    status_after_etalon,
    status_after_kp_ready,
    status_after_kp_sent,
    status_label,
)
from authz import DENY_DELETE_MSG, is_service_admin

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

deals_bp = Blueprint('deals', __name__, url_prefix='/deals')


def _actor_id():
    return session.get('user_id')


def _parse_kp_options(raw) -> dict | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _safe_kp_pdf_path(pdf_path: str | Path) -> Path | None:
    """Разрешает путь к PDF только внутри reports/kp."""
    try:
        path = Path(pdf_path).resolve()
        allowed = (PROJECT_ROOT / "reports" / "kp").resolve()
        path.relative_to(allowed)
    except (OSError, ValueError, TypeError):
        return None
    if not path.is_file() or path.suffix.lower() != ".pdf":
        return None
    return path


def _remove_deal_kp_files(deal: dict) -> None:
    """Удаляет PDF КП сделки, если файл лежит в reports/kp."""
    meta = _parse_kp_options(deal.get("kp_options"))
    pdf_path = (meta or {}).get("pdf_path")
    path = _safe_kp_pdf_path(pdf_path) if pdf_path else None
    if not path:
        return
    try:
        path.unlink(missing_ok=True)
        folder = path.parent
        allowed = (PROJECT_ROOT / "reports" / "kp").resolve()
        folder.resolve().relative_to(allowed)
        if folder.is_dir() and folder.name.startswith("deal_") and not any(folder.iterdir()):
            folder.rmdir()
    except (OSError, ValueError):
        logger.warning("Не удалось удалить файл КП сделки %s", deal.get("id"))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


_EXTRACTION_SOURCES = frozenset({"llm", "regex", "merged"})
_EXTRACTION_SOURCE_RE = re.compile(r"источник\s+(llm|regex|merged)\b")


def _latest_extraction_source(timeline: list | None) -> str:
    """Последний канал разбора из журнала, если колонка сделки ещё пустая."""
    for item in timeline or []:
        match = _EXTRACTION_SOURCE_RE.search((item or {}).get("detail") or "")
        if match:
            return match.group(1)
    return ""


def _parse_transcript_for_crm(transcript: str, overrides: dict | None = None) -> tuple[dict, object, str]:
    """Пайплайн extraction → (CRM-словарь, DealExtraction, source).

    Эталон и статус считаются снаружи. extracted и source нужны для
    валидации, эскалации, lead scoring и аудита.
    """
    from extraction.crm_adapter import to_crm_dict
    from extraction.pipeline import extract

    extracted, source = extract(transcript)
    return to_crm_dict(extracted, overrides, source=source), extracted, source


def _missing_required_contacts(phone: str | None, email: str | None) -> list[str]:
    """Телефон и email обязательны; email — основной канал отправки КП."""
    missing: list[str] = []
    if not (phone or '').strip():
        missing.append('телефон')
    em = (email or '').strip()
    if not em or '@' not in em:
        missing.append('email')
    return missing


def get_db():
    return connect_db('deals.db')

def _deal_with_etalon(row: sqlite3.Row) -> dict:
    deal = dict(row)
    deal["status"] = normalize_status(deal.get("status"))
    deal["status_label"] = status_label(deal["status"])
    match = etalon_match_score(deal)
    deal["etalon_score"] = match["score"]
    deal["etalon_grade"] = match["grade"]
    deal["etalon_missing"] = match["missing"]
    deal["etalon_questions"] = match["questions"]
    deal["can_generate_kp"] = match["can_generate_kp"]
    deal["is_complete"] = match["is_complete"]
    deal["etalon_threshold"] = match["threshold"]
    apply_tk_cost(deal)
    return deal


def _maybe_run_reminders(conn: sqlite3.Connection, *, notify: bool = True) -> dict | None:
    """Тихая проверка зависших сделок (с cooldown на уровне сделок)."""
    try:
        from utils.reminders import process_reminders

        return process_reminders(conn, notify=notify)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reminders skipped: %s", exc)
        return None

@deals_bp.route('/')
@login_required
def list_deals():
    completion_status = request.args.get('completion_status', '').strip()
    status_filter = request.args.get('status', '').strip()
    search = request.args.get('search', '').strip()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    per_page = 20

    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM deals WHERE user_id = ? OR user_id IS NULL ORDER BY created_at DESC',
        (session['user_id'],),
    ).fetchall()
    reminder_info = _maybe_run_reminders(conn, notify=True)
    conn.close()

    if reminder_info and reminder_info.get('due'):
        flash(
            f"Напоминание: {reminder_info['due']} сделок без действий > 3 дней"
            + (f" (Telegram: {reminder_info.get('notified', 0)})" if reminder_info.get('notified') else ''),
            'warning',
        )

    deals = [_deal_with_etalon(row) for row in rows]

    if status_filter:
        status_filter = normalize_status(status_filter)
        deals = [d for d in deals if (d.get('status') or '') == status_filter]

    if completion_status == 'high':
        deals = [d for d in deals if d.get('etalon_score', 0) >= KP_THRESHOLD]
    elif completion_status == 'medium':
        deals = [
            d for d in deals
            if 50 <= d.get('etalon_score', 0) < KP_THRESHOLD
        ]
    elif completion_status == 'low':
        deals = [d for d in deals if d.get('etalon_score', 0) < 50]

    if search:
        q = search.lower()
        def _match(deal: dict) -> bool:
            hay = ' '.join(
                str(deal.get(k) or '')
                for k in (
                    'client_name', 'client_phone', 'client_email',
                    'client_telegram', 'plot', 'notes',
                )
            ).lower()
            return q in hay
        deals = [d for d in deals if _match(d)]

    total = len(deals)
    ready = sum(1 for d in deals if d.get('can_generate_kp'))
    incomplete = total - ready
    avg_score = int(round(sum(d.get('etalon_score', 0) for d in deals) / total)) if total else 0
    stats = {
        'total': total,
        'ready': ready,
        'incomplete': incomplete,
        'avg_score': avg_score,
    }

    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    start = (page - 1) * per_page
    page_deals = deals[start:start + per_page]

    def _page_url(p: int) -> str:
        return url_for(
            'deals.list_deals',
            completion_status=completion_status or None,
            status=status_filter or None,
            search=search or None,
            page=p,
        )

    pagination = {
        'current_page': page,
        'pages': pages,
        'has_prev': page > 1,
        'has_next': page < pages,
        'prev_url': _page_url(page - 1) if page > 1 else None,
        'next_url': _page_url(page + 1) if page < pages else None,
        'pages_range': list(range(1, pages + 1)),
        'url_for_page': _page_url,
    }

    return render_template(
        'deals/list.html',
        deals=page_deals,
        kp_threshold=KP_THRESHOLD,
        completion_status=completion_status,
        status_filter=status_filter,
        search=search,
        stats=stats,
        pagination=pagination,
        deal_statuses=DEAL_STATUSES,
        status_labels=STATUS_LABELS,
    )

@deals_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_deal():
    if request.method == 'POST':
        file = request.files.get('file')
        transcript = request.form.get('transcript', '').strip()

        if file and file.filename:
            filename = secure_filename(file.filename)
            upload_dir = '/tmp/uploads'
            os.makedirs(upload_dir, exist_ok=True)
            file_path = os.path.join(upload_dir, filename)
            file.save(file_path)
            transcript = extract_text_from_file(file_path) or transcript

        if not transcript:
            flash('Введите текст транскрибации или загрузите файл', 'error')
            return redirect(request.url)

        overrides = {
            'client_name': request.form.get('client_name', '').strip(),
            'client_phone': request.form.get('client_phone', '').strip(),
            'client_email': request.form.get('client_email', '').strip(),
            'client_telegram': request.form.get('client_telegram', '').strip(),
            'plot': request.form.get('plot', '').strip(),
            'budget': request.form.get('budget', '').strip(),
            'area': request.form.get('area', '').strip(),
            'material': request.form.get('material', '').strip(),
            'timeline': request.form.get('timeline', '').strip(),
            'funding_source': request.form.get('funding_source', '').strip(),
            'catalog_project': request.form.get('catalog_project', '').strip(),
        }
        notes = request.form.get('notes', '').strip()

        extracted = None
        source = ""
        try:
            parsed_data, extracted, source = _parse_transcript_for_crm(transcript, overrides)
            validation = etalon_match_score(parsed_data)
            logger.info(
                "=== PARSED DATA ===\n%s\n=== ETALON %s%% missing=%s source=%s ===",
                json.dumps(parsed_data, indent=2, ensure_ascii=False),
                validation['score'],
                validation['missing'],
                parsed_data.get('extraction_source'),
            )
        except Exception as e:
            logger.error(f"Ошибка парсинга/валидации: {e}")
            parsed_data = {}
            validation = etalon_match_score({})

        client_name = parsed_data.get('client_name')
        client_phone = parsed_data.get('client_phone')
        client_email = parsed_data.get('client_email')
        client_telegram = parsed_data.get('client_telegram')

        # Телефон/email не блокируют создание: парсер берёт их из протокола,
        # иначе сделка уходит в «Неполные данные» (эталон).
        plot = parsed_data.get('plot')
        budget = parsed_data.get('budget')  # необязательно (если клиент назвал)
        area = parsed_data.get('area')
        material = parsed_data.get('material')
        timeline = parsed_data.get('timeline')
        funding_source = parsed_data.get('funding_source')
        catalog_project = parsed_data.get('catalog_project')
        tk_cost = None if is_timber_material(material) else calc_tk_cost(area)
        initial_status = status_after_etalon(
            can_generate_kp=bool(validation.get('can_generate_kp')),
        )

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO deals (
                client_name, client_phone, client_email, client_telegram,
                transcript, notes, user_id, status,
                plot, budget, area, material, timeline, funding_source, tk_cost, catalog_project,
                extraction_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            client_name, client_phone, client_email, client_telegram,
            transcript, notes, session['user_id'], initial_status,
            plot, budget, area, material, timeline, funding_source, tk_cost, catalog_project,
            parsed_data.get('extraction_source') or '',
        ))
        deal_id = cursor.lastrowid
        log_action(
            conn,
            deal_id=deal_id,
            action='created',
            detail=(
                f'Статус: {status_label(initial_status)}; '
                f'эталон {validation.get("score", 0)}%; '
                f'источник {parsed_data.get("extraction_source") or "—"}'
            ),
            user_id=_actor_id(),
        )
        # Аудит + контроль качества (только если парсер отработал)
        if extracted is not None:
            from extraction.validation.rules import validate
            from extraction.validation.escalation import escalate
            from extraction.scoring.lead_score import score as lead_score
            from extraction.audit.logger import log_extraction

            _validation = validate(extracted)
            _escalation = escalate(extracted, _validation, raw_text=transcript)
            _lead = lead_score(extracted)
            log_extraction(
                conn,
                deal_id=deal_id,
                extraction=extracted,
                source=source,
                validation=_validation,
                escalation=_escalation,
                lead=_lead,
            )
        conn.commit()
        conn.close()

        score = validation.get('score', 0)
        if not validation.get('is_complete'):
            if score >= KP_THRESHOLD:
                flash(
                    f'Сделка создана. Заполнение {score}% — КП можно генерировать, '
                    f'но лучше дособрать недостающие данные.',
                    'warning',
                )
            elif score >= 50:
                flash(
                    f'Сделка создана. Заполнение {score}% — рекомендуем дособрать данные '
                    f'до {KP_THRESHOLD}% перед генерацией КП.',
                    'warning',
                )
            else:
                flash(
                    f'Сделка создана. Заполнение {score}% — данных недостаточно для КП. '
                    f'Нужно уточнить недостающие поля.',
                    'error',
                )
            return redirect(url_for('deals.incomplete_data', deal_id=deal_id))

        flash('Сделка создана! Данные соответствуют эталону на 100%.', 'success')
        return redirect(url_for('deals.deal_detail', deal_id=deal_id))

    return render_template('deal_form.html', kp_threshold=KP_THRESHOLD)

@deals_bp.route('/<int:deal_id>')
@login_required
def deal_detail(deal_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
    if not row:
        conn.close()
        flash('Сделка не найдена', 'error')
        return redirect(url_for('deals.list_deals'))

    deal = _deal_with_etalon(row)
    timeline = list_actions(conn, deal_id, limit=80)

    # Аудит — до закрытия соединения
    from extraction.audit.logger import get_last_audit
    audit = get_last_audit(conn, deal_id) or {}

    conn.close()

    # data для шаблона: алиасы парсера + % заполнения (порог КП)
    data = {
        'name': deal.get('client_name') or '',
        'phone': deal.get('client_phone') or '',
        'email': deal.get('client_email') or '',
        'telegram': deal.get('client_telegram') or '',
        'plot_size': deal.get('plot') or '',
        'budget': deal.get('budget') or '',
        'area': deal.get('area') or '',
        'material': deal.get('material') or '',
        'deadline': deal.get('timeline') or '',
        'financing': deal.get('funding_source') or '',
        'completion_percent': deal.get('etalon_score', 0),
        'is_complete': deal.get('can_generate_kp', False),
        'missing_fields_names': deal.get('etalon_missing') or [],
        'extraction_source': (
            deal.get('extraction_source')
            if deal.get('extraction_source') in _EXTRACTION_SOURCES
            else _latest_extraction_source(timeline)
        ),
        **audit,
    }

    field_rows = [
        {'key': 'client_name', 'label': 'Имя клиента', 'value': deal.get('client_name')},
    ]
    for key, label in etalon_fields_for(deal):
        value = deal.get(key)
        text = str(value).strip() if value is not None else ''
        if text in {'', '—', '-', 'None', 'null'}:
            text = ''
        field_rows.append({'key': key, 'label': label, 'value': text or None})

    # Автополе по стандарту компании (не входит в % эталона)
    field_rows.append({
        'key': 'tk_cost',
        'label': 'Стоимость ТК',
        'value': deal.get('tk_cost_fmt') or None,
    })
    if deal.get('budget'):
        field_rows.append({
            'key': 'budget',
            'label': 'Бюджет клиента (необяз.)',
            'value': deal.get('budget'),
        })

    kp_meta = _parse_kp_options(deal.get('kp_options'))

    from mailer import smtp_configured
    from telegram_send import resolve_deal_chat_id, telegram_configured
    from utils.crm_telegram import client_bind_link

    chat_ready = bool(resolve_deal_chat_id(deal))
    bind_link = client_bind_link(deal["id"]) if not chat_ready else None

    from analytics import collect_deal_files

    tab = (request.args.get("tab") or "main").strip().lower()
    if tab not in {"main", "kp", "history", "files"}:
        tab = "main"

    return render_template(
        'deals/view.html',
        deal=deal,
        data=data,
        field_rows=field_rows,
        kp_threshold=KP_THRESHOLD,
        kp_meta=kp_meta,
        smtp_ready=smtp_configured(),
        telegram_ready=telegram_configured(),
        telegram_chat_ready=chat_ready,
        telegram_bind_link=bind_link,
        timeline=timeline,
        deal_statuses=DEAL_STATUSES,
        status_labels=STATUS_LABELS,
        deal_files=collect_deal_files(deal, kp_meta),
        active_tab=tab,
    )

@deals_bp.route('/<int:deal_id>/incomplete')
@login_required
def incomplete_data(deal_id):
    """Страница «Недостающие данные» для менеджера."""
    conn = get_db()
    row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
    conn.close()
    if not row:
        flash('Сделка не найдена', 'error')
        return redirect(url_for('deals.list_deals'))

    deal = _deal_with_etalon(row)
    match = etalon_match_score(deal)
    missing_items = [
        {
            'key': key,
            'label': label,
            'question': FIELD_QUESTIONS.get(key, ''),
        }
        for key, label in etalon_fields_for(deal)
        if key in match['missing_keys']
    ]

    return render_template(
        'deals/incomplete_data.html',
        deal=deal,
        missing_items=missing_items,
        kp_threshold=KP_THRESHOLD,
        validation=match,
    )

@deals_bp.route('/<int:deal_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_deal(deal_id):
    """Редактирование сделки со всеми полями эталона."""
    conn = get_db()
    row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
    conn.close()
    if not row:
        flash('Сделка не найдена', 'error')
        return redirect(url_for('deals.list_deals'))

    deal = _deal_with_etalon(row)

    if request.method == 'POST':
        # Основные поля
        client_name = request.form.get('client_name', deal['client_name'] or '').strip()
        client_phone = request.form.get('client_phone', deal['client_phone'] or '').strip()
        client_email = request.form.get('client_email', deal['client_email'] or '').strip()
        client_telegram = (
            request.form.get('client_telegram')
            or request.form.get('telegram')
            or deal['client_telegram']
            or ''
        ).strip()
        status = request.form.get('status', deal['status'] or 'new').strip() or 'new'
        notes = request.form.get('notes', deal['notes'] or '').strip()
        transcript = request.form.get('transcript', deal['transcript'] or '').strip()

        # Параметры строительства (алиасы из чернового API тоже принимаем)
        plot = (
            request.form.get('plot')
            or request.form.get('plot_size')
            or deal['plot']
            or ''
        ).strip()
        budget = request.form.get('budget', deal['budget'] or '').strip()
        area = request.form.get('area', deal['area'] or '').strip()
        material = request.form.get('material', deal['material'] or '').strip()
        timeline = (
            request.form.get('timeline')
            or request.form.get('deadline')
            or deal['timeline']
            or ''
        ).strip()
        funding_source = (
            request.form.get('funding_source')
            or request.form.get('financing')
            or deal['funding_source']
            or ''
        ).strip()
        catalog_project = (
            request.form.get('catalog_project')
            or deal.get('catalog_project')
            or ''
        ).strip()

        # Если менеджер дополнил транскрибацию — перепарсить.
        # Уже введённые поля уходят в overrides и не затираются.
        extraction_source = ''
        _extracted = None
        _source = ''
        if transcript and transcript != (deal['transcript'] or ''):
            try:
                reparsed, _extracted, _source = _parse_transcript_for_crm(transcript, {
                    'client_name': client_name,
                    'client_phone': client_phone,
                    'client_email': client_email,
                    'client_telegram': client_telegram,
                    'plot': plot,
                    'budget': budget,
                    'area': area,
                    'material': material,
                    'timeline': timeline,
                    'funding_source': funding_source,
                    'catalog_project': catalog_project,
                })
                client_name = reparsed.get('client_name') or ''
                client_phone = reparsed.get('client_phone') or ''
                client_email = reparsed.get('client_email') or ''
                client_telegram = reparsed.get('client_telegram') or ''
                plot = reparsed.get('plot') or ''
                budget = reparsed.get('budget') or ''
                area = reparsed.get('area') or ''
                material = reparsed.get('material') or ''
                timeline = reparsed.get('timeline') or ''
                funding_source = reparsed.get('funding_source') or ''
                catalog_project = reparsed.get('catalog_project') or ''
                extraction_source = reparsed.get('extraction_source') or ''
            except Exception as e:
                logger.error(f"Ошибка перепарсинга при редактировании: {e}")
                flash(f'Ошибка обновления данных транскрибации: {e}', 'warning')

        contact_gaps = _missing_required_contacts(client_phone, client_email)
        if contact_gaps:
            flash(
                'Укажите обязательные контакты: ' + ', '.join(contact_gaps)
                + ' (email — основной канал КП; Telegram опционален).',
                'error',
            )
            return redirect(url_for('deals.edit_deal', deal_id=deal_id))

        try:
            tk_cost = None if is_timber_material(material) else calc_tk_cost(area)
            draft = {
                'client_phone': client_phone,
                'client_email': client_email,
                'plot': plot,
                'area': area,
                'material': material,
                'timeline': timeline,
                'funding_source': funding_source,
                'catalog_project': catalog_project,
                'transcript': transcript,
            }
            match_preview = etalon_match_score(draft)
            requested_status = normalize_status(status)
            # Ручной completed/lost / явный выбор пайплайна; иначе авто по эталону
            if requested_status in ('completed', 'lost'):
                new_status = requested_status
            elif requested_status in ('new', 'incomplete', 'kp_ready'):
                # не даём kp_ready при дырявом эталоне
                if requested_status == 'kp_ready' and not match_preview['can_generate_kp']:
                    new_status = 'incomplete'
                elif requested_status in ('new', 'incomplete'):
                    new_status = status_after_etalon(
                        can_generate_kp=match_preview['can_generate_kp'],
                        current=requested_status,
                    )
                else:
                    new_status = requested_status
            else:
                new_status = status_after_etalon(
                    can_generate_kp=match_preview['can_generate_kp'],
                    current=deal.get('status'),
                )

            conn = get_db()
            conn.execute(
                '''
                UPDATE deals SET
                    client_name = ?, client_phone = ?, client_email = ?, client_telegram = ?,
                    transcript = ?, notes = ?, status = ?,
                    plot = ?, budget = ?, area = ?, material = ?, timeline = ?, funding_source = ?,
                    tk_cost = ?, catalog_project = ?, extraction_source = ?
                WHERE id = ?
                ''',
                (
                    client_name, client_phone, client_email, client_telegram,
                    transcript, notes, new_status,
                    plot, budget, area, material, timeline, funding_source,
                    tk_cost, catalog_project,
                    extraction_source or (deal.get('extraction_source') or ''),
                    deal_id,
                ),
            )
            log_action(
                conn,
                deal_id=deal_id,
                action='updated',
                detail=(
                    f'Эталон {match_preview["score"]}%; статус {status_label(new_status)}'
                    + (f'; источник {extraction_source}' if extraction_source else '')
                ),
                user_id=_actor_id(),
            )
            # Аудит переразбора (только если транскрибация менялась успешно)
            if _extracted is not None:
                from extraction.validation.rules import validate
                from extraction.validation.escalation import escalate
                from extraction.scoring.lead_score import score as lead_score
                from extraction.audit.logger import log_extraction

                _validation = validate(_extracted)
                _escalation = escalate(_extracted, _validation, raw_text=transcript)
                _lead = lead_score(_extracted)
                log_extraction(
                    conn,
                    deal_id=deal_id,
                    extraction=_extracted,
                    source=_source or extraction_source or 'regex',
                    validation=_validation,
                    escalation=_escalation,
                    lead=_lead,
                )
            if normalize_status(deal.get('status')) != new_status:
                log_action(
                    conn,
                    deal_id=deal_id,
                    action='status_changed',
                    detail=f'{status_label(deal.get("status"))} → {status_label(new_status)}',
                    user_id=_actor_id(),
                )
            conn.commit()
            updated = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
            conn.close()

            # Пересчитываем % заполнения эталона
            match = etalon_match_score(updated)
            if match['is_complete']:
                flash('Сделка обновлена! Данные соответствуют эталону на 100%.', 'success')
            elif match['can_generate_kp']:
                flash(
                    f'Сделка обновлена. Заполнение {match["score"]}% — КП можно генерировать.',
                    'success',
                )
            else:
                flash(
                    f'Сделка обновлена. Заполнение {match["score"]}% — ещё не хватает данных для КП.',
                    'warning',
                )
        except Exception as e:
            logger.error(f"Ошибка сохранения сделки #{deal_id}: {e}")
            flash(f'Ошибка обновления данных: {e}', 'warning')
            return redirect(url_for('deals.edit_deal', deal_id=deal_id))

        return redirect(url_for('deals.deal_detail', deal_id=deal_id))

    return render_template(
        'deals/edit.html',
        deal=deal,
        edit=True,
        kp_threshold=KP_THRESHOLD,
        deal_statuses=DEAL_STATUSES,
        status_labels=STATUS_LABELS,
    )

@deals_bp.route('/<int:deal_id>/generate-kp', methods=['POST'])
@login_required
def generate_kp(deal_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Сделка не найдена'}), 404

    deal = dict(row)
    match = etalon_match_score(deal)
    if not match['can_generate_kp']:
        conn.close()
        return jsonify({
            'status': 'error',
            'message': (
                f'Недостаточно данных для КП: {match["score"]}% '
                f'(нужно ≥ {KP_THRESHOLD}%). Не хватает: {", ".join(match["missing"])}'
            ),
            'score': match['score'],
            'threshold': KP_THRESHOLD,
            'missing': match['missing'],
            'redirect': url_for('deals.incomplete_data', deal_id=deal_id),
        }), 400

    payload = request.get_json(silent=True) or {}
    watermark = (payload.get('watermark') or request.args.get('watermark') or 'draft').strip()
    if watermark not in ('draft', 'approved'):
        watermark = 'draft'
    use_ai = payload.get('use_ai', True)
    if isinstance(use_ai, str):
        use_ai = use_ai.lower() not in ('0', 'false', 'no')

    manager_name = session.get('user_name') or session.get('username') or None

    try:
        from utils.stroika_kp import generate_stroika_kp_pdf, parse_area_m2, PRICE_PER_M2
        from utils.timber_kp import generate_timber_kp_from_deal, is_timber_material as timber_deal

        timber = timber_deal(deal.get('material')) or timber_deal(deal.get('transcript'))
        if timber:
            meta = generate_timber_kp_from_deal(
                deal,
                watermark=watermark,
                manager_name=manager_name,
            )
        else:
            if not parse_area_m2(deal.get('area')):
                conn.close()
                return jsonify({
                    'status': 'error',
                    'message': 'Укажите площадь дома (м²) в карточке сделки — без неё нельзя посчитать КП.',
                    'redirect': url_for('deals.edit_deal', deal_id=deal_id),
                }), 400

            meta = generate_stroika_kp_pdf(
                deal,
                watermark=watermark,
                use_ai=bool(use_ai),
                manager_name=manager_name,
            )
    except ValueError as exc:
        conn.close()
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        conn.close()
        logger.exception('Ошибка генерации КП для сделки %s', deal_id)
        return jsonify({
            'status': 'error',
            'message': f'Не удалось сформировать КП: {exc}',
        }), 500

    new_status = status_after_kp_ready(deal.get('status'))
    conn.execute(
        'UPDATE deals SET kp_options = ?, status = ? WHERE id = ?',
        (json.dumps(meta, ensure_ascii=False), new_status, deal_id),
    )
    if timber:
        message = (
            f'КП готово (клееный брус): {meta.get("total_fmt")} '
            f'· вариант «Стандарт»'
        )
        detail = f'брус · {meta.get("total_fmt")} · {meta.get("watermark")}'
    else:
        message = (
            f'КП готово: {meta["area_m2"]} м² × {PRICE_PER_M2:,} ₽/м² = {meta["total_fmt"]}'
            .replace(',', ' ')
        )
        detail = f'{meta.get("area_m2")} м² · {meta.get("total_fmt")} · {meta.get("watermark")}'
    log_action(
        conn,
        deal_id=deal_id,
        action='kp_generated',
        detail=detail,
        user_id=_actor_id(),
    )
    if normalize_status(deal.get('status')) != new_status:
        log_action(
            conn,
            deal_id=deal_id,
            action='status_changed',
            detail=f'{status_label(deal.get("status"))} → {status_label(new_status)}',
            user_id=_actor_id(),
        )
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success',
        'message': message,
        'deal_id': deal_id,
        'score': match['score'],
        'kp': meta,
        'download_url': url_for('deals.download_kp', deal_id=deal_id),
    })


@deals_bp.route('/<int:deal_id>/kp.pdf')
@login_required
def download_kp(deal_id):
    conn = get_db()
    row = conn.execute('SELECT kp_options FROM deals WHERE id = ?', (deal_id,)).fetchone()
    conn.close()
    if not row:
        flash('Сделка не найдена', 'error')
        return redirect(url_for('deals.list_deals'))

    meta = _parse_kp_options(row['kp_options'] if isinstance(row, sqlite3.Row) else row[0])
    if not meta or not meta.get('pdf_path'):
        flash('КП ещё не сгенерировано', 'warning')
        return redirect(url_for('deals.deal_detail', deal_id=deal_id))

    path = _safe_kp_pdf_path(meta['pdf_path'])
    if not path:
        flash('Файл КП не найден на диске', 'error')
        return redirect(url_for('deals.deal_detail', deal_id=deal_id))

    download_name = (
        f"KP_timber_deal{deal_id}.pdf"
        if (meta or {}).get("kp_kind") == "timber"
        else f"KP_DomMaster_deal{deal_id}.pdf"
    )
    response = send_file(
        path,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=download_name,
        conditional=True,
        max_age=0,
    )
    response.headers['Cache-Control'] = 'no-store'
    return response


@deals_bp.route('/telegram-outbox', methods=['GET'])
def telegram_outbox_list():
    """Очередь КП на отправку в Telegram (забирает локальный бот)."""
    expected = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    got = (request.headers.get('X-Bot-Token') or '').strip()
    if not expected or got != expected:
        return jsonify({'ok': False, 'message': 'Unauthorized'}), 401

    conn = get_db()
    rows = conn.execute(
        "SELECT id, telegram_outbox FROM deals WHERE telegram_outbox IS NOT NULL AND telegram_outbox != ''"
    ).fetchall()
    conn.close()
    items = []
    for row in rows:
        try:
            payload = json.loads(row['telegram_outbox'])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload['deal_id'] = row['id']
            items.append(payload)
    return jsonify({'ok': True, 'items': items})


@deals_bp.route('/telegram-outbox/<int:deal_id>', methods=['POST'])
def telegram_outbox_ack(deal_id):
    """Подтверждение доставки из локального бота."""
    expected = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    got = (request.headers.get('X-Bot-Token') or '').strip()
    if not expected or got != expected:
        return jsonify({'ok': False, 'message': 'Unauthorized'}), 401

    payload = request.get_json(silent=True) or {}
    ok = bool(payload.get('ok', True))
    from datetime import datetime

    conn = get_db()
    if ok:
        conn.execute(
            '''
            UPDATE deals SET
                telegram_outbox = NULL,
                delivery_method = CASE
                    WHEN delivery_method IS NULL OR delivery_method = '' THEN 'telegram'
                    WHEN delivery_method LIKE '%telegram%' THEN delivery_method
                    ELSE delivery_method || '+telegram'
                END,
                delivery_date = ?,
                delivery_status = 'ok',
                delivery_error = NULL,
                status = CASE
                    WHEN status IN ('approved', 'kp_ready', 'new', 'incomplete', 'sent')
                    THEN 'kp_sent' ELSE status END
            WHERE id = ?
            ''',
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), deal_id),
        )
        log_action(
            conn,
            deal_id=deal_id,
            action='kp_sent',
            detail='telegram outbox ack',
            user_id=None,
        )
    else:
        err = str(payload.get('error') or 'outbox send failed')
        conn.execute(
            'UPDATE deals SET delivery_status = ?, delivery_error = ? WHERE id = ?',
            ('error', err, deal_id),
        )
        log_action(
            conn,
            deal_id=deal_id,
            action='kp_send_failed',
            detail=err,
            user_id=None,
        )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@deals_bp.route('/<int:deal_id>/telegram-bind', methods=['POST'])
def telegram_bind_api(deal_id):
    """
    Публичный endpoint для бота: сохранить chat_id клиента.
    Auth: заголовок X-Bot-Token == TELEGRAM_BOT_TOKEN.
    """
    expected = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    got = (request.headers.get('X-Bot-Token') or '').strip()
    if not expected or got != expected:
        return jsonify({'ok': False, 'message': 'Unauthorized'}), 401

    payload = request.get_json(silent=True) or {}
    chat_id = str(payload.get('chat_id') or '').strip()
    username = (payload.get('username') or '').strip().lstrip('@') or None
    if not chat_id.isdigit() and not (chat_id.startswith('-') and chat_id[1:].isdigit()):
        return jsonify({'ok': False, 'message': 'chat_id обязателен'}), 400

    conn = get_db()
    try:
        row = conn.execute(
            'SELECT id, client_name, client_telegram FROM deals WHERE id = ?',
            (deal_id,),
        ).fetchone()
        if not row:
            return jsonify({'ok': False, 'message': f'Сделка #{deal_id} не найдена'}), 404

        current_tg = (row['client_telegram'] or '').strip()
        new_client_tg = current_tg
        if username and (not current_tg or current_tg.isdigit()):
            new_client_tg = f'@{username}'

        conn.execute(
            '''
            UPDATE deals
            SET telegram_chat_id = ?,
                client_telegram = COALESCE(NULLIF(?, ''), client_telegram)
            WHERE id = ?
            ''',
            (chat_id, new_client_tg, deal_id),
        )
        log_action(
            conn,
            deal_id=deal_id,
            action='telegram_bound',
            detail=f'chat_id={chat_id}' + (f' @{username}' if username else ''),
            user_id=None,
        )
        conn.commit()
        return jsonify({
            'ok': True,
            'deal_id': deal_id,
            'client_name': row['client_name'] or 'Клиент',
            'chat_id': chat_id,
            'username': username,
        })
    except Exception as exc:  # noqa: BLE001
        logger.exception('telegram-bind failed deal=%s', deal_id)
        return jsonify({'ok': False, 'message': str(exc)}), 500
    finally:
        conn.close()


@deals_bp.route('/<int:deal_id>/kp.pdf/bot', methods=['GET'])
def download_kp_bot(deal_id):
    """Скачивание PDF для локального бота (X-Bot-Token)."""
    expected = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    got = (request.headers.get('X-Bot-Token') or '').strip()
    if not expected or got != expected:
        return jsonify({'ok': False, 'message': 'Unauthorized'}), 401

    conn = get_db()
    row = conn.execute('SELECT kp_options FROM deals WHERE id = ?', (deal_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'ok': False, 'message': 'Сделка не найдена'}), 404

    meta = _parse_kp_options(row['kp_options'] if isinstance(row, sqlite3.Row) else row[0])
    if not meta or not meta.get('pdf_path'):
        return jsonify({'ok': False, 'message': 'КП не сгенерировано'}), 404

    path = _safe_kp_pdf_path(meta['pdf_path'])
    if not path:
        return jsonify({'ok': False, 'message': 'Файл КП не найден'}), 404

    return send_file(
        path,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=(
            f'KP_timber_deal{deal_id}.pdf'
            if (meta or {}).get('kp_kind') == 'timber'
            else f'KP_DomMaster_deal{deal_id}.pdf'
        ),
        max_age=0,
    )


@deals_bp.route('/<int:deal_id>/approve-kp', methods=['POST'])
@login_required
def approve_kp(deal_id):
    """Пересобирает КП с водяным знаком «УТВЕРЖДЕНО»."""
    conn = get_db()
    row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Сделка не найдена'}), 404

    deal = dict(row)
    meta = _parse_kp_options(deal.get('kp_options'))
    if not meta:
        conn.close()
        return jsonify({
            'status': 'error',
            'message': 'Сначала сгенерируйте черновик КП',
        }), 400

    manager_name = session.get('user_name') or session.get('username') or None
    try:
        from utils.stroika_kp import generate_stroika_kp_pdf
        from utils.timber_kp import generate_timber_kp_from_deal, is_timber_material as timber_deal

        timber = (
            (meta or {}).get("kp_kind") == "timber"
            or timber_deal(deal.get("material"))
            or timber_deal(deal.get("transcript"))
        )
        if timber:
            approved = generate_timber_kp_from_deal(
                deal,
                watermark="approved",
                manager_name=manager_name,
            )
        else:
            # Без AI — быстрее и стабильнее на VPS; цифры те же по стандарту
            approved = generate_stroika_kp_pdf(
                deal,
                watermark='approved',
                use_ai=False,
                manager_name=manager_name,
            )
    except Exception as exc:  # noqa: BLE001
        conn.close()
        logger.exception('Ошибка утверждения КП #%s', deal_id)
        return jsonify({'status': 'error', 'message': str(exc)}), 500

    new_status = status_after_kp_ready(deal.get('status'))
    conn.execute(
        'UPDATE deals SET kp_options = ?, status = ? WHERE id = ?',
        (json.dumps(approved, ensure_ascii=False), new_status, deal_id),
    )
    log_action(
        conn,
        deal_id=deal_id,
        action='kp_approved',
        detail=approved.get('total_fmt'),
        user_id=_actor_id(),
    )
    if normalize_status(deal.get('status')) != new_status:
        log_action(
            conn,
            deal_id=deal_id,
            action='status_changed',
            detail=f'{status_label(deal.get("status"))} → {status_label(new_status)}',
            user_id=_actor_id(),
        )
    conn.commit()
    conn.close()
    return jsonify({
        'status': 'success',
        'message': f'КП утверждено: {approved.get("total_fmt")}',
        'kp': approved,
        'download_url': url_for('deals.download_kp', deal_id=deal_id),
    })


@deals_bp.route('/<int:deal_id>/send-kp', methods=['POST'])
@login_required
def send_kp(deal_id):
    """Отправка утверждённого КП клиенту (email и/или telegram)."""
    from datetime import datetime

    from mailer import send_kp_email, smtp_configured
    from telegram_send import resolve_deal_chat_id, send_kp_telegram, telegram_configured

    payload = request.get_json(silent=True) or {}
    channels = payload.get('channels') or []
    if isinstance(channels, str):
        channels = [channels]
    if not channels:
        # по умолчанию — email, если есть; иначе telegram
        channels = ['email']

    conn = get_db()
    row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Сделка не найдена'}), 404

    deal = dict(row)
    meta = _parse_kp_options(deal.get('kp_options'))
    if not meta or not meta.get('pdf_path'):
        conn.close()
        return jsonify({'status': 'error', 'message': 'КП ещё не сгенерировано'}), 400
    if meta.get('watermark') != 'approved':
        conn.close()
        return jsonify({
            'status': 'error',
            'message': 'Сначала утвердите КП (кнопка «Утвердить»)',
        }), 400

    path = _safe_kp_pdf_path(meta['pdf_path'])
    if not path:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Файл КП не найден на диске'}), 404

    manager_name = session.get('user_name') or session.get('username') or None
    results = []
    errors = []

    if 'email' in channels:
        email = (deal.get('client_email') or '').strip()
        if not email:
            errors.append('У клиента не указан email')
        elif not smtp_configured():
            errors.append('SMTP не настроен в .env (SMTP_HOST / SMTP_USER / SMTP_PASSWORD)')
        else:
            try:
                results.append(send_kp_email(
                    to_email=email,
                    client_name=deal.get('client_name') or 'Клиент',
                    pdf_path=path,
                    kp_meta=meta,
                    manager_name=manager_name,
                ))
            except Exception as exc:  # noqa: BLE001
                logger.exception('Email send failed deal=%s', deal_id)
                errors.append(f'Email: {exc}')

    if 'telegram' in channels:
        chat_id = resolve_deal_chat_id(deal)
        if not chat_id:
            errors.append(
                'Telegram не привязан. Отправьте клиенту ссылку привязки из карточки сделки '
                '(или сохраните числовой chat_id).'
            )
        elif not telegram_configured():
            errors.append('TELEGRAM_BOT_TOKEN не задан')
        else:
            caption = (
                f"КП «Дом Мастер» {meta.get('kp_number', '')}\n"
                f"{meta.get('area_m2')} м² · {meta.get('total_fmt')}"
            )
            try:
                results.append(send_kp_telegram(
                    chat_id=chat_id,
                    pdf_path=path,
                    caption=caption,
                ))
            except Exception as exc:  # noqa: BLE001
                logger.exception('Telegram send failed deal=%s — ставим в outbox', deal_id)
                # VPS часто не достучится до api.telegram.org — очередь для локального бота
                outbox = {
                    'deal_id': deal_id,
                    'chat_id': chat_id,
                    'pdf_path': str(path),
                    'pdf_url': f'/deals/{deal_id}/kp.pdf/bot',
                    'caption': caption,
                    'queued_at': datetime.now().isoformat(timespec='seconds'),
                    'error': str(exc),
                }
                conn.execute(
                    'UPDATE deals SET telegram_outbox = ? WHERE id = ?',
                    (json.dumps(outbox, ensure_ascii=False), deal_id),
                )
                conn.commit()
                results.append({
                    'ok': True,
                    'method': 'telegram_queued',
                    'chat_id': chat_id,
                    'message': (
                        'Telegram с сервера недоступен — КП поставлено в очередь. '
                        'Локальный бот доставит PDF клиенту в течение минуты.'
                    ),
                })
                # Не считаем очередь ошибкой: доставку закрывает outbox-воркер

    method = '+'.join(r.get('method', '') for r in results) or None
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    queued_only = bool(results) and all(r.get('method') == 'telegram_queued' for r in results)
    has_queue = any(r.get('method') == 'telegram_queued' for r in results)

    if results and not errors:
        status = status_after_kp_sent(deal.get('status'))
        delivery_status = 'queued' if queued_only else 'ok'
        delivery_error = None
        if queued_only:
            flash_msg = results[0].get('message') or 'КП в очереди Telegram — бот доставит'
        elif has_queue:
            flash_msg = (
                'КП отправлено. Telegram уйдёт через локальный бот '
                '(сервер к Telegram API недоступен).'
            )
        else:
            flash_msg = 'КП отправлено клиенту'
        http = 200
    elif results and errors:
        status = status_after_kp_sent(deal.get('status'))
        delivery_status = 'partial'
        delivery_error = '; '.join(errors)
        flash_msg = 'Отправлено частично: ' + delivery_error
        http = 200
    else:
        status = normalize_status(deal.get('status') or 'kp_ready')
        delivery_status = 'error'
        delivery_error = '; '.join(errors) or 'Не удалось отправить'
        flash_msg = delivery_error
        http = 400

    conn.execute(
        '''
        UPDATE deals SET
            status = ?,
            delivery_method = ?,
            delivery_date = ?,
            delivery_status = ?,
            delivery_error = ?
        WHERE id = ?
        ''',
        (status if results else status, method, now if results else deal.get('delivery_date'),
         delivery_status, delivery_error, deal_id),
    )
    if results:
        channels_txt = ', '.join(r.get('method', '?') for r in results)
        log_action(
            conn,
            deal_id=deal_id,
            action='kp_sent',
            detail=f'{channels_txt}; {delivery_status}',
            user_id=_actor_id(),
        )
        if normalize_status(deal.get('status')) != status:
            log_action(
                conn,
                deal_id=deal_id,
                action='status_changed',
                detail=f'{status_label(deal.get("status"))} → {status_label(status)}',
                user_id=_actor_id(),
            )
    else:
        log_action(
            conn,
            deal_id=deal_id,
            action='kp_send_failed',
            detail=delivery_error,
            user_id=_actor_id(),
        )
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'success' if results else 'error',
        'message': flash_msg,
        'results': results,
        'errors': errors,
        'delivery_status': delivery_status,
    }), http


@deals_bp.route('/<int:deal_id>/status', methods=['POST'])
@login_required
def update_status(deal_id):
    status = normalize_status((request.json or {}).get('status'))
    if status not in DEAL_STATUSES:
        return jsonify({'error': 'Неизвестный статус'}), 400

    conn = get_db()
    row = conn.execute('SELECT status FROM deals WHERE id = ?', (deal_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Сделка не найдена'}), 404

    old = normalize_status(row['status'] if isinstance(row, sqlite3.Row) else row[0])
    conn.execute('UPDATE deals SET status = ? WHERE id = ?', (status, deal_id))
    log_action(
        conn,
        deal_id=deal_id,
        action='status_changed',
        detail=f'{status_label(old)} → {status_label(status)}',
        user_id=_actor_id(),
    )
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'deal_status': status, 'label': status_label(status)})


@deals_bp.route('/<int:deal_id>/delete', methods=['POST'])
@login_required
def delete_deal(deal_id):
    """Удаляет одну сделку. Только администратор сервиса."""
    if not is_service_admin():
        return jsonify({'status': 'error', 'message': DENY_DELETE_MSG}), 403

    conn = get_db()
    row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'status': 'error', 'message': 'Сделка не найдена'}), 404

    deal = dict(row)
    try:
        conn.execute('DELETE FROM action_log WHERE deal_id = ?', (deal_id,))
    except sqlite3.OperationalError:
        pass
    conn.execute('DELETE FROM deals WHERE id = ?', (deal_id,))
    conn.commit()
    conn.close()
    _remove_deal_kp_files(deal)

    name = (deal.get('client_name') or '').strip() or f'#{deal_id}'
    return jsonify({
        'status': 'success',
        'message': f'Сделка {name} удалена',
        'redirect': url_for('deals.list_deals'),
    })


@deals_bp.route('/reminders/run', methods=['POST'])
@login_required
def run_reminders():
    """Ручной запуск проверки зависших сделок."""
    conn = get_db()
    info = _maybe_run_reminders(conn, notify=True) or {}
    conn.close()
    return jsonify({'ok': True, **{k: info.get(k) for k in ('stale_total', 'due', 'notified', 'errors')}})
