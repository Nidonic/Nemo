"""Delivering one-time codes: e-mail over SMTP, SMS through a webhook, or the log in development."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from nemo_cloud.settings import Settings

log = logging.getLogger("nemo_cloud.codes")


class DeliveryError(Exception):
    pass


async def deliver(settings: Settings, channel: str, identifier: str, code: str) -> str:
    """Send the code; returns how it went ("email", "sms", "console")."""
    text = f"Your Nemo sign-in code is {code}. It expires in {settings.code_ttl // 60} minutes."
    if channel == "phone":
        if settings.sms_webhook:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(settings.sms_webhook, json={"phone": identifier, "code": code, "text": text})
            if r.status_code >= 400:
                raise DeliveryError(f"SMS gateway refused ({r.status_code}).")
            return "sms"
    elif settings.email_sender == "smtp":
        msg = EmailMessage()
        msg["Subject"] = f"Nemo code: {code}"
        msg["From"] = settings.smtp_from or settings.smtp_user
        msg["To"] = identifier
        msg.set_content(text)
        try:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=ssl.create_default_context(), timeout=20) as s:
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryError(f"E-mail could not be sent: {exc}") from exc
        return "email"
    log.warning("sign-in code for %s: %s", identifier, code)
    return "console"
