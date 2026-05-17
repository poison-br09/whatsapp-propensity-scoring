import asyncio
import logging
from datetime import datetime
from typing import Any

from app.core.config import Settings
from app.models.poll import PollPredictionRecord, UserHistory
from app.models.whatsapp import WhatsAppPollRecord, WhatsAppPollVoteEventRecord, WhatsAppPollVoteSnapshotRecord

Logger = logging.getLogger
logger = Logger(__name__)


class SupabasePollRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None

    async def get_user_history(self, phone_numbers: list[str]) -> dict[str, UserHistory]:
        if not phone_numbers:
            logger.info('Skipping Supabase history RPC because no phone numbers were provided')
            return {}

        logger.info(
            'Calling Supabase history RPC rpc=%s phone_numbers=%s',
            self._settings.supabase_poll_history_rpc,
            len(phone_numbers),
        )
        result = await asyncio.to_thread(self._call_history_rpc, phone_numbers)
        rows = getattr(result, 'data', result) or []
        histories = [self._parse_history(row) for row in rows]
        logger.info(
            'Supabase history RPC completed rpc=%s rows=%s',
            self._settings.supabase_poll_history_rpc,
            len(histories),
        )
        return {history.mobile: history for history in histories}

    async def insert_predictions(self, records: list[PollPredictionRecord]) -> None:
        if not records:
            logger.info('Skipping poll_prediction insert because there are no records')
            return

        payload = [record.to_supabase_payload() for record in records]
        logger.info('Inserting poll_prediction records count=%s', len(payload))
        await asyncio.to_thread(self._insert_prediction_payload, payload)
        logger.info('Inserted poll_prediction records count=%s', len(payload))

    async def upsert_prediction(self, record: PollPredictionRecord) -> None:
        payload = record.to_supabase_payload()
        logger.info(
            'Upserting poll_prediction record mobile=%s source_filename=%s',
            record.mobile,
            record.source_filename,
        )
        await asyncio.to_thread(self._upsert_prediction_payload, payload)

    async def upsert_whatsapp_poll(self, record: WhatsAppPollRecord) -> None:
        await asyncio.to_thread(self._upsert_whatsapp_poll_payload, record.to_supabase_payload())

    async def create_whatsapp_poll_vote_event_if_new(self, record: WhatsAppPollVoteEventRecord) -> bool:
        exists = await asyncio.to_thread(self._whatsapp_vote_event_exists, record.dedupe_key)
        if exists:
            return False

        await asyncio.to_thread(self._insert_whatsapp_vote_event_payload, record.to_supabase_payload())
        logger.info('Inserted WhatsApp poll vote event dedupe_key=%s', record.dedupe_key)
        return True

    async def upsert_whatsapp_poll_vote_snapshot(self, record: WhatsAppPollVoteSnapshotRecord) -> None:
        await asyncio.to_thread(self._upsert_whatsapp_vote_snapshot_payload, record.to_supabase_payload())

    def _call_history_rpc(self, phone_numbers: list[str]) -> Any:
        return (
            self._get_client()
            .rpc(
                self._settings.supabase_poll_history_rpc,
                {'phone_numbers': phone_numbers},
            )
            .execute()
        )

    def _insert_prediction_payload(self, payload: list[dict[str, object]]) -> Any:
        return self._get_client().table('poll_prediction').insert(payload).execute()

    def _upsert_prediction_payload(self, payload: dict[str, object]) -> Any:
        return (
            self._get_client()
            .table('poll_prediction')
            .upsert(payload, on_conflict='mobile,source_filename')
            .execute()
        )

    def _upsert_whatsapp_poll_payload(self, payload: dict[str, object]) -> Any:
        return (
            self._get_client()
            .table(self._settings.supabase_whatsapp_poll_table)
            .upsert(payload, on_conflict='poll_message_id')
            .execute()
        )

    def _whatsapp_vote_event_exists(self, dedupe_key: str) -> bool:
        result = (
            self._get_client()
            .table(self._settings.supabase_whatsapp_vote_event_table)
            .select('dedupe_key')
            .eq('dedupe_key', dedupe_key)
            .limit(1)
            .execute()
        )
        rows = getattr(result, 'data', result) or []
        return bool(rows)

    def _insert_whatsapp_vote_event_payload(self, payload: dict[str, object]) -> Any:
        return self._get_client().table(self._settings.supabase_whatsapp_vote_event_table).insert(payload).execute()

    def _upsert_whatsapp_vote_snapshot_payload(self, payload: dict[str, object]) -> Any:
        return (
            self._get_client()
            .table(self._settings.supabase_whatsapp_vote_snapshot_table)
            .upsert(payload, on_conflict='poll_message_id,voter_jid')
            .execute()
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        if not self._settings.supabase_url or not self._settings.supabase_service_role_key:
            logger.error('Supabase client cannot be created because credentials are missing')
            raise RuntimeError('Supabase URL and service role key must be configured.')

        try:
            from supabase import create_client
        except ImportError as exc:
            logger.exception('Supabase package import failed')
            raise RuntimeError('Install the supabase package to use this repository.') from exc

        logger.info('Creating Supabase client')
        self._client = create_client(
            self._settings.supabase_url,
            self._settings.supabase_service_role_key,
        )
        return self._client

    @staticmethod
    def _parse_history(row: dict[str, Any]) -> UserHistory:
        return UserHistory(
            mobile=str(row.get('mobile') or ''),
            total_purchases=int(row.get('total_purchases') or 0),
            last_purchase_date=_parse_datetime(row.get('last_purchase_date')),
            purchases_last_30_days=int(row.get('purchases_last_30_days') or 0),
            purchases_last_60_days=int(row.get('purchases_last_60_days') or 0),
            total_yes_votes=int(row.get('total_yes_votes') or 0),
            last_vote_converted=_parse_optional_bool(row.get('last_vote_converted')),
            n_2_vote_converted=_parse_optional_bool(row.get('n_2_vote_converted')),
        )


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace('Z', '+00:00')
        return datetime.fromisoformat(normalized)
    return None


def _parse_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
