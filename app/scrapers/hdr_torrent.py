import asyncio
import logging
import re
from urllib.parse import quote, unquote

from bs4 import BeautifulSoup

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper
from app.scrapers.relevance import build_series_queries, is_relevant_release, matches_episode

logger = logging.getLogger(__name__)


class HDRTorrentScraper(BaseScraper):
    """Scraper para o site HDR Torrent — foco em conteúdo 4K/HDR"""

    name = "HDR Torrent"
    base_url = "https://www.hdrtorrent.net"
    _fallback_urls = [
        "https://www.hdrtorrent.net",
        "https://hdrtorrent.com",
    ]

    # Mesmo padrão de profundidade/concorrência do Apache Torrent e Comando
    # Filmes — processa mais candidatos em paralelo em vez de poucos em
    # sequência.
    MAX_DETAIL_PAGES = 10
    MAX_CONCURRENT_DETAIL_FETCHES = 5

    async def search(
        self,
        query: str,
        imdb_id: str,
        type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentResult]:
        """Busca torrents no HDR Torrent por título.

        Para séries, tenta primeiro a query com S01E05 (episódio avulso) e,
        se não achar nada, cai para a query só com o título (pacote de
        temporada completa) — mesmo padrão do Apache Torrent/Comando Filmes.
        """
        resultados: list[TorrentResult] = []
        vistos: set[str] = set()

        for tentativa in build_series_queries(query, season, episode):
            encontrados = await self._buscar_query(tentativa, season, episode)
            for torrent in encontrados:
                if torrent.info_hash not in vistos:
                    vistos.add(torrent.info_hash)
                    resultados.append(torrent)
            if resultados:
                break

        logger.info(f"[{self.name}] Encontrados {len(resultados)} torrents para '{query}'")
        return resultados

    async def _buscar_query(
        self, query: str, season: int | None, episode: int | None
    ) -> list[TorrentResult]:
        """Executa uma única rodada de busca+extração para uma query."""
        resultados: list[TorrentResult] = []

        # Antes faltava quote() aqui — espaços e acentos na query iam sem
        # codificação nenhuma pra URL.
        urls_busca = [f"{u}/?s={quote(query)}" for u in self._fallback_urls]
        response = await self._get_with_fallback(urls_busca)
        if not response:
            return resultados

        soup = BeautifulSoup(response.text, "html.parser")

        # Extrai links dos posts (padrão WordPress)
        links_posts: list[str] = []
        for tag in soup.select("article a, .post a, h2 a, h3 a"):
            href = tag.get("href", "")
            if href and href.startswith("http") and href not in links_posts:
                links_posts.append(href)

        if not links_posts:
            return resultados

        # Processa em paralelo (limitado por semáforo) em vez de sequencial.
        candidatos = links_posts[: self.MAX_DETAIL_PAGES]
        semaforo = asyncio.Semaphore(self.MAX_CONCURRENT_DETAIL_FETCHES)

        async def _extrair_com_limite(link: str) -> TorrentResult | None:
            async with semaforo:
                try:
                    return await self._extrair_torrent(link)
                except Exception as e:
                    logger.error(f"[{self.name}] Erro ao processar {link}: {e}")
                    return None

        torrents_brutos = await asyncio.gather(*[_extrair_com_limite(link) for link in candidatos])

        # Antes esse scraper não filtrava relevância nenhuma — qualquer link
        # que batesse nos seletores genéricos (inclusive posts recentes na
        # barra lateral, sem relação com a busca) virava resultado.
        rejeitados = 0
        for link_post, torrent in zip(candidatos, torrents_brutos, strict=True):
            if not torrent:
                continue
            if not is_relevant_release(query, torrent.title, link_post):
                rejeitados += 1
                logger.warning(
                    f"[{self.name}] Descartado por baixa relevância: "
                    f"query='{query}' resultado='{torrent.title}'"
                )
                continue
            if not matches_episode(torrent.title, season, episode):
                rejeitados += 1
                logger.warning(
                    f"[{self.name}] Descartado por temporada/episódio diferente: "
                    f"pedido=S{season}E{episode} resultado='{torrent.title}'"
                )
                continue
            resultados.append(torrent)

        if rejeitados:
            logger.debug(f"[{self.name}] '{query}': {rejeitados} descartados")
        return resultados

    async def _extrair_torrent(self, url_post: str) -> TorrentResult | None:
        """Acessa a página do torrent e extrai o magnet link"""
        response = await self._get(url_post)
        if not response:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # Busca magnet link na página
        magnet_tag = soup.find("a", href=re.compile(r"^magnet:", re.IGNORECASE))
        if not magnet_tag:
            return None

        magnet: str = magnet_tag["href"]

        # Extrai info_hash do magnet
        match = re.search(r"urn:btih:([a-fA-F0-9]+)", magnet)
        if not match:
            return None
        info_hash = match.group(1).lower()

        # Título da página
        titulo_tag = soup.find("h1") or soup.find("title")
        titulo = titulo_tag.get_text(strip=True) if titulo_tag else unquote(url_post.split("/")[-2])

        # Detecta qualidade (foco especial em HDR/DolbyVision)
        quality = self._detectar_qualidade(titulo)
        dubbed = self._detectar_dublado(titulo)
        size = self._extrair_tamanho(soup)

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
        """Detecta a qualidade pelo título — foco especial em 4K/HDR"""
        titulo_upper = titulo.upper()
        # Prioriza detecção de HDR e Dolby Vision
        if "DOLBY VISION" in titulo_upper or "DOLBYVISION" in titulo_upper or "DV" in titulo_upper:
            return "4K DolbyVision"
        if "HDR" in titulo_upper and ("4K" in titulo_upper or "2160P" in titulo_upper):
            return "4K HDR"
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
            for tag in ["DUBLADO", "DUAL ÁUDIO", "DUAL AUDIO", "DUAL", "NACIONAL"]
        )

    def _extrair_tamanho(self, soup: BeautifulSoup) -> str | None:
        """Tenta extrair o tamanho do arquivo da página"""
        texto = soup.get_text()
        match = re.search(r"(\d+[.,]?\d*\s*(?:GB|MB|TB))", texto, re.IGNORECASE)
        return match.group(1).strip() if match else None
