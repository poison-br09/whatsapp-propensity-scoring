import csv
import io
import logging

from app.models.poll import PollPredictionRecord, UserHistory
from app.repositories.supabase_poll_repository import SupabasePollRepository
from app.services.csv_parser import parse_metadata_from_filename, parse_whatsapp_poll_csv
from app.services.scoring import calculate_prediction_score


OUTPUT_COLUMNS = [
    "raw_name",
    "mobile",
    "vote",
    "prediction_percentage",
    "product_name",
    "poll_date",
    "ignored_reason",
]


Logger = logging.getLogger
logger = Logger(__name__)


class PollScoringService:
    def __init__(self, repository: SupabasePollRepository) -> None:
        self._repository = repository

    async def score_csv(self, csv_bytes: bytes, filename: str | None) -> str:
        logger.info("Starting poll scoring pipeline filename=%s", filename)
        metadata = parse_metadata_from_filename(filename)
        rows = parse_whatsapp_poll_csv(csv_bytes)
        logger.info(
            "Poll CSV parsed filename=%s product_name=%s poll_date=%s rows=%s",
            filename,
            metadata.product_name,
            metadata.poll_date.isoformat(),
            len(rows),
        )

        yes_mobiles = sorted({row.mobile for row in rows if row.is_scored_candidate})
        logger.info(
            "Fetching Supabase history for scored candidates filename=%s yes_candidates=%s",
            filename,
            len(yes_mobiles),
        )
        histories = await self._repository.get_user_history(
            [mobile for mobile in yes_mobiles if mobile is not None]
        )
        logger.info(
            "Fetched Supabase history filename=%s requested=%s returned=%s",
            filename,
            len(yes_mobiles),
            len(histories),
        )

        scored_count = 0
        for row in rows:
            if row.is_scored_candidate and row.mobile is not None:
                history = histories.get(row.mobile, UserHistory(mobile=row.mobile))
                row.prediction_percentage = calculate_prediction_score(
                    history,
                    metadata.poll_date,
                )
                scored_count += 1
        logger.info("Applied prediction scores filename=%s scored_rows=%s", filename, scored_count)

        prediction_records = [
            PollPredictionRecord(
                mobile=row.mobile,
                product_name=metadata.product_name,
                poll_date=metadata.poll_date,
                vote=row.vote,
                prediction_score=row.prediction_percentage,
                source_filename=metadata.source_filename,
            )
            for row in rows
            if row.is_valid_phone_row and row.mobile is not None and row.vote is not None
        ]
        logger.info(
            "Inserting poll prediction records filename=%s records=%s",
            filename,
            len(prediction_records),
        )
        await self._repository.insert_predictions(prediction_records)
        logger.info("Inserted poll prediction records filename=%s", filename)

        output_csv = _render_output_csv(rows, metadata.product_name, metadata.poll_date.isoformat())
        logger.info(
            "Rendered output CSV filename=%s output_bytes=%s",
            filename,
            len(output_csv.encode("utf-8")),
        )
        return output_csv


def _render_output_csv(rows: list, product_name: str, poll_date: str) -> str:
    logger.info("Rendering output CSV rows=%s product_name=%s poll_date=%s", len(rows), product_name, poll_date)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=OUTPUT_COLUMNS)
    writer.writeheader()

    for row in rows:
        writer.writerow(
            {
                "raw_name": row.raw_name,
                "mobile": row.mobile or "",
                "vote": "" if row.vote is None else row.vote,
                "prediction_percentage": (
                    "" if row.prediction_percentage is None else row.prediction_percentage
                ),
                "product_name": product_name,
                "poll_date": poll_date,
                "ignored_reason": row.ignored_reason or "",
            }
        )

    return output.getvalue()
