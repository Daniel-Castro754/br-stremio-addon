import logging
from urllib.parse import quote

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Trackers padrão do YTS para montar magnets
YTS_TRACKERS = [
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:80",
    "udp://tracker.coppersurfer.tk:6969",
    "udp://glotorrents.pw:6969/announce",
    "udp://exodus.desync.com:6969",
    "udp://p4p.arenabg.com:1337",
]


class YTSScraper(BaseScraper):
    """Scraper para YTS via API JSON oficial — fonte de alta qualidade"""

    name = "YTS"
    base_url = "https://yts.mx"

    async def search(self, query: str, imdb_id: str, type: str) -> list[TorrentResult]:
        """Busca filmes no YTS via API JSON"""
        resultados: list[TorrentResult] = []

        # API JSON do YTS — sem scraping HTML
        url = f"{self.base_url}/api/v2/list_movies.json?query_term={quote(query)}&limit=5"
        response = await self._get(url)
        if not response:
            return resultados

        try:
            data = response.json()
        except Exception as e:
            logger.error(f"[{self.name}] Erro ao parsear JSON: {e}")
            return resultados

        filmes = data.get("data", {}).get("movies") or []

        for filme in filmes:
            titulo_filme = filme.get("title_long") or filme.get("title", "")
            torrents = filme.get("torrents") or []

            for torrent_info in torrents:
                try:
                    result = self._parsear_torrent(titulo_filme, torrent_info)
                    if result:
                        resultados.append(result)
                except Exception as e:
                    logger.error(f"[{self.name}] Erro ao processar torrent: {e}")
                    continue

        logger.info(f"[{self.name}] Encontrados {len(resultados)} torrents para '{query}'")
        return resultados

    def _parsear_torrent(self, titulo: str, torrent_info: dict) -> TorrentResult | None:
        """Converte dados do torrent YTS em TorrentResult"""
        hash_val = torrent_info.get("hash", "")
        if not hash_val:
            return None

        info_hash = hash_val.lower()

        # Monta magnet com trackers padrão YTS
        trackers_str = "&".join(f"tr={t}" for t in YTS_TRACKERS)
        magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={quote(titulo)}&{trackers_str}"

        quality = torrent_info.get("quality", "Desconhecida")
        size = torrent_info.get("size", None)
        seeders = torrent_info.get("seeds")

        # YTS é fonte inglesa — detecta dublado pelo título (raro)
        dubbed = self._detectar_dublado(titulo)

        return TorrentResult(
            title=f"{titulo} [{quality}]",
            info_hash=info_hash,
            magnet=magnet,
            quality=quality,
            dubbed=dubbed,
            source=self.name,
            size=size,
            seeders=seeders,
        )

    def _detectar_dublado(self, titulo: str) -> bool:
        """Detecta se o torrent é dublado PT-BR (raro no YTS)"""
        titulo_upper = titulo.upper()
        return any(
            tag in titulo_upper
            for tag in ["DUBLADO", "DUAL", "PORTUGUESE", "NACIONAL", "PT-BR"]
        )
