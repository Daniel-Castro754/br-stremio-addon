import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.manifest import get_manifest
from app.models.config import settings
from app.routes.configure import router as configure_router
from app.routes.stream import aggregator, router as stream_router
from app.services.cache import cache

# Configura logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await cache.init()
    logger.info("=" * 50)
    logger.info("🇧🇷 BR Streams iniciado!")
    logger.info(f"📺 Configuração: {settings.BASE_URL}/configure")
    logger.info(f"📋 Manifest: {settings.BASE_URL}/manifest.json")
    logger.info(f"💾 Storage backend: {settings.STORAGE_BACKEND}")
    logger.info(f"⏱  Scraper timeout: {settings.SCRAPER_TIMEOUT_SECONDS}s")
    logger.info("=" * 50)
    yield
    # Shutdown
    await cache.delete_expired()
    await cache.close()
    logger.info("Cache fechado.")


# Cria a aplicação FastAPI
app = FastAPI(title="BR Streams 🇧🇷", lifespan=lifespan)

# CORS liberado para todas as origens (necessário para Stremio web)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Inclui as rotas
app.include_router(configure_router)
app.include_router(stream_router)


@app.get("/")
async def root():
    """Redireciona para página de configuração"""
    return RedirectResponse(url="/configure")


@app.get("/health")
async def health():
    """Diagnóstico sem fazer novas requisições às fontes."""
    return {
        "status": "ok",
        "sources": aggregator.get_source_health(),
    }


@app.get("/manifest.json")
async def manifest():
    """Retorna o manifest do addon"""
    return get_manifest()


@app.get("/{rd_token}/manifest.json")
async def manifest_with_token(rd_token: str):
    """Retorna manifest no modo Real-Debrid."""
    return get_manifest()


@app.get("/hybrid/{rd_token}/manifest.json")
async def manifest_hybrid(rd_token: str):
    """Retorna manifest no modo híbrido: Real-Debrid + P2P."""
    return get_manifest()


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL,
        reload=True,
    )
