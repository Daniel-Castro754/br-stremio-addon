import logging
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Palavras-chave para filtrar conteúdo PT-BR
PTBR_KEYWORDS = ["DUBLADO", "DUAL", "NACIONAL", "PORTUGUESE", "PORTUGUES", "PT-BR", "PTBR"]


class TorrentGalaxyScraper(BaseScraper):
    """Scraper para TorrentGalaxy — filtra apenas conteúdo PT-BR"""

    name = "TorrentGalaxy"
    base_url = "https://torrentgalaxy.to"
    stability = "bloqueado_antibot"

    async def search(self, query: str, imdb_id: str, type: str) -> list[TorrentResult]:
        """Busca torrents PT-BR no TorrentGalaxy"""
        resultados: list[TorrentResult] = []

        # lang=31 = Portuguese, nox=2 = apenas torrents verificados
        url = f"{self.base_url}/torrents.php?search={quote(query)}&lang=31&nox=2"
        response = await self._get(url)
        if not response:
            return resultados

        soup = BeautifulSoup(response.text, "html.parser")

        # Tenta extrair linhas da tabela de resultados
        linhas = soup.select("div.tgxtablerow")
        if not linhas:
            linhas = soup.select("tr.tgxtable")

        if not linhas:
            logger.debug(f"[{self.name}] Nenhuma linha de resultado encontrada")
            return resultados

        for linha in linhas[:10]:
            try:
                torrent = self._parsear_linha(linha)
                if torrent:
                    resultados.append(torrent)
            except Exception as e:
                logger.error(f"[{self.name}] Erro ao processar linha: {e}")
                continue

        # Filtra apenas resultados PT-BR
        resultados_ptbr = [r for r in resultados if self._is_ptbr(r.title)]

        if not resultados_ptbr:
            logger.info(f"[{self.name}] {len(resultados)} resultados mas nenhum PT-BR para '{query}'")
            return []

        logger.info(f"[{self.name}] Encontrados {len(resultados_ptbr)} torrents PT-BR para '{query}'")
        return resultados_ptbr

    def _parsear_linha(self, linha: BeautifulSoup) -> TorrentResult | None:
        """Extrai dados de uma linha da tabela do TorrentGalaxy"""
        # Busca magnet link
        magnet_tag = linha.find("a", href=lambda h: h and h.startswith("magnet:"))
        if not magnet_tag:
            return None

        magnet: str = magnet_tag["href"]

        # Extrai info_hash
        match = re.search(r"urn:btih:([a-fA-F0-9]+)", magnet)
        if not match:
            return None
        info_hash = match.group(1).lower()

        # Busca título — link principal do torrent
        link_tag = linha.find("a", href=lambda h: h and "/torrent/" in str(h))
        titulo = link_tag.get_text(strip=True) if link_tag else "Sem título"

        # Busca seeders
        seeders = self._extrair_seeders(linha)

        return TorrentResult(
            title=titulo,
            info_hash=info_hash,
            magnet=magnet,
            quality=self._detectar_qualidade(titulo),
            dubbed=self._detectar_dublado(titulo),
            source=self.name,
            size=self._extrair_tamanho(linha),
            seeders=seeders,
        )

    def _extrair_seeders(self, linha: BeautifulSoup) -> int | None:
        """Tenta extrair número de seeders da linha"""
        # TorrentGalaxy usa span ou font com cor verde para seeders
        for tag in linha.find_all(["span", "font"], attrs={"color": re.compile(r"green|#0")}):
            texto = tag.get_text(strip=True)
            if texto.isdigit():
                return int(texto)
        # Fallback: busca por classe
        seed_tag = linha.select_one("[title*='Seeders'], .tgxtableSeed, span.badge-success")
        if seed_tag:
            texto = seed_tag.get_text(strip=True)
            if texto.isdigit():
                return int(texto)
        return None

    def _extrair_tamanho(self, linha: BeautifulSoup) -> str | None:
        """Tenta extrair tamanho do torrent"""
        texto = linha.get_text()
        match = re.search(r"(\d+[.,]?\d*\s*(?:GB|MB|TB))", texto, re.IGNORECASE)
        return match.group(1).strip() if match else None

    def _is_ptbr(self, titulo: str) -> bool:
        """Verifica se o título contém palavras-chave PT-BR"""
        titulo_upper = titulo.upper()
        return any(kw in titulo_upper for kw in PTBR_KEYWORDS)

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
            for tag in ["DUBLADO", "DUAL", "NACIONAL", "PT-BR"]
        )
