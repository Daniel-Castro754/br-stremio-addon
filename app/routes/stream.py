import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.services.cache import cache
from app.services.real_debrid import (
    RealDebridPlaybackNotReadyError,
    RealDebridResolveError,
    RealDebridService,
)
from app.services.stream_aggregator import PLAY_SESSION_TTL_SECONDS, StreamAggregator

logger = logging.getLogger(__name__)
router = APIRouter()
PLAY_NOT_READY_RETRY_AFTER_SECONDS = 2
# resolved_url e guardada na mesma play session.
# Por isso o TTL precisa ficar alinhado ao TTL da sessao para nao encurta-la.
PLAY_RESOLVED_URL_TTL_SECONDS = PLAY_SESSION_TTL_SECONDS

# Instancia global do agregador.
aggregator = StreamAggregator()


def _play_ref(play_id: str) -> str:
    """Reduz o identificador nos logs para evitar ruido desnecessario."""
    return play_id[:8]


def _request_base_url(request: Request) -> str:
    """Deriva a base URL da request atual (scheme + host), sem trailing slash."""
    return str(request.base_url).rstrip("/")


@router.get("/{rd_token}/stream/{type}/{id}.json")
async def get_streams_with_rd(rd_token: str, type: str, id: str, request: Request) -> dict:
    """
    Endpoint de streams com token RD no path.

    Compatibilidade atual:
      O token continua no path do manifest/stream. Este modulo nunca registra
      o token em logs manuais; o risco residual fica nos access logs da infra.
    """
    req_id = uuid.uuid4().hex[:8]
    t0 = time.monotonic()
    imdb_id = id.split(":")[0] if ":" in id else id.replace(".json", "")
    token = rd_token if rd_token.lower() != "none" else None

    streams = await aggregator.get_streams(
        imdb_id=imdb_id,
        stremio_id=id.replace(".json", ""),
        type=type,
        req_id=req_id,
        rd_token=token,
        request_base_url=_request_base_url(request),
    )

    elapsed = (time.monotonic() - t0) * 1000
    logger.info(
        f"[{req_id}] [STREAM] {type}/{imdb_id} -> {len(streams)} resultados ({elapsed:.0f}ms)"
    )
    return {"streams": [stream.model_dump(exclude_none=True) for stream in streams]}


@router.get("/stream/{type}/{id}.json")
async def get_streams(type: str, id: str, request: Request) -> dict:
    """Endpoint de streams sem token RD."""
    req_id = uuid.uuid4().hex[:8]
    t0 = time.monotonic()
    imdb_id = id.split(":")[0] if ":" in id else id.replace(".json", "")

    streams = await aggregator.get_streams(
        imdb_id=imdb_id,
        stremio_id=id.replace(".json", ""),
        type=type,
        req_id=req_id,
        rd_token=None,
        request_base_url=_request_base_url(request),
    )

    elapsed = (time.monotonic() - t0) * 1000
    logger.info(
        f"[{req_id}] [STREAM] {type}/{imdb_id} -> {len(streams)} resultados ({elapsed:.0f}ms)"
    )
    return {"streams": [stream.model_dump(exclude_none=True) for stream in streams]}


