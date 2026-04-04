import asyncio
import logging
import time
import uuid

from app.models.config import settings
from app.models.torrent import StreamResult, TorrentResult
from app.scrapers.apache_torrent import ApacheTorrentScraper
from app.scrapers.base import BaseScraper, set_req_id
from app.scrapers.brazuca_addon import BrazucaAddonScraper
from app.scrapers.comando_filmes import ComandoFilmesScraper
from app.scrapers.hdr_torrent import HDRTorrentScraper
from app.scrapers.micoleao import MicoLeaoScraper
from app.scrapers.rutracker import RuTrackerScraper
from app.scrapers.torrent_1337x import Torrent1337xScraper
from app.scrapers.torrent_galaxy import TorrentGalaxyScraper
from app.scrapers.yts import YTSScraper
from app.services.cache import cache
from app.services.labeler import build_stream_name, build_stream_title

logger = logging.getLogger(__name__)

# Ordem de prioridade das qualidades (menor = melhor)
QUALITY_ORDER: dict[str, int] = {
    "4K": 0,
    "4K HDR": 0,
    "4K DolbyVision": 0,
    "2160p": 0,
    "1080p": 1,
    "720p": 2,
    "480p": 3,
    "Desconhecida": 4,
}

# ── Scraper Registry ──
SCRAPER_REGISTRY: list[tuple[str, type[BaseScraper]]] = [
    ("ENABLE_APACHE_TORRENT", ApacheTorrentScraper),
    ("ENABLE_COMANDO_FILMES", ComandoFilmesScraper),
    ("ENABLE_HDR_TORRENT",    HDRTorrentScraper),
    ("ENABLE_MICOLEAO",       MicoLeaoScraper),
    ("ENABLE_BRAZUCA",        BrazucaAddonScraper),
    ("ENABLE_YTS",            YTSScraper),
    ("ENABLE_TORRENT_GALAXY", TorrentGalaxyScraper),
    ("ENABLE_1337X",          Torrent1337xScraper),
    ("ENABLE_RUTRACKER",      RuTrackerScraper),
]


def _build_scraper_list() -> list[BaseScraper]:
    """Instancia APENAS os scrapers habilitados por feature flag."""
    active: list[BaseScraper] = []
    for flag_name, scraper_cls in SCRAPER_REGISTRY:
        enabled = getattr(settings, flag_name, False)
        if enabled:
            scraper = scraper_cls()
            active.append(scraper)
            logger.info(f"[REGISTRY] ✅ {scraper.name} ({scraper.stability})")
        else:
            logger.info(f"[REGISTRY] ❌ {scraper_cls.__name__} desativado ({flag_name}=false)")
    return active


def _quality_score(quality: str) -> int:
    return QUALITY_ORDER.get(quality, 4)


def _sort_streams(results: list[tuple[TorrentResult, bool]]) -> list[tuple[TorrentResult, bool]]:
    return sorted(results, key=lambda item: (
        not item[1],
        _quality_score(item[0].quality),
        not item[0].dubbed,
        -(item[0].seeders or 0),
    ))


# Budget mínimo para cada etapa, em segundos.
# Se o budget restante for menor que estes valores, a etapa é ignorada.
MIN_BUDGET_TITLE_FETCH = 2.0   # fetch de título Cinemeta/TMDB
MAX_BUDGET_TITLE_FETCH = 4.0   # teto do fetch de título (não consome mais que isso)
MIN_BUDGET_SCRAPERS = 1.0      # rodada de scrapers
PLAY_SESSION_TTL_SECONDS = 1800  # 30 min — cobre delay, reload e navegacao entre conteudos


