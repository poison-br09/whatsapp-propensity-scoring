import logging

from fastapi import FastAPI

from app.api.v1.routes.polls import router as polls_router
Logger = logging.getLogger
logger = Logger(__name__)


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info("Creating FastAPI application")

    app = FastAPI(
        title="WhatsApp Propensity Scoring API",
        version="0.1.0",
    )
    app.include_router(polls_router, prefix="/api/v1")
    logger.info("Registered API routers")
    return app


app = create_app()