@router.api_route("/play/{play_id}", methods=["GET", "HEAD"])
async def play_stream(play_id: str, request: Request):
    """
    Resolve a sessao de playback e redireciona para o link HTTP do RD.

    Aceita GET e HEAD:
      Alguns clientes (Stremio, players de video) fazem HEAD antes do GET
      para validar que a URL e alcancavel. Sem suporte a HEAD, o servidor
      retornava 405 e o cliente abortava com ERR_OPENING_MEDIA.

    Cache de URL resolvida:
      Apos resolver via RD, a URL e guardada na mesma play session.
      Como o cache reutiliza a mesma chave `play:{id}`, o TTL da resolved_url
      fica alinhado ao TTL da propria sessao para nao encurta-la.
      Se o cliente fizer HEAD seguido de GET (padrao comum), o segundo
      request reutiliza a URL ja resolvida sem repetir o fluxo RD.

    Fluxo atual:
      - usa a play session criada em /stream sem pre-checagem do RD
      - tenta o fluxo lazy addMagnet -> selectFiles -> info -> unrestrict/link
      - se o torrent ainda nao estiver pronto, faz retries curtos e retorna 503 temporario
      - continua multi-use com TTL curto

    Escolha de status:
      - sucesso continua em 302
      - "nao pronto" usa 503 + Retry-After, e nao 409, porque representa
        indisponibilidade temporaria e nao depende de suporte especifico do
        cliente a um codigo de conflito
      - falha operacional continua em 502
    """
    t0 = time.monotonic()
    method = request.method
    play_key = f"play:{play_id}"
    play_ref = _play_ref(play_id)
    session_data, session_status = await cache.get_with_status(play_key)
    if not session_data:
        if session_status == "expired":
            logger.warning(f"[PLAY] {method} 404 sessao expirada {play_ref} (TTL excedido)")
            raise HTTPException(
                status_code=404,
                detail="Sessao de playback expirada. Gere um novo stream.",
            )
        logger.warning(f"[PLAY] {method} 404 sessao inexistente {play_ref} (play_id invalido ou nunca criado)")
        raise HTTPException(
            status_code=404,
            detail="Sessao de playback inexistente. Gere um novo stream.",
        )

    if not isinstance(session_data, dict):
        logger.error(f"[PLAY] {method} 500 sessao corrompida {play_ref} (tipo={type(session_data).__name__})")
        raise HTTPException(status_code=500, detail="Sessao de playback corrompida")

    req_id = session_data.get("req_id", play_ref)

    # Reutiliza URL ja resolvida se disponivel (evita re-resolve no HEAD+GET)
    cached_url = session_data.get("resolved_url")
    if cached_url:
        elapsed = (time.monotonic() - t0) * 1000
        logger.info(f"[{req_id}] [PLAY] {method} 302 (cached) {play_ref} ({elapsed:.0f}ms)")
        return RedirectResponse(url=cached_url, status_code=302)

    rd_token = session_data.get("rd_token")
    magnet = session_data.get("magnet")
    type_ = session_data.get("type", "movie")
    stremio_id = session_data.get("stremio_id", "")

    missing_fields = [
        field_name for field_name, value in {
            "rd_token": rd_token,
            "magnet": magnet,
        }.items() if not value
    ]
    if missing_fields:
        logger.error(
            f"[{req_id}] [PLAY] {method} 500 sessao corrompida {play_ref} "
            f"(faltando: {', '.join(missing_fields)})"
        )
        raise HTTPException(status_code=500, detail="Sessao de playback corrompida")

    logger.info(f"[{req_id}] [PLAY] {method} Inicio {play_ref}")

    rd = RealDebridService(rd_token, req_id=req_id, play_ref=play_ref)
    try:
        try:
            stream_url = await rd.get_stream_url(
                magnet=magnet,
                type=type_,
                stremio_id=stremio_id,
            )
        except RealDebridPlaybackNotReadyError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            logger.warning(f"[{req_id}] [PLAY] {method} 503 nao pronto {play_ref} ({elapsed:.0f}ms)")
            raise HTTPException(
                status_code=503,
                detail=str(exc),
                headers={
                    "Retry-After": str(PLAY_NOT_READY_RETRY_AFTER_SECONDS),
                    "Cache-Control": "no-store",
                },
            ) from exc
        except RealDebridResolveError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            logger.error(f"[{req_id}] [PLAY] {method} 502 falha operacional {play_ref} ({elapsed:.0f}ms)")
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        # Guarda URL resolvida na session para reuso (HEAD+GET, retries)
        session_data["resolved_url"] = stream_url
        await cache.set(play_key, session_data, ttl=PLAY_RESOLVED_URL_TTL_SECONDS)

        elapsed = (time.monotonic() - t0) * 1000
        logger.info(f"[{req_id}] [PLAY] {method} 302 redirect {play_ref} ({elapsed:.0f}ms)")
        return RedirectResponse(url=stream_url, status_code=302)
    finally:
        await rd.close()
