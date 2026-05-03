import logging
import secrets

from fastapi import APIRouter, Depends, File, HTTPException, Query, Security, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader

from app.core.config import Settings, get_settings
from app.repositories.supabase_poll_repository import SupabasePollRepository
from app.services.csv_parser import CsvParseError
from app.services.poll_scoring_service import PollScoringService

Logger = logging.getLogger
logger = Logger(__name__)

router = APIRouter(prefix="/polls", tags=["polls"])
api_key_header = APIKeyHeader(
    name="x-api-key",
    auto_error=False,
    description="API key required to score WhatsApp poll CSV uploads.",
)


def get_poll_repository(
    settings: Settings = Depends(get_settings),
) -> SupabasePollRepository:
    return SupabasePollRepository(settings)


def get_poll_scoring_service(
    repository: SupabasePollRepository = Depends(get_poll_repository),
) -> PollScoringService:
    return PollScoringService(repository)


def require_api_key(
    x_api_key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.api_key:
        logger.error("API key protection is not configured")
        raise HTTPException(status_code=500, detail="API key is not configured.")

    if x_api_key is None:
        logger.warning("Rejected score request: missing x-api-key header")
        raise HTTPException(status_code=401, detail="Missing x-api-key header.")

    if not secrets.compare_digest(x_api_key, settings.api_key):
        logger.warning("Rejected score request: invalid x-api-key header")
        raise HTTPException(status_code=401, detail="Invalid API key.")


@router.post(
    "/score",
    responses={
        200: {"content": {"application/json": {}}},
        400: {"description": "Invalid CSV upload or filename."},
        401: {"description": "Missing or invalid x-api-key header."},
    },
)
async def score_poll_csv(
    file: UploadFile = File(...),
    threshold: float = Query(..., description="Prediction score threshold (0–100). Rows with prediction_percentage >= threshold are counted."),
    _: None = Depends(require_api_key),
    service: PollScoringService = Depends(get_poll_scoring_service),
) -> Response:
    logger.info("Received poll scoring request filename=%s threshold=%s", file.filename, threshold)
    csv_bytes = await file.read()
    logger.info(
        "Read uploaded poll CSV filename=%s bytes=%s",
        file.filename,
        len(csv_bytes),
    )

    try:
        result = await service.score_csv(csv_bytes, file.filename, threshold)
    except CsvParseError as exc:
        logger.warning(
            "Poll scoring request rejected filename=%s reason=%s",
            file.filename,
            exc,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    output_filename = _result_filename(file.filename)
    logger.info(
        "Poll scoring request completed filename=%s output_filename=%s bytes=%s above_threshold=%s",
        file.filename,
        output_filename,
        len(result.csv.encode("utf-8")),
        result.above_threshold_count,
    )
    return JSONResponse(
        content={
            "above_threshold_count": result.above_threshold_count,
            "filename": output_filename,
            "csv": result.csv,
        }
    )


def _result_filename(filename: str | None) -> str:
    if not filename:
        return "scored-poll.csv"
    if filename.lower().endswith(".csv"):
        return f"{filename[:-4]}-scored.csv"
    return f"{filename}-scored.csv"
