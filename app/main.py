import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.internal.routes.whatsapp import router as whatsapp_router
from app.api.v1.routes.admin import router as admin_router
from app.api.v1.routes.polls import router as polls_router
from app.api.v1.routes.whatsapp import router as whatsapp_management_router
from app.core.config import get_settings
from app.core.logging_setup import configure_logging
from app.core.whatsapp_bridge import BaileysBridgeProcessManager
from app.services.keyword_analysis_state import KeywordAnalysisStateService
from app.services.propensity_scoring_state import PropensityScoringStateService
from app.services.whatsapp_session_state import WhatsAppSessionStateService

Logger = logging.getLogger
logger = Logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    bridge_manager = BaileysBridgeProcessManager(settings)
    app.state.whatsapp_session_state = WhatsAppSessionStateService()
    app.state.keyword_analysis_state = KeywordAnalysisStateService()
    app.state.propensity_scoring_state = PropensityScoringStateService()
    app.state.bridge_manager = bridge_manager
    bridge_manager.start()
    try:
        yield
    finally:
        bridge_manager.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    logger.info('Creating FastAPI application')

    app = FastAPI(
        title='WhatsApp Propensity Scoring API',
        version='0.1.0',
        lifespan=lifespan,
    )
    app.include_router(polls_router, prefix='/api/v1')
    app.include_router(whatsapp_management_router, prefix='/api/v1')
    app.include_router(admin_router, prefix='/api/v1')
    app.include_router(whatsapp_router)
    logger.info('Registered API routers')
    return app


app = create_app()
