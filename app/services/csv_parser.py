import csv
import io
import logging
import re
from datetime import date
from pathlib import Path

from app.models.poll import PollMetadata, PollRow


Logger = logging.getLogger
logger = Logger(__name__)

DATE_SUFFIX_RE = re.compile(r"^(?P<slug>.+)-(?P<date>\d{4}-\d{2}-\d{2})$")
PHONE_SEPARATOR_RE = re.compile(r"[\s+\-().]+")


class CsvParseError(ValueError):
    pass


def parse_metadata_from_filename(filename: str | None) -> PollMetadata:
    logger.info("Parsing poll metadata from filename=%s", filename)
    if not filename:
        raise CsvParseError("Uploaded file must have a filename.")

    stem = Path(filename).stem
    match = DATE_SUFFIX_RE.match(stem)
    if not match:
        raise CsvParseError(
            "Filename must end with a poll date in YYYY-MM-DD format."
        )

    try:
        poll_date = date.fromisoformat(match.group("date"))
    except ValueError as exc:
        raise CsvParseError("Filename contains an invalid poll date.") from exc

    product_name = match.group("slug").replace("-", " ").strip()
    if not product_name:
        raise CsvParseError("Filename must include a product name before the date.")

    metadata = PollMetadata(
        product_name=product_name,
        poll_date=poll_date,
        source_filename=filename,
    )
    logger.info(
        "Parsed poll metadata product_name=%s poll_date=%s",
        metadata.product_name,
        metadata.poll_date.isoformat(),
    )
    return metadata


def normalize_phone(raw_name: str) -> str | None:
    cleaned = PHONE_SEPARATOR_RE.sub("", raw_name.strip())
    if cleaned.isdigit() and 10 <= len(cleaned) <= 15:
        return cleaned
    return None


def parse_vote(yes_value: str | None, no_value: str | None) -> tuple[int | None, str | None]:
    has_yes = bool((yes_value or "").strip())
    has_no = bool((no_value or "").strip())

    if has_yes and has_no:
        return None, "ambiguous_vote"
    if has_yes:
        return 1, None
    if has_no:
        return 0, None
    return None, "missing_vote"


def parse_whatsapp_poll_csv(csv_bytes: bytes) -> list[PollRow]:
    logger.info("Parsing WhatsApp poll CSV bytes=%s", len(csv_bytes))
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        raise CsvParseError("CSV is empty.")

    normalized_fields = {field.strip().lower() for field in reader.fieldnames}
    required_fields = {"name", "yes", "no"}
    if not required_fields.issubset(normalized_fields):
        raise CsvParseError("CSV must include Name, yes, and no columns.")

    rows: list[PollRow] = []
    skipped_summary_rows = 0
    for raw in reader:
        raw_name = (raw.get("Name") or raw.get("name") or "").strip()
        if raw_name.lower() == "total":
            skipped_summary_rows += 1
            continue

        vote, vote_error = parse_vote(raw.get("yes"), raw.get("no"))
        mobile = normalize_phone(raw_name)
        ignored_reason = vote_error

        if mobile is None:
            ignored_reason = "invalid_phone"
        elif vote_error is not None:
            ignored_reason = vote_error

        rows.append(
            PollRow(
                raw_name=raw_name,
                mobile=mobile,
                vote=vote,
                ignored_reason=ignored_reason,
            )
        )

    valid_phone_rows = sum(1 for row in rows if row.is_valid_phone_row)
    invalid_rows = sum(1 for row in rows if row.ignored_reason is not None)
    yes_rows = sum(1 for row in rows if row.vote == 1)
    no_rows = sum(1 for row in rows if row.vote == 0)
    logger.info(
        "Parsed WhatsApp CSV rows=%s valid_phone_rows=%s ignored_rows=%s yes_rows=%s no_rows=%s skipped_summary_rows=%s",
        len(rows),
        valid_phone_rows,
        invalid_rows,
        yes_rows,
        no_rows,
        skipped_summary_rows,
    )
    return rows
