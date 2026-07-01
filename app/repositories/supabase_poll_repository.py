import asyncio
import logging
from datetime import datetime
from typing import Any

from app.core.config import Settings
from app.models.poll import PollPredictionRecord, UserHistory
from app.models.whatsapp import WhatsAppKeywordMatchRecord, WhatsAppPollRecord, WhatsAppPollVoteEventRecord, WhatsAppPollVoteSnapshotRecord

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

    async def store_keyword_match(self, record: WhatsAppKeywordMatchRecord) -> None:
        await asyncio.to_thread(self._insert_keyword_match_payload, record.to_supabase_payload())

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

    async def query_polls(
        self,
        *,
        group_jid: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        return await asyncio.to_thread(self._query_polls, group_jid, limit, offset)

    async def query_poll_votes(self, poll_message_id: str) -> list[dict]:
        return await asyncio.to_thread(self._query_poll_votes, poll_message_id)

    async def get_active_keywords(self) -> list[dict[str, str]]:
        rows = await asyncio.to_thread(self._fetch_active_keywords)
        return [{'id': str(row['id']), 'keyword': str(row['keyword'])} for row in rows]

    async def get_all_keywords(self) -> list[dict]:
        rows = await asyncio.to_thread(self._fetch_all_keywords)
        return [{'id': str(row['id']), 'keyword': str(row['keyword']), 'is_active': bool(row['is_active'])} for row in rows]

    async def delete_keywords(self, keywords: list[str]) -> list[dict]:
        return await asyncio.to_thread(self._delete_keywords, keywords)

    async def query_matches(
        self,
        *,
        sender_name: str | None = None,
        sender_phone: str | None = None,
        keywords: list[str] | None = None,
        receiver_phone: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        return await asyncio.to_thread(
            self._query_matches,
            sender_name, sender_phone, keywords, receiver_phone, date_from, date_to, limit, offset,
        )

    async def add_keywords(self, keywords: list[str]) -> list[dict]:
        return await asyncio.to_thread(self._add_keywords, keywords)

    async def fetch_users(self) -> list[dict]:
        return await asyncio.to_thread(self._fetch_users)

    async def deactivate_user_db(self, user_id: str) -> None:
        await asyncio.to_thread(self._deactivate_user, user_id)

    async def activate_user_db(self, user_id: str) -> dict | None:
        return await asyncio.to_thread(self._activate_user, user_id)

    async def delete_user_db(self, user_id: str) -> dict | None:
        return await asyncio.to_thread(self._delete_user, user_id)

    async def set_keywords_active(self, keywords: list[str], enabled: bool) -> list[dict[str, str]]:
        rows = await asyncio.to_thread(self._update_keywords_active, keywords, enabled)
        return [{'keyword': str(row['keyword']), 'enabled': str(row['is_active'])} for row in rows]

    def _fetch_users(self) -> list[dict]:
        result = (
            self._get_client()
            .table(self._settings.supabase_users_table)
            .select('id,username,whatsapp_phone,target_group_jid,role,is_active,created_at')
            .order('created_at')
            .execute()
        )
        return getattr(result, 'data', result) or []

    def _deactivate_user(self, user_id: str) -> None:
        (
            self._get_client()
            .table(self._settings.supabase_users_table)
            .update({'is_active': False})
            .eq('id', user_id)
            .execute()
        )

    def _activate_user(self, user_id: str) -> dict | None:
        result = (
            self._get_client()
            .table(self._settings.supabase_users_table)
            .update({'is_active': True})
            .eq('id', user_id)
            .execute()
        )
        rows = getattr(result, 'data', result) or []
        return rows[0] if rows else None

    def _delete_user(self, user_id: str) -> dict | None:
        result = (
            self._get_client()
            .table(self._settings.supabase_users_table)
            .delete()
            .eq('id', user_id)
            .execute()
        )
        rows = getattr(result, 'data', result) or []
        return rows[0] if rows else None

    def _fetch_all_keywords(self) -> list[Any]:
        result = (
            self._get_client()
            .table(self._settings.supabase_whatsapp_keywords_table)
            .select('id,keyword,is_active')
            .order('keyword')
            .execute()
        )
        return getattr(result, 'data', result) or []

    def _delete_keywords(self, keywords: list[str]) -> list[dict]:
        normalized = [kw.lower().strip() for kw in keywords]
        result = (
            self._get_client()
            .table(self._settings.supabase_whatsapp_keywords_table)
            .delete()
            .in_('keyword', normalized)
            .execute()
        )
        deleted_set = {row['keyword'].lower() for row in (getattr(result, 'data', result) or [])}
        return [{'keyword': kw, 'deleted': kw in deleted_set} for kw in normalized]

    def _query_matches(
        self,
        sender_name: str | None,
        sender_phone: str | None,
        keywords: list[str] | None,
        receiver_phone: str | None,
        date_from: str | None,
        date_to: str | None,
        limit: int,
        offset: int,
    ) -> dict:
        client = self._get_client()
        table = self._settings.supabase_whatsapp_keyword_match_table
        q = client.table(table).select(
            'keyword,sender_name,sender_phone,message,message_date,receiver_phone',
            count='exact',
        )
        if sender_name:
            q = q.ilike('sender_name', f'%{sender_name}%')
        if sender_phone:
            q = q.ilike('sender_phone', f'%{sender_phone}%')
        if keywords:
            normalized = [kw.lower().strip() for kw in keywords]
            q = q.in_('keyword', normalized)
        if receiver_phone:
            q = q.eq('receiver_phone', receiver_phone.strip())
        if date_from:
            q = q.gte('message_date', date_from)
        if date_to:
            q = q.lte('message_date', date_to)
        result = q.order('message_date', desc=True).range(offset, offset + limit - 1).execute()
        rows = getattr(result, 'data', result) or []
        total = getattr(result, 'count', None) or 0
        return {'rows': rows, 'total': total}

    def _query_polls(self, group_jid: str | None, limit: int, offset: int) -> dict:
        client = self._get_client()
        q = client.table(self._settings.supabase_whatsapp_poll_table).select(
            'poll_message_id,poll_title,poll_options,group_jid,group_name,poll_created_at,receiver_phone',
            count='exact',
        )
        if group_jid:
            q = q.eq('group_jid', group_jid)
        result = q.order('poll_created_at', desc=True).range(offset, offset + limit - 1).execute()
        rows = getattr(result, 'data', result) or []
        total = getattr(result, 'count', None) or 0
        return {'rows': rows, 'total': total}

    def _query_poll_votes(self, poll_message_id: str) -> list[dict]:
        result = (
            self._get_client()
            .table(self._settings.supabase_whatsapp_vote_snapshot_table)
            .select('voter_jid,voter_phone,selected_options,normalized_vote,last_vote_timestamp,receiver_phone')
            .eq('poll_message_id', poll_message_id)
            .order('last_vote_timestamp', desc=True)
            .execute()
        )
        return getattr(result, 'data', result) or []

    def _fetch_active_keywords(self) -> list[Any]:
        result = (
            self._get_client()
            .table(self._settings.supabase_whatsapp_keywords_table)
            .select('id,keyword')
            .eq('is_active', True)
            .execute()
        )
        return getattr(result, 'data', result) or []

    def _update_keywords_active(self, keywords: list[str], enabled: bool) -> list[Any]:
        result = (
            self._get_client()
            .table(self._settings.supabase_whatsapp_keywords_table)
            .update({'is_active': enabled})
            .in_('keyword', keywords)
            .execute()
        )
        return getattr(result, 'data', result) or []

    def _add_keywords(self, keywords: list[str]) -> list[dict]:
        client = self._get_client()
        table = self._settings.supabase_whatsapp_keywords_table
        normalized = [kw.lower().strip() for kw in keywords]

        existing_result = (
            client.table(table)
            .select('keyword')
            .in_('keyword', normalized)
            .execute()
        )
        existing = {row['keyword'].lower() for row in (getattr(existing_result, 'data', existing_result) or [])}

        new_keywords = [kw for kw in normalized if kw not in existing]
        if new_keywords:
            client.table(table).insert(
                [{'keyword': kw, 'is_active': True} for kw in new_keywords]
            ).execute()

        return [
            {'keyword': kw, 'added': kw not in existing, 'already_existed': kw in existing}
            for kw in normalized
        ]

    def _insert_keyword_match_payload(self, payload: dict[str, object]) -> Any:
        client = self._get_client()
        table = self._settings.supabase_whatsapp_keyword_match_table
        client.table(table).upsert(payload, on_conflict='message_id,keyword_id', ignore_duplicates=True).execute()
        sender_update = {k: payload[k] for k in ('sender_jid', 'sender_name', 'sender_phone') if payload.get(k)}
        if sender_update:
            (
                client.table(table)
                .update(sender_update)
                .eq('message_id', payload['message_id'])
                .eq('keyword_id', payload['keyword_id'])
                .eq('sender_jid', '')
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
