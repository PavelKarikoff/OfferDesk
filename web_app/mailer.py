"""Отправка КП по email (SMTP)."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from pricing import PRICE_PER_M2


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_USER", "").strip())


def send_kp_email(
    *,
    to_email: str,
    client_name: str,
    pdf_path: str | Path,
    kp_meta: dict[str, Any] | None = None,
    manager_name: str | None = None,
) -> dict[str, Any]:
    """
    Отправляет письмо с вложением PDF КП.
    Настройки: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_USE_TLS.
    """
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    from_addr = os.getenv("SMTP_FROM", "").strip() or user
    port = int(os.getenv("SMTP_PORT", "587") or 587)
    use_tls = os.getenv("SMTP_USE_TLS", "1").strip().lower() not in ("0", "false", "no")

    if not host or not user:
        raise RuntimeError(
            "SMTP не настроен. Укажите SMTP_HOST, SMTP_USER, SMTP_PASSWORD в .env"
        )
    if not to_email or "@" not in to_email:
        raise ValueError("У клиента нет корректного email")

    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF не найден: {path}")

    meta = kp_meta or {}
    area = meta.get("area_m2", "—")
    total = meta.get("total_fmt", "—")
    kp_number = meta.get("kp_number", "КП")
    manager = manager_name or "Отдел продаж"
    timber = meta.get("kp_kind") == "timber"
    company = meta.get("company_name") or ("Дома из клееного бруса" if timber else "Дом Мастер")

    if timber:
        subject = f"Коммерческое предложение «{company}» — {kp_number}"
        body = (
            f"Здравствуйте, {client_name or 'уважаемый клиент'}!\n\n"
            f"Направляем коммерческое предложение на строительство дома "
            f"из клееного бруса (тёплый контур).\n\n"
            f"Номер: {kp_number}\n"
            f"Объект / площадь: {area}\n"
            f"Ориентировочная стоимость: {total}\n\n"
            f"PDF во вложении. Итоговая смета уточняется после выбора проекта "
            f"и выезда на участок.\n\n"
            f"С уважением,\n{manager}\n{company}\n"
        )
    else:
        rate = f"{PRICE_PER_M2:,}".replace(",", " ")
        subject = f"Коммерческое предложение «Дом Мастер» — {kp_number}"
        body = (
            f"Здравствуйте, {client_name or 'уважаемый клиент'}!\n\n"
            f"Направляем коммерческое предложение на строительство тёплого контура "
            f"из газобетона.\n\n"
            f"Номер: {kp_number}\n"
            f"Площадь: {area} м²\n"
            f"Ориентировочная стоимость ТК: {total}\n"
            f"Ставка: {rate} ₽/м² (стандарт компании)\n\n"
            f"PDF во вложении.\n\n"
            f"С уважением,\n{manager}\nООО «Дом-Мастер»\n"
            f"+7 (495) 123-45-67 · dom-master.ru\n"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content(body)
    msg.add_attachment(
        path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=path.name if path.name.endswith(".pdf") else f"{kp_number}.pdf",
    )

    if use_tls and port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as smtp:
            smtp.ehlo()
            if use_tls:
                context = ssl.create_default_context()
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)

    return {
        "ok": True,
        "to": to_email,
        "from": from_addr,
        "subject": subject,
        "method": "email",
    }
