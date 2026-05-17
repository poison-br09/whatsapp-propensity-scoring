import logging
import smtplib
from email.message import EmailMessage

from app.core.config import Settings

Logger = logging.getLogger
logger = Logger(__name__)


class EmailService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def is_configured(self) -> bool:
        return bool(
            self._settings.smtp_host
            and self._settings.smtp_from_email
            and self._settings.whatsapp_alert_recipient_list
        )

    def send_whatsapp_logged_out_alert(
        self,
        *,
        phone_number: str | None,
        occurred_at: str | None,
        status_code: int | None,
    ) -> bool:
        if not self.is_configured():
            logger.warning('Skipping WhatsApp logout alert email because SMTP or recipients are not fully configured')
            return False

        subject = 'WhatsApp bridge session logged out'
        body = (
            'The WhatsApp bridge session has been logged out.\n\n'
            f'Phone number: {phone_number or "unknown"}\n'
            f'Occurred at: {occurred_at or "unknown"}\n'
            f'Status code: {status_code if status_code is not None else "unknown"}\n\n'
            'A new pairing flow is required to reconnect the session.'
        )

        message = EmailMessage()
        message['Subject'] = subject
        message['From'] = self._settings.smtp_from_email
        message['To'] = ', '.join(self._settings.whatsapp_alert_recipient_list)
        message.set_content(body)

        self._send_message(message)
        logger.info(
            'Sent WhatsApp logout alert email recipients=%s phone_number=%s',
            self._settings.whatsapp_alert_recipient_list,
            phone_number,
        )
        return True

    def _send_message(self, message: EmailMessage) -> None:
        if self._settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(self._settings.smtp_host, self._settings.smtp_port) as smtp:
                self._login_and_send(smtp, message)
            return

        with smtplib.SMTP(self._settings.smtp_host, self._settings.smtp_port) as smtp:
            if self._settings.smtp_use_tls:
                smtp.starttls()
            self._login_and_send(smtp, message)

    def _login_and_send(self, smtp: smtplib.SMTP, message: EmailMessage) -> None:
        if self._settings.smtp_username:
            smtp.login(self._settings.smtp_username, self._settings.smtp_password or '')
        smtp.send_message(message)
