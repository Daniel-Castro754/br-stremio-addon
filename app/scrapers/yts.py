import logging
from urllib.parse import quote

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Trackers públicos usados somente para montar o magnet a partir do hash.
YTS_TRACKERS = [
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:80",
    "udp://tracker.coppersurfer.tk:6969",
    "udp://glotorrents.pw:6969/announce",
    "udp://exodus.desync.com:6969",
    "udp://p4p.arenabg.com:1337",
]


class YTSScraper(BaseScraper):
    """Busca filmes pela API JSON usando o IMDb ID para evitar homônimos."""

    name = "YTS"
    base_url = "https://yts.bz"
    # Busca só por imdb_id — o texto de `query` nunca é usado.
    USES_TEXT_QUERY = False
    _fallback_urls = [
        "https://yts.bz",
        "https://yts.lt",
        "https://yts.am",
        "https://yts.ag",
        "https://yts.gg",
    ]

    async def search(
        self,
        query: str,
        imdb_id: str,
        type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentResult]:
        resultados: list[TorrentResult] = []
        if type != "movie":
            return resultados

        urls = [
            f"{base}/api/v2/list_movies.json?query_term={quote(imdb_id)}&limit=5"
            for base in self._fallback_urls
        ]
        response = await self._get_with_fallback(urls)
        if not response:
            return resultados

        try:
            data = response.json()
        except Exception as exc:
            self.last_error = f"JSON inválido: {exc}"
            logger.error(f"[{self.name}] Erro ao parsear JSON: {exc}")
            return resultados

        filmes = data.get("data", {}).get("movies") or []
        for filme in filmes:
            returned_imdb = str(filme.get("imdb_code") or "").lower()
            if returned_imdb and returned_imdb != imdb_id.lower():
                logger.warning(
                    f"[{self.name}] IMDb divergente descartado: "
                    f"esperado={imdb_id} recebido={returned_imdb}"
                )
                continue

            titulo_filme = filme.get("title_long") or filme.get("title", "")
            for torrent_info in filme.get("torrents") or []:
                try:
                    result = self._parsear_torrent(titulo_filme, torrent_info)
                    if result:
                        resultados.append(result)
                except Exception as exc:
                    logger.error(f"[{self.name}] Erro ao processar torrent: {exc}")

        logger.info(f"[{self.name}] Encontrados {len(resultados)} torrents para '{imdb_id}'")
        return resultados

    def _parsear_torrent(self, titulo: str, torrent_info: dict) -> TorrentResult | None:
        hash_val = str(torrent_info.get("hash") or "").strip()
        if not hash_val:
            return None

        info_hash = hash_val.lower()
        trackers_str = "&".join(f"tr={quote(tracker, safe=':/')}" for tracker in YTS_TRACKERS)
        magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={quote(titulo)}&{trackers_str}"

        return TorrentResult(
            title=f"{titulo} [{torrent_info.get('quality', 'Desconhecida')}]",
            info_hash=info_hash,
            magnet=magnet,
            quality=torrent_info.get("quality", "Desconhecida"),
            dubbed=self._detectar_dublado(titulo),
            source=self.name,
            size=torrent_info.get("size"),
            seeders=torrent_info.get("seeds"),
        )

    def _detectar_dublado(self, titulo: str) -> bool:
        titulo_upper = titulo.upper()
        return any(
            tag in titulo_upper
            for tag in ["DUBLADO", "DUAL ÁUDIO", "DUAL AUDIO", "DUAL", "NACIONAL", "PORTUGUES", "PORTUGUESE", "PT-BR"]
        )
