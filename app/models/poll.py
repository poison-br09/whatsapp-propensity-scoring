from dataclasses import dataclass
from datetime import date, datetime


@dataclass(slots=True)
class PollMetadata:
    product_name: str
    poll_date: date
    source_filename: str


@dataclass(slots=True)
class PollRow:
    raw_name: str
    mobile: str | None
    vote: int | None
    ignored_reason: str | None = None
    prediction_percentage: float | None = None

    @property
    def is_valid_phone_row(self) -> bool:
        return self.mobile is not None and self.vote is not None

    @property
    def is_scored_candidate(self) -> bool:
        return self.mobile is not None and self.vote == 1


@dataclass(slots=True)
class UserHistory:
    mobile: str
    total_purchases: int = 0
    last_purchase_date: datetime | None = None
    purchases_last_30_days: int = 0
    purchases_last_60_days: int = 0
    total_yes_votes: int = 0
    last_vote_converted: bool | None = None
    n_2_vote_converted: bool | None = None


@dataclass(slots=True)
class PollPredictionRecord:
    mobile: str
    product_name: str
    poll_date: date
    vote: int
    prediction_score: float | None
    source_filename: str

    def to_supabase_payload(self) -> dict[str, object]:
        return {
            "mobile": self.mobile,
            "product_name": self.product_name,
            "poll_date": self.poll_date.isoformat(),
            "vote": self.vote,
            "prediction_score": self.prediction_score,
            "source_filename": self.source_filename,
        }

