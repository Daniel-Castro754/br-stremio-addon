import logging
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Palavras-chave para filtrar conteúdo PT-BR
PTBR_KEYWORDS = ["DUBLADO", "DUAL", "NACIONAL", "PORTUGUESE", "PORTUGUES", "PT-BR", "PTBR"]


class RuTrackerScraper(BaseScraper):
    """Scraper para RuTracker — busca pública apenas (magnets podem exigir login)"""

    name = "RuTracker"
    base_url = "https://rutracker.org"
    stability = "não_confiável_cloud"

    async def search(
        self,
        query: str,
        imdb_id: str,
        type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentResult]:
        """Busca torrents PT-BR no RuTracker (busca pública)"""
        resultados: list[TorrentResult] = []

        query_encoded = quote(f"{query} dublado")
        url = f"{self.base_url}/forum/tracker.php?nm={query_encoded}"
        response = await self._get(url)
        if not response:
            return resultados

        soup = BeautifulSoup(response.text, "html.parser")

        # Tabela de resultados do RuTracker
        linhas = soup.select("table#tor-tbl tr.tCenter")
        if not linhas:
            linhas = soup.select("table#tor-tbl tr.hl-tr")

        if not linhas:
            logger.debug(f"[{self.name}] Nenhuma linha de resultado para '{query}'")
            return resultados

        # Coleta tópicos com filtro PT-BR
        topicos: list[tuple[str, str, int | None]] = []  # (url, titulo, seeders)
        for linha in linhas:
            try:
                info = self._extrair_info_linha(linha)
                if info and self._is_ptbr(info[1]):
                    topicos.append(info)
            except Exception:
                continue

        if not topicos:
            logger.info(f"[{self.name}] Nenhum resultado PT-BR encontrado")
            return resultados

        # Tenta acessar cada tópico para extrair magnet
        magnets_encontrados = 0
        for url_topico, titulo, seeders in topicos[:5]:
            try:
                torrent = await self._extrair_torrent(url_topico, titulo, seeders)
                if torrent:
                    resultados.append(torrent)
                    magnets_encontrados += 1
            except Exception as e:
                logger.error(f"[{self.name}] Erro ao processar tópico: {e}")
                continue

        logger.info(
            f"[{self.name}] {len(topicos)} tópicos PT-BR encontrados, "
            f"{magnets_encontrados} com magnet acessível"
        )
        return resultados

    def _extrair_info_linha(self, linha: BeautifulSoup) -> tuple[str, str, int | None] | None:
        """Extrai URL, título e seeders de uma linha da tabela"""
        # Título e link do tópico
        td_title = linha.select_one("td.t-title, td.t-title-col")
        if not td_title:
            return None

        link_tag = td_title.find("a")
        if not link_tag:
            return None

        href = link_tag.get("href", "")
        titulo = link_tag.get_text(strip=True)
        if not href or not titulo:
            return None

        # Monta URL completa
        if href.startswith("viewtopic.php"):
            url_topico = f"{self.base_url}/forum/{href}"
        elif href.startswith("/"):
            url_topico = f"{self.base_url}{href}"
        else:
            url_topico = href

        # Seeders
        seeders = None
        td_seeds = linha.select_one("td.seedmed, td.seed")
        if td_seeds:
            texto = td_seeds.get_text(strip=True).replace(",", "")
            if texto.isdigit():
                seeders = int(texto)

        return url_topico, titulo, seeders

    async def _extrair_torrent(
        self, url_topico: str, titulo: str, seeders: int | None
    ) -> TorrentResult | None:
        """Tenta acessar tópico e extrair magnet (pode falhar se exigir login)"""
        response = await self._get(url_topico)
        if not response:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # Busca magnet link (pode estar bloqueado por login)
        magnet_tag = soup.find("a", class_="magnet-link")
        if not magnet_tag:
            magnet_tag = soup.find("a", href=lambda h: h and h.startswith("magnet:"))
        if not magnet_tag:
            # Bloqueado por login — pula silenciosamente
            return None

        magnet: str = magnet_tag["href"]

        # Extrai info_hash
        match = re.search(r"urn:btih:([a-fA-F0-9]+)", magnet)
        if not match:
            return None
        info_hash = match.group(1).lower()

        return TorrentResult(
            title=titulo,
            info_hash=info_hash,
            magnet=magnet,
            quality=self._detectar_qualidade(titulo),
            dubbed=self._detectar_dublado(titulo),
            source=self.name,
            size=self._extrair_tamanho(soup),
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
            for tag in ["DUBLADO", "DUAL", "NACIONAL", "PT-BR", "PORTUGUESE"]
        )

    def _extrair_tamanho(self, soup: BeautifulSoup) -> str | None:
        """Tenta extrair tamanho do torrent"""
        texto = soup.get_text()
        match = re.search(r"(\d+[.,]?\d*\s*(?:GB|MB|TB))", texto, re.IGNORECASE)
        return match.group(1).strip() if match else None
