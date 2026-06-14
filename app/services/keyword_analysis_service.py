import logging
import re
from datetime import datetime, timezone

from app.models.whatsapp import WhatsAppKeywordMatchRecord, WhatsAppMessageWebhook, WhatsAppMessageWebhookResponse
from app.repositories.supabase_poll_repository import SupabasePollRepository

logger = logging.getLogger(__name__)


class KeywordAnalysisService:
    def __init__(self, repository: SupabasePollRepository) -> None:
        self._repository = repository
        self._keywords: list[dict[str, str]] | None = None

    async def refresh_keywords(self) -> None:
        self._keywords = await self._repository.get_active_keywords()
        logger.info('Loaded %s active keywords from DB', len(self._keywords))

    async def _get_keywords(self) -> list[dict[str, str]]:
        if self._keywords is None:
            await self.refresh_keywords()
        return self._keywords or []

    async def analyze_message(self, payload: WhatsAppMessageWebhook) -> WhatsAppMessageWebhookResponse:
        keywords = await self._get_keywords()
        if not keywords:
            return WhatsAppMessageWebhookResponse(matched=False)

        pattern = re.compile(
            r'\b(' + '|'.join(re.escape(kw['keyword']) for kw in keywords) + r')\b',
            re.IGNORECASE,
        )
        found_texts = {m.lower() for m in pattern.findall(payload.message)}
        if not found_texts:
            return WhatsAppMessageWebhookResponse(matched=False)

        message_date = datetime.fromtimestamp(payload.message_timestamp_ms / 1000, tz=timezone.utc)
        stored = False
        for kw in keywords:
            if kw['keyword'].lower() not in found_texts:
                continue
            record = WhatsAppKeywordMatchRecord(
                keyword_id=kw['id'],
                keyword=kw['keyword'],
                group_jid=payload.group_jid,
                sender_jid=payload.sender_jid,
                sender_name=payload.sender_name,
                sender_phone=payload.sender_phone,
                message=payload.message,
                message_id=payload.message_id,
                message_date=message_date,
            )
            await self._repository.store_keyword_match(record)
            stored = True

        logger.info(
            'Keyword match found message_id=%s sender_phone=%s keywords=%s',
            payload.message_id,
            payload.sender_phone,
            list(found_texts),
        )
        return WhatsAppMessageWebhookResponse(matched=True, keywords=list(found_texts), stored=stored)
