import logging
import re

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class BrazucaAddonScraper(BaseScraper):
    """Scraper que consome o addon Brazuca Torrents via API Stremio"""

    name = "Brazuca Torrents"
    base_url = "https://94c8cb9f702d-brazuca-torrents.baby-beamup.club"

    async def search(self, query: str, imdb_id: str, type: str) -> list[TorrentResult]:
        """Busca streams no addon Brazuca Torrents via API JSON"""
        resultados: list[TorrentResult] = []

        # Consome a API do addon Stremio diretamente (não faz web scraping)
        url = f"{self.base_url}/stream/{type}/{imdb_id}.json"
        response = await self._get(url)
        if not response:
            return resultados

        try:
            data = response.json()
        except Exception as e:
            logger.error(f"[{self.name}] Erro ao parsear JSON de {url}: {e}")
            return resultados

        streams = data.get("streams", [])

        for stream in streams:
            try:
                torrent = self._parsear_stream(stream)
                if torrent:
                    resultados.append(torrent)
            except Exception as e:
                logger.error(f"[{self.name}] Erro ao processar stream: {e}")
                continue

        logger.info(f"[{self.name}] Encontrados {len(resultados)} torrents para '{imdb_id}'")
        return resultados

    def _parsear_stream(self, stream: dict) -> TorrentResult | None:
        """Converte um objeto stream do Stremio em TorrentResult"""
        info_hash = stream.get("infoHash", "")
        url_direta = stream.get("url", "")

        # Precisa ter pelo menos infoHash ou url
        if not info_hash and not url_direta:
            return None

        # Monta magnet a partir do infoHash
        if info_hash:
            info_hash = info_hash.lower().strip()
            magnet = f"magnet:?xt=urn:btih:{info_hash}"
        else:
            # Sem hash — usa url direta, magnet vazio
            magnet = ""
            # Gera um hash fictício baseado na URL para deduplicação
            info_hash = ""

        # Extrai título do stream
        titulo = stream.get("title", "") or stream.get("name", "") or "Sem título"

        # Detecta qualidade e dublado pelo título
        quality = self._detectar_qualidade(titulo)
        dubbed = self._detectar_dublado(titulo)

        # Tenta extrair tamanho do título (ex: "1.4 GB" no texto)
        size = self._extrair_tamanho_titulo(titulo)

        return TorrentResult(
            title=titulo,
            info_hash=info_hash,
            magnet=magnet,
            quality=quality,
            dubbed=dubbed,
            source=self.name,
            size=size,
            seeders=None,
        )

    def _detectar_qualidade(self, titulo: str) -> str:
        """Detecta a qualidade pelo título"""
        titulo_upper = titulo.upper()
        if "4K" in titulo_upper or "2160P" in titulo_upper:
            return "4K"
        if "1080P" in titulo_upper:
            return "1080p"
        if "720P" in titulo_upper:
            return "720p"
        if "480P" in titulo_upper:
            return "480p"
        return "Desconhecida"

    def _detectar_dublado(self, titulo: str) -> bool:
        """Detecta se o torrent é dublado PT-BR"""
        titulo_upper = titulo.upper()
        return any(
            tag in titulo_upper
            for tag in ["DUBLADO", "DUAL ÁUDIO", "DUAL AUDIO", "DUAL", "NACIONAL", "PT-BR"]
        )

    def _extrair_tamanho_titulo(self, titulo: str) -> str | None:
        """Tenta extrair o tamanho do arquivo a partir do texto do título"""
        match = re.search(r"(\d+[.,]?\d*\s*(?:GB|MB|TB))", titulo, re.IGNORECASE)
        return match.group(1).strip() if match else None
