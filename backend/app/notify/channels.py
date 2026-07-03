"""Delivery channels: Telegram bot + SMTP email."""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from ..config import settings

log = logging.getLogger("housespotter.notify")


def send_telegram(text: str, photo_url: str | None = None, chat_id: str | None = None) -> bool:
    chat_id = chat_id or settings.telegram_chat_id
    if not settings.telegram_bot_token or not chat_id:
        log.debug("Telegram not configured; skipping")
        return False
    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    try:
        if photo_url:
            resp = httpx.post(
                f"{base}/sendPhoto",
                data={
                    "chat_id": chat_id,
                    "photo": photo_url,
                    "caption": text[:1024],
                    "parse_mode": "HTML",
                },
                timeout=20,
            )
        else:
            resp = httpx.post(
                f"{base}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "text": text[:4096],
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=20,
            )
        if resp.status_code != 200:
            log.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:200])
            return False
        return True
    except Exception:
        log.exception("Telegram send error")
        return False


def send_email(subject: str, html_body: str, to: str | None = None) -> bool:
    to = to or settings.smtp_to
    if not settings.smtp_host or not to:
        log.debug("SMTP not configured; skipping")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception:
        log.exception("Email send error")
        return False
