"""Email delivery. Callers should run `send_email` via BackgroundTasks so requests don't block on SMTP."""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from api.core.config import get_settings

logger = logging.getLogger("api.email")

_env = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def send_email(to: str, subject: str, template: str, **context) -> None:
    s = get_settings()
    html = _env.get_template(f"{template}.html").render(**context)
    text = _env.get_template(f"{template}.txt").render(**context)

    if s.email_backend == "console":
        # Contains links with live tokens, so this backend is development-only (blocked in production).
        logger.warning("EMAIL to=%s subject=%s\n%s", to, subject, text)
        return

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.email_from, to, subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    password = s.smtp_password.get_secret_value() if s.smtp_password else None
    try:
        if s.smtp_use_ssl:
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
            server.starttls(context=ssl.create_default_context())
        with server:
            if s.smtp_user and password:
                server.login(s.smtp_user, password)
            server.send_message(msg)
    except Exception:
        # Runs in a background task: log and swallow so the API response isn't affected.
        logger.exception("Failed to send email to %s", to)