class StreamAggregator:
    """Agrega resultados de múltiplos scrapers e integra com Real-Debrid"""

    def __init__(self) -> None:
        self.scrapers: list[BaseScraper] = _build_scraper_list()

    def _budget_remaining(self, t_start: float) -> float:
        elapsed = time.monotonic() - t_start
        return max(0.0, settings.REQUEST_BUDGET_SECONDS - elapsed)

    async def _fetch_title(
        self, imdb_id: str, type: str, req_id: str, budget: float
    ) -> tuple[str, str]:
        """
        Busca título original e PT-BR usando fontes gratuitas.
        Prioridade:
          1. Se TMDB_API_KEY configurada → TMDB (título PT-BR confiável)
          2. Cinemeta (meta.name — pode vir em PT-BR para conteúdo brasileiro)
          3. OMDB (fallback — título original na maioria dos casos)
        Budget-aware: usa min(budget, MAX_BUDGET_TITLE_FETCH) como timeout.
        Se budget < MIN_BUDGET_TITLE_FETCH, retorna fallback imediatamente.
        """
        import httpx

        titulo_original = imdb_id
        titulo_ptbr = imdb_id

        if budget < MIN_BUDGET_TITLE_FETCH:
            logger.warning(
                f"[{req_id}] [_fetch_title] Budget insuficiente ({budget:.1f}s) — usando fallback"
            )
            return titulo_original, titulo_ptbr

        timeout = min(budget, MAX_BUDGET_TITLE_FETCH)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                # Cinemeta — título principal (pode vir em PT-BR)
                for content_type in [type, "movie", "series"]:
                    r = await client.get(
                        f"https://v3-cinemeta.strem.io/meta/{content_type}/{imdb_id}.json"
                    )
                    if r.status_code == 200:
                        data = r.json()
                        nome = data.get("meta", {}).get("name", "")
                        if nome:
                            titulo_original = nome
                            titulo_ptbr = nome
                            break

                # TMDB — título PT-BR (só se API key configurada)
                if settings.TMDB_API_KEY:
                    try:
                        r_tmdb = await client.get(
                            f"https://api.themoviedb.org/3/find/{imdb_id}"
                            f"?external_source=imdb_id&language=pt-BR"
                            f"&api_key={settings.TMDB_API_KEY}"
                        )
                        if r_tmdb.status_code == 200:
                            data_tmdb = r_tmdb.json()
                            resultados = (
                                data_tmdb.get("movie_results")
                                or data_tmdb.get("tv_results")
                                or []
                            )
                            if resultados:
                                nome_ptbr = (
                                    resultados[0].get("title")
                                    or resultados[0].get("name")
                                    or ""
                                )
                                if nome_ptbr:
                                    titulo_ptbr = nome_ptbr
                        elif r_tmdb.status_code == 401:
                            logger.warning(
                                f"[{req_id}] [_fetch_title] TMDB 401 — API key inválida, ignorando"
                            )
                    except Exception as e:
                        logger.warning(f"[{req_id}] [_fetch_title] TMDB falhou: {e}")

                # OMDB — fallback para título (API key pública)
                if titulo_ptbr == imdb_id or titulo_ptbr == titulo_original:
                    try:
                        r_omdb = await client.get(
                            f"https://omdbapi.com/?i={imdb_id}&apikey=trilogy"
                        )
                        if r_omdb.status_code == 200:
                            data_omdb = r_omdb.json()
                            nome_omdb = data_omdb.get("Title", "")
                            if nome_omdb:
                                # OMDB retorna título original na maioria dos casos
                                if titulo_original == imdb_id:
                                    titulo_original = nome_omdb
                                # Se ainda não temos PT-BR diferente, usa o do OMDB
                                if titulo_ptbr == imdb_id:
                                    titulo_ptbr = nome_omdb
                    except Exception as e:
                        logger.warning(f"[{req_id}] [_fetch_title] OMDB falhou: {e}")

        except Exception as e:
            logger.warning(f"[{req_id}] [_fetch_title] Erro ({timeout:.1f}s timeout): {e}")
            titulo_ptbr = titulo_original

        # Garante que nunca retorna imdb_id como título se temos algo melhor
        if titulo_ptbr == imdb_id and titulo_original != imdb_id:
            titulo_ptbr = titulo_original

        logger.info(
            f"[{req_id}] Títulos: original='{titulo_original}' ptbr='{titulo_ptbr}'"
        )
        return titulo_original, titulo_ptbr

    async def get_streams(
        self,
        imdb_id: str,
        type: str,
        req_id: str,
        stremio_id: str | None = None,
        title: str | None = None,
        rd_token: str | None = None,
        request_base_url: str | None = None,
    ) -> list[StreamResult]:
        """
        Busca streams em todos os scrapers e formata para o Stremio.

        Budget total real — cada etapa consulta o budget restante:
          1. _fetch_title: limitado a MAX_BUDGET_TITLE_FETCH (4s)
          2. scrapers ptbr: budget restante
          3. scrapers original: só se budget > MIN_BUDGET_SCRAPERS

        Real-Debrid:
          - não há mais pré-checagem via instantAvailability
          - se houver token RD e magnet válido, o addon cria play session
            e delega a resolução real para /play no clique
        """
        t_start = time.monotonic()
        logger.info(
            f"[{req_id}] Início: {type}/{imdb_id} "
            f"(budget={settings.REQUEST_BUDGET_SECONDS}s)"
        )

        cache_key = f"streams:{imdb_id}:{type}"
        torrent_results = await self._get_cached_torrents(cache_key, req_id)

        if torrent_results is None:
            # Cache miss — busca títulos e roda scrapers dentro do budget
            remaining = self._budget_remaining(t_start)
            titulo_original, titulo_ptbr = await self._fetch_title(
                imdb_id, type, req_id, remaining
            )

            # Primeira rodada (PT-BR)
            remaining = self._budget_remaining(t_start)
            if remaining > MIN_BUDGET_SCRAPERS:
                torrent_results = await self._run_scrapers(
                    titulo_ptbr, imdb_id, type, req_id, "ptbr", remaining
                )
            else:
                logger.warning(f"[{req_id}] Budget esgotado antes dos scrapers ({remaining:.1f}s)")
                torrent_results = []

            # Segunda rodada (título original) — só se necessário e viável
            remaining = self._budget_remaining(t_start)
            if len(torrent_results) < 3 and titulo_original != titulo_ptbr and remaining > MIN_BUDGET_SCRAPERS:
                logger.info(
                    f"[{req_id}] Poucos resultados ({len(torrent_results)}), "
                    f"segunda rodada (budget={remaining:.1f}s)..."
                )
                extras = await self._run_scrapers(
                    titulo_original, imdb_id, type, req_id, "original", remaining
                )
                torrent_results = self._deduplicate(torrent_results + extras)
            elif len(torrent_results) < 3 and titulo_original != titulo_ptbr:
                logger.warning(
                    f"[{req_id}] Budget insuficiente para segunda rodada ({remaining:.1f}s)"
                )

            # Salva no cache
            await cache.set(cache_key, [t.model_dump() for t in torrent_results])
            logger.info(f"[{req_id}] [CACHE SET] {cache_key} → {len(torrent_results)} torrents")

        # Monta pares e ordena
        pares = [(t, bool(rd_token and t.magnet)) for t in torrent_results]
        pares_ordenados = _sort_streams(pares)

        # Formata StreamResults + cria play sessions
        streams: list[StreamResult] = []
        play_sessions_count = 0
        for torrent, has_play_url in pares_ordenados:
            stream_url = None
            if rd_token and torrent.magnet:
                play_id = str(uuid.uuid4())
                # Risco operacional conhecido:
                #   O token RD ainda precisa ser persistido na play session porque
                #   /play e /stream sao requests separados e o contrato atual do
                #   addon nao tem vault dedicado nem cookie/header de servidor.
                #   Nesta fase o risco fica documentado e isolado neste payload.
                #   O token nunca entra no stream cache, apenas em play sessions.
                session_data = {
                    "rd_token": rd_token,
                    "magnet": torrent.magnet,
                    "info_hash": torrent.info_hash,
                    "imdb_id": imdb_id,
                    "stremio_id": stremio_id or imdb_id,
                    "type": type,
                    "req_id": req_id,
                    "created_at": time.time(),
                    "play_session_ttl": PLAY_SESSION_TTL_SECONDS,
                }
                # Play session TTL = 900s (15 min).
                # Política: MULTI-USE com TTL curto.
                #
                # Justificativa:
                #   Players de vídeo (mpv, ExoPlayer no Android) fazem retry
                #   automático em caso de rede instável, seek, ou rebuffering.
                #   O Stremio pode chamar a mesma URL de play 2–3x em sequência
                #   rápida. Com single-use, a segunda chamada falha com 404,
                #   quebrando a experiência silenciosamente.
                #
                #   O RD unrestrict_link é idempotente — resolver o mesmo
                #   magnet/arquivo múltiplas vezes não gera custo adicional
                #   real no Real-Debrid.
                #
                #   A sessão expira por TTL, o que limita o reuso
                #   a um intervalo razoável sem precisar de delete explícito.
                #   O TTL de 5 min falhava quando o usuario demorava para clicar,
                #   recarregava o cliente ou navegava entre conteudos. 15 min
                #   ainda falhava em cenarios de browse longo ou reload tardio.
                #   30 min cobre melhor o uso real sem exagero.
                #
                # TTL efetivo desta play session: 1800s / 30 min.
                await cache.set(
                    f"play:{play_id}",
                    session_data,
                    ttl=PLAY_SESSION_TTL_SECONDS,
                )
                # Usa a origem real da request para montar a URL de playback.
                # Fallback para settings.BASE_URL se não fornecido (ex: testes).
                base = request_base_url or settings.BASE_URL
                stream_url = f"{base}/play/{play_id}"
                play_sessions_count += 1
                has_play_url = True

            stream = self._formatar_stream(
                torrent=torrent,
                has_play_url=has_play_url,
                tem_rd=rd_token is not None,
                stream_url=stream_url
            )
            streams.append(stream)

        elapsed_total = (time.monotonic() - t_start) * 1000
        logger.info(
            f"[{req_id}] Concluído: {len(streams)} streams, "
            f"{play_sessions_count} play sessions, {elapsed_total:.0f}ms"
        )

        return streams

    async def _run_scrapers(
        self, query: str, imdb_id: str, type: str,
        req_id: str, label: str, budget: float
    ) -> list[TorrentResult]:
        """Executa scrapers em paralelo com budget real e req_id propagado."""
        if not self.scrapers:
            logger.warning(f"[{req_id}] Nenhum scraper ativo!")
            return []

        effective_timeout = min(settings.SCRAPER_TIMEOUT_SECONDS + 2.0, budget)

        async def _timed_search(scraper: BaseScraper) -> tuple[str, list[TorrentResult] | Exception, float]:
            # Propaga req_id via contextvars — seguro sob concorrência.
            # Cada task criada por asyncio.gather herda o contexto do pai,
            # e set_req_id() define o valor para esta task específica.
            set_req_id(req_id)
            t0 = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    scraper.search(query, imdb_id, type),
                    timeout=effective_timeout,
                )
                elapsed = (time.monotonic() - t0) * 1000
                return scraper.name, result, elapsed
            except asyncio.TimeoutError:
                elapsed = (time.monotonic() - t0) * 1000
                return scraper.name, TimeoutError(f"Timeout ({elapsed:.0f}ms, budget={budget:.1f}s)"), elapsed
            except Exception as e:
                elapsed = (time.monotonic() - t0) * 1000
                return scraper.name, e, elapsed

        tasks = [_timed_search(s) for s in self.scrapers]
        resultados = await asyncio.gather(*tasks)

        todos: list[TorrentResult] = []
        for scraper_name, resultado, elapsed_ms in resultados:
            if isinstance(resultado, Exception):
                logger.warning(
                    f"[{req_id}] [{label}] [{scraper_name}] FALHOU: {resultado} ({elapsed_ms:.0f}ms)"
                )
                continue
            count = len(resultado)
            logger.info(
                f"[{req_id}] [{label}] [{scraper_name}] {count} resultados ({elapsed_ms:.0f}ms)"
            )
            todos.extend(resultado)

        return self._deduplicate(todos)

    def _deduplicate(self, results: list[TorrentResult]) -> list[TorrentResult]:
        vistos: set[str] = set()
        unicos: list[TorrentResult] = []
        for torrent in results:
            if not torrent.info_hash:
                continue
            if torrent.info_hash not in vistos:
                vistos.add(torrent.info_hash)
                unicos.append(torrent)
        return unicos

    async def _get_cached_torrents(self, cache_key: str, req_id: str) -> list[TorrentResult] | None:
        dados = await cache.get(cache_key)
        if dados is None:
            logger.info(f"[{req_id}] [CACHE MISS] {cache_key}")
            return None

        logger.info(f"[{req_id}] [CACHE HIT] {cache_key}")
        try:
            return [TorrentResult(**item) for item in dados]
        except Exception as e:
            logger.error(f"[{req_id}] [CACHE] Erro ao deserializar: {e}")
            return None

    def _formatar_stream(
        self,
        torrent: TorrentResult,
        has_play_url: bool,
        tem_rd: bool,
        stream_url: str | None = None,
    ) -> StreamResult:
        """
        Formata TorrentResult para StreamResult.

        Exclusão mútua explícita:
          stream_url presente -> url preenchida, infoHash OMITIDO
          stream_url ausente  -> infoHash preenchido, url OMITIDO
          Nunca os dois ao mesmo tempo.
        """
        if stream_url:
            return StreamResult(
                name=build_stream_name(torrent, has_play_url=True),
                title=build_stream_title(torrent, has_play_url=True),
                url=stream_url,
                behaviorHints={"bingeGroup": f"rd-{torrent.info_hash[:8]}"},
            )

        behavior: dict = {}
        if tem_rd:
            behavior = {"notWebReady": True}

        return StreamResult(
            name=build_stream_name(torrent, has_play_url),
            title=build_stream_title(torrent, has_play_url),
            infoHash=torrent.info_hash,
            behaviorHints=behavior,
        )

    async def close(self) -> None:
        for scraper in self.scrapers:
            await scraper.close()
