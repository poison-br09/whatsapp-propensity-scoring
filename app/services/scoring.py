import logging
from datetime import date, datetime, time

from app.models.poll import UserHistory


Logger = logging.getLogger
logger = Logger(__name__)


def calculate_prediction_score(history: UserHistory, poll_date: date) -> float:
    logger.info(
        "Calculating prediction mobile=%s total_purchases=%s total_yes_votes=%s",
        _mask_mobile(history.mobile),
        history.total_purchases,
        history.total_yes_votes,
    )

    if history.total_yes_votes <= 0:
        score = float(_score_first_time_voter(history, poll_date))
        logger.info(
            "Calculated first-time voter score mobile=%s score=%s",
            _mask_mobile(history.mobile),
            score,
        )
        return score

    score = 20
    score += _purchase_velocity_points(history)
    score += _recency_points(history, poll_date)
    score += _behavior_points(history)

    if history.total_yes_votes > 0:
        score += min((history.total_purchases / history.total_yes_votes) * 10, 10)

    final_score = round(_clamp(score), 2)
    logger.info(
        "Calculated returning voter score mobile=%s raw_score=%s final_score=%s",
        _mask_mobile(history.mobile),
        round(score, 2),
        final_score,
    )
    return final_score


def _score_first_time_voter(history: UserHistory, poll_date: date) -> int:
    score = 50
    days_since_purchase = _days_since_last_purchase(history, poll_date)

    if days_since_purchase is None:
        return score
    if days_since_purchase < 15:
        return score + 20
    if days_since_purchase < 30:
        return score + 15
    return score + 10


def _purchase_velocity_points(history: UserHistory) -> int:
    if history.purchases_last_30_days >= 3:
        return 20
    if history.purchases_last_60_days >= 5:
        return 10
    return 0


def _recency_points(history: UserHistory, poll_date: date) -> int:
    days_since_purchase = _days_since_last_purchase(history, poll_date)
    if days_since_purchase is None:
        return 0
    if days_since_purchase < 15:
        return 15
    if days_since_purchase < 30:
        return 10
    return 0


def _behavior_points(history: UserHistory) -> int:
    score = 0

    if history.last_vote_converted is True:
        score += 20
    elif history.last_vote_converted is False:
        score -= 5

    if history.n_2_vote_converted is True:
        score += 15
    elif history.n_2_vote_converted is False:
        score -= 5

    return score


def _days_since_last_purchase(history: UserHistory, poll_date: date) -> int | None:
    if history.last_purchase_date is None:
        return None

    poll_datetime = datetime.combine(poll_date, time.min)
    return (poll_datetime.date() - history.last_purchase_date.date()).days


def _clamp(score: float) -> float:
    return max(0.0, min(100.0, score))


def _mask_mobile(mobile: str) -> str:
    if len(mobile) <= 4:
        return "****"
    return f"***{mobile[-4:]}"
