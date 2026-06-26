from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field


class WhatsAppPollCreatedWebhook(BaseModel):
    group_jid: str
    poll_message_id: str
    poll_title: str
    poll_options: list[str] = Field(default_factory=list)
    poll_created_at_ms: int


class WhatsAppPollVoteWebhook(BaseModel):
    dedupe_key: str
    group_jid: str
    poll_message_id: str
    poll_title: str
    poll_options: list[str] = Field(default_factory=list)
    voter_jid: str
    voter_phone: str | None = None
    selected_options: list[str] = Field(default_factory=list)
    vote_timestamp_ms: int


class WhatsAppPollCreatedWebhookResponse(BaseModel):
    stored_poll: bool


class WhatsAppPollVoteWebhookResponse(BaseModel):
    duplicate: bool
    stored_event: bool
    stored_snapshot: bool
    normalized_vote: int | None = None
    prediction_score: float | None = None
    prediction_upserted: bool = False


class WhatsAppPairingRequest(BaseModel):
    phone_number: str = Field(min_length=6)


class WhatsAppPairingResponse(BaseModel):
    phone_number: str
    pairing_code: str
    status: str = 'pairing_code_generated'
    access_token: str | None = None  # set only when phone was newly linked


class WhatsAppSessionEventWebhook(BaseModel):
    event: str
    phone_number: str | None = None
    status_code: int | None = None
    occurred_at: str | None = None
    target_group_jid: str | None = None
    pairing_required: bool | None = None


class WhatsAppSessionEventWebhookResponse(BaseModel):
    accepted: bool
    email_sent: bool = False


class WhatsAppSessionStatusResponse(BaseModel):
    status: str
    phone_number: str | None = None
    target_group_jid: str | None = None
    last_event_at: str | None = None
    last_disconnect_code: int | None = None
    pairing_required: bool = False


class WhatsAppBackfillActionResponse(BaseModel):
    action: str
    accepted: bool


class WhatsAppMessageWebhook(BaseModel):
    group_jid: str
    sender_jid: str
    sender_name: str | None = None
    sender_phone: str | None = None
    receiver_phone: str | None = None
    message: str
    message_id: str
    message_timestamp_ms: int


class WhatsAppMessageWebhookResponse(BaseModel):
    matched: bool
    keywords: list[str] = Field(default_factory=list)
    stored: bool = False


class WhatsAppKeywordAnalysisActionResponse(BaseModel):
    action: str
    enabled: bool


class WhatsAppPropensityActionResponse(BaseModel):
    action: str
    enabled: bool


class WhatsAppKeywordAddRequest(BaseModel):
    keywords: list[str] = Field(min_length=1)

class WhatsAppKeywordAddResult(BaseModel):
    keyword: str
    added: bool
    already_existed: bool

class WhatsAppKeywordAddResponse(BaseModel):
    results: list[WhatsAppKeywordAddResult]


class WhatsAppKeywordItem(BaseModel):
    id: str
    keyword: str
    is_active: bool


class WhatsAppKeywordListResponse(BaseModel):
    keywords: list[WhatsAppKeywordItem]


class WhatsAppKeywordDeleteRequest(BaseModel):
    keywords: list[str] = Field(min_length=1)


class WhatsAppKeywordDeleteResult(BaseModel):
    keyword: str
    deleted: bool


class WhatsAppKeywordDeleteResponse(BaseModel):
    results: list[WhatsAppKeywordDeleteResult]


class WhatsAppMatchItem(BaseModel):
    keyword: str
    sender_name: str | None
    sender_phone: str | None
    message: str
    message_date: str
    receiver_phone: str | None


class WhatsAppMatchQueryResponse(BaseModel):
    matches: list[WhatsAppMatchItem]
    total: int


class WhatsAppKeywordControlRequest(BaseModel):
    keywords: list[str] = Field(min_length=1)
    enabled: bool


class WhatsAppKeywordControlResult(BaseModel):
    keyword: str
    enabled: bool
    found: bool


class WhatsAppKeywordControlResponse(BaseModel):
    updated: list[WhatsAppKeywordControlResult]


@dataclass(slots=True)
class WhatsAppPollRecord:
    group_jid: str
    poll_message_id: str
    poll_title: str
    poll_options: list[str]
    poll_created_at: datetime

    def to_supabase_payload(self) -> dict[str, object]:
        return {
            'group_jid': self.group_jid,
            'poll_message_id': self.poll_message_id,
            'poll_title': self.poll_title,
            'poll_options': self.poll_options,
            'poll_created_at': self.poll_created_at.isoformat(),
        }


@dataclass(slots=True)
class WhatsAppPollVoteEventRecord:
    dedupe_key: str
    group_jid: str
    poll_message_id: str
    poll_title: str
    voter_jid: str
    voter_phone: str | None
    selected_options: list[str]
    normalized_vote: int | None
    vote_timestamp: datetime

    def to_supabase_payload(self) -> dict[str, object]:
        return {
            'dedupe_key': self.dedupe_key,
            'group_jid': self.group_jid,
            'poll_message_id': self.poll_message_id,
            'poll_title': self.poll_title,
            'voter_jid': self.voter_jid,
            'voter_phone': self.voter_phone,
            'selected_options': self.selected_options,
            'normalized_vote': self.normalized_vote,
            'vote_timestamp': self.vote_timestamp.isoformat(),
        }


@dataclass(slots=True)
class WhatsAppKeywordMatchRecord:
    keyword_id: str
    keyword: str
    group_jid: str
    sender_jid: str
    sender_name: str | None
    sender_phone: str | None
    receiver_phone: str | None
    message: str
    message_id: str
    message_date: datetime

    def to_supabase_payload(self) -> dict[str, object]:
        return {
            'keyword_id': self.keyword_id,
            'keyword': self.keyword,
            'group_jid': self.group_jid,
            'sender_jid': self.sender_jid,
            'sender_name': self.sender_name,
            'sender_phone': self.sender_phone,
            'receiver_phone': self.receiver_phone,
            'message': self.message,
            'message_id': self.message_id,
            'message_date': self.message_date.isoformat(),
        }


@dataclass(slots=True)
class WhatsAppPollVoteSnapshotRecord:
    group_jid: str
    poll_message_id: str
    poll_title: str
    voter_jid: str
    voter_phone: str | None
    selected_options: list[str]
    normalized_vote: int | None
    last_vote_timestamp: datetime

    def to_supabase_payload(self) -> dict[str, object]:
        return {
            'group_jid': self.group_jid,
            'poll_message_id': self.poll_message_id,
            'poll_title': self.poll_title,
            'voter_jid': self.voter_jid,
            'voter_phone': self.voter_phone,
            'selected_options': self.selected_options,
            'normalized_vote': self.normalized_vote,
            'last_vote_timestamp': self.last_vote_timestamp.isoformat(),
        }
