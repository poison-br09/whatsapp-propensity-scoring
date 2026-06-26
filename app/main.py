import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client

from app.api.internal.routes.whatsapp import router as whatsapp_router
from app.api.v1.routes.admin import router as admin_router
from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.polls import router as polls_router
from app.api.v1.routes.whatsapp import router as whatsapp_management_router
from app.core.bridge_pool import BridgePool
from app.core.config import get_settings
from app.core.logging_setup import configure_logging
from app.services.keyword_analysis_state import KeywordAnalysisStateService
from app.services.propensity_scoring_state import PropensityScoringStateService

Logger = logging.getLogger
logger = Logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    bridge_pool = BridgePool(settings)
    app.state.bridge_pool = bridge_pool
    app.state.bridge_manager = None
    app.state.keyword_analysis_state = KeywordAnalysisStateService()
    app.state.propensity_scoring_state = PropensityScoringStateService()

    try:
        client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        result = client.table(settings.supabase_users_table).select('id,whatsapp_phone,target_group_jid').eq('is_active', True).execute()
        users = getattr(result, 'data', result) or []
        for user in users:
            if user.get('whatsapp_phone'):
                bridge_pool.start_bridge(user['id'], user['whatsapp_phone'], user.get('target_group_jid'))
    except Exception as exc:
        logger.warning('Could not load users for bridge pool startup: %s', exc)

    try:
        yield
    finally:
        bridge_pool.stop_all()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    logger.info('Creating FastAPI application')

    app = FastAPI(
        title='WhatsApp Propensity Scoring API',
        version='0.1.0',
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    app.include_router(polls_router, prefix='/api/v1')
    app.include_router(whatsapp_management_router, prefix='/api/v1')
    app.include_router(admin_router, prefix='/api/v1')
    app.include_router(auth_router, prefix='/api/v1')
    app.include_router(whatsapp_router)
    logger.info('Registered API routers')
    return app


app = create_app()
