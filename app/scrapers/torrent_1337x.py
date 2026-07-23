import logging
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Palavras-chave para filtrar conteúdo PT-BR
PTBR_KEYWORDS = ["DUBLADO", "DUAL", "NACIONAL", "PORTUGUESE", "PORTUGUES", "PT-BR", "PTBR"]


class Torrent1337xScraper(BaseScraper):
    """Scraper para 1337x — busca com filtro 'dublado'"""

    name = "1337x"
    base_url = "https://1337x.to"
    stability = "bloqueado_antibot"

    async def search(
        self,
        query: str,
        imdb_id: str,
        type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentResult]:
        """Busca torrents PT-BR no 1337x (adiciona 'dublado' na query)"""
        resultados: list[TorrentResult] = []

        # Busca já filtrando por "dublado" para reduzir ruído
        query_encoded = quote(f"{query} dublado")
        url = f"{self.base_url}/search/{query_encoded}/1/"
        response = await self._get(url)
        if not response:
            return resultados

        soup = BeautifulSoup(response.text, "html.parser")

        # Extrai linhas da tabela de resultados
        linhas = soup.select("tbody tr")
        if not linhas:
            linhas = soup.select("table.table-list tr")

        if not linhas:
            logger.debug(f"[{self.name}] Nenhuma linha de resultado para '{query}'")
            return resultados

        # Coleta links das páginas de torrents individuais
        links_torrents: list[tuple[str, int | None]] = []
        for linha in linhas:
            try:
                info = self._extrair_info_linha(linha)
                if info:
                    links_torrents.append(info)
            except Exception:
                continue

        # Limita a 5 páginas individuais
        for href, seeders in links_torrents[:5]:
            try:
                torrent = await self._extrair_torrent(href, seeders)
                if torrent and self._is_ptbr(torrent.title):
                    resultados.append(torrent)
            except Exception as e:
                logger.error(f"[{self.name}] Erro ao processar {href}: {e}")
                continue

        logger.info(f"[{self.name}] Encontrados {len(resultados)} torrents PT-BR para '{query}'")
        return resultados

    def _extrair_info_linha(self, linha: BeautifulSoup) -> tuple[str, int | None] | None:
        """Extrai link do torrent e seeders de uma linha da tabela"""
        # Link da página do torrent
        td_name = linha.select_one("td.name, td.coll-1")
        if not td_name:
            return None

        link_tag = td_name.find("a", href=lambda h: h and "/torrent/" in str(h))
        if not link_tag:
            return None

        href = link_tag.get("href", "")
        if not href:
            return None

        # Seeders
        seeders = None
        td_seeds = linha.select_one("td.seeds, td.coll-2")
        if td_seeds:
            texto = td_seeds.get_text(strip=True)
            if texto.isdigit():
                seeders = int(texto)

        return href, seeders

    async def _extrair_torrent(self, href: str, seeders: int | None) -> TorrentResult | None:
        """Acessa página individual do torrent e extrai magnet"""
        url_completa = f"{self.base_url}{href}" if href.startswith("/") else href
        response = await self._get(url_completa)
        if not response:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # Busca magnet link
        magnet_tag = soup.find("a", href=lambda h: h and h.startswith("magnet:"))
        if not magnet_tag:
            return None

        magnet: str = magnet_tag["href"]

        # Extrai info_hash
        match = re.search(r"urn:btih:([a-fA-F0-9]+)", magnet)
        if not match:
            return None
        info_hash = match.group(1).lower()

        # Título
        titulo_tag = soup.select_one("h1, .box-info-heading h1")
        titulo = titulo_tag.get_text(strip=True) if titulo_tag else "Sem título"

        # Tamanho
        size = self._extrair_tamanho(soup)

        return TorrentResult(
            title=titulo,
            info_hash=info_hash,
            magnet=magnet,
            quality=self._detectar_qualidade(titulo),
            dubbed=self._detectar_dublado(titulo),
            source=self.name,
            size=size,
            seeders=seeders,
        )

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

    def _extrair_tamanho(self, soup: BeautifulSoup) -> str | None:
        """Tenta extrair tamanho do torrent da página"""
        texto = soup.get_text()
        match = re.search(r"(\d+[.,]?\d*\s*(?:GB|MB|TB))", texto, re.IGNORECASE)
        return match.group(1).strip() if match else None
