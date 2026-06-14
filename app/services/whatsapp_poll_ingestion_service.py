import logging
import re
from datetime import UTC, datetime

from app.models.poll import PollPredictionRecord, UserHistory
from app.models.whatsapp import (
    WhatsAppPollCreatedWebhook,
    WhatsAppPollCreatedWebhookResponse,
    WhatsAppPollRecord,
    WhatsAppPollVoteEventRecord,
    WhatsAppPollVoteSnapshotRecord,
    WhatsAppPollVoteWebhook,
    WhatsAppPollVoteWebhookResponse,
)
from app.repositories.supabase_poll_repository import SupabasePollRepository
from app.services.scoring import calculate_prediction_score

Logger = logging.getLogger
logger = Logger(__name__)

POSITIVE_TOKENS = {
    'yes',
    'y',
    'interested',
    'buy',
    'will buy',
    'i will buy',
    'want',
    'count me in',
    'in',
    'go ahead',
    'order',
}
NEGATIVE_TOKENS = {
    'no',
    'n',
    'not interested',
    'wont buy',
    'will not buy',
    'dont want',
    'do not want',
    'skip',
    'not now',
    'out',
}


class WhatsAppPollIngestionService:
    def __init__(self, repository: SupabasePollRepository) -> None:
        self._repository = repository

    async def ingest_poll_created(
        self,
        payload: WhatsAppPollCreatedWebhook,
    ) -> WhatsAppPollCreatedWebhookResponse:
        record = WhatsAppPollRecord(
            group_jid=payload.group_jid,
            poll_message_id=payload.poll_message_id,
            poll_title=payload.poll_title,
            poll_options=payload.poll_options,
            poll_created_at=_timestamp_from_ms(payload.poll_created_at_ms),
        )
        await self._repository.upsert_whatsapp_poll(record)
        logger.info(
            'Upserted WhatsApp poll definition poll_message_id=%s options=%s',
            payload.poll_message_id,
            payload.poll_options,
        )
        return WhatsAppPollCreatedWebhookResponse(stored_poll=True)

    async def ingest_vote(self, payload: WhatsAppPollVoteWebhook, *, scoring_enabled: bool = True) -> WhatsAppPollVoteWebhookResponse:
        vote_timestamp = _timestamp_from_ms(payload.vote_timestamp_ms)
        poll_record = WhatsAppPollRecord(
            group_jid=payload.group_jid,
            poll_message_id=payload.poll_message_id,
            poll_title=payload.poll_title,
            poll_options=payload.poll_options,
            poll_created_at=vote_timestamp,
        )
        await self._repository.upsert_whatsapp_poll(poll_record)

        normalized_vote = _normalize_vote(payload.poll_options, payload.selected_options)
        event = WhatsAppPollVoteEventRecord(
            dedupe_key=payload.dedupe_key,
            group_jid=payload.group_jid,
            poll_message_id=payload.poll_message_id,
            poll_title=payload.poll_title,
            voter_jid=payload.voter_jid,
            voter_phone=payload.voter_phone,
            selected_options=payload.selected_options,
            normalized_vote=normalized_vote,
            vote_timestamp=vote_timestamp,
        )

        inserted = await self._repository.create_whatsapp_poll_vote_event_if_new(event)
        if not inserted:
            logger.info('Skipping duplicate WhatsApp poll vote dedupe_key=%s', payload.dedupe_key)
            return WhatsAppPollVoteWebhookResponse(
                duplicate=True,
                stored_event=False,
                stored_snapshot=False,
            )

        snapshot = WhatsAppPollVoteSnapshotRecord(
            group_jid=payload.group_jid,
            poll_message_id=payload.poll_message_id,
            poll_title=payload.poll_title,
            voter_jid=payload.voter_jid,
            voter_phone=payload.voter_phone,
            selected_options=payload.selected_options,
            normalized_vote=normalized_vote,
            last_vote_timestamp=vote_timestamp,
        )
        await self._repository.upsert_whatsapp_poll_vote_snapshot(snapshot)

        prediction_score: float | None = None
        prediction_upserted = False
        if not scoring_enabled:
            logger.info(
                'Propensity scoring disabled, skipping prediction dedupe_key=%s',
                payload.dedupe_key,
            )
        elif payload.voter_phone and normalized_vote is not None:
            if normalized_vote == 1:
                history_map = await self._repository.get_user_history([payload.voter_phone])
                history = history_map.get(payload.voter_phone, UserHistory(mobile=payload.voter_phone))
                prediction_score = float(calculate_prediction_score(history, vote_timestamp.date()))

            record = PollPredictionRecord(
                mobile=payload.voter_phone,
                product_name=payload.poll_title or 'WhatsApp Poll',
                poll_date=vote_timestamp.date(),
                vote=normalized_vote,
                prediction_score=prediction_score,
                source_filename=f'whatsapp:{payload.poll_message_id}',
            )
            await self._repository.upsert_prediction(record)
            prediction_upserted = True
            logger.info(
                'Upserted live poll prediction mobile=%s poll_message_id=%s vote=%s',
                payload.voter_phone,
                payload.poll_message_id,
                normalized_vote,
            )
        else:
            logger.info(
                'Stored WhatsApp poll vote without scoring dedupe_key=%s normalized_vote=%s selected_options=%s',
                payload.dedupe_key,
                normalized_vote,
                payload.selected_options,
            )

        return WhatsAppPollVoteWebhookResponse(
            duplicate=False,
            stored_event=True,
            stored_snapshot=True,
            normalized_vote=normalized_vote,
            prediction_score=prediction_score,
            prediction_upserted=prediction_upserted,
        )


def _timestamp_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _normalize_vote(poll_options: list[str], selected_options: list[str]) -> int | None:
    mapping = _infer_binary_mapping(poll_options)
    if not mapping or len(selected_options) != 1:
        return None

    selected = _canonicalize(selected_options[0])
    return mapping.get(selected)


def _infer_binary_mapping(poll_options: list[str]) -> dict[str, int] | None:
    normalized_options = [_canonicalize(option) for option in poll_options if _canonicalize(option)]
    if len(normalized_options) != 2:
        return None

    classified: dict[str, int] = {}
    for option in normalized_options:
        if option in POSITIVE_TOKENS:
            classified[option] = 1
        elif option in NEGATIVE_TOKENS:
            classified[option] = 0
        else:
            return None

    if set(classified.values()) != {0, 1}:
        return None

    return classified


def _canonicalize(value: str) -> str:
    compact = re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip()
    return re.sub(r'\s+', ' ', compact)
