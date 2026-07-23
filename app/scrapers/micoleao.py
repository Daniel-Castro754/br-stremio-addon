import logging
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class MicoLeaoScraper(BaseScraper):
    """Scraper para o site MicoLeão Dublado (WordPress)"""

    name = "MicoLeão Dublado"
    base_url = "https://www.micoleaodublado.net"
    _fallback_urls = [
        "https://www.micoleaodublado.net",
        "https://micoleaodublado.com.br",
    ]

    async def search(
        self,
        query: str,
        imdb_id: str,
        type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentResult]:
        """Busca torrents no MicoLeão Dublado por título"""
        resultados: list[TorrentResult] = []

        # Busca com fallback de DNS
        urls_busca = [f"{u}/?s={query}" for u in self._fallback_urls]
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

        # Limita a 10 resultados
        for link_post in links_posts[:10]:
            try:
                torrent = await self._extrair_torrent(link_post)
                if torrent:
                    resultados.append(torrent)
            except Exception as e:
                logger.error(f"[{self.name}] Erro ao processar {link_post}: {e}")
                continue

        logger.info(f"[{self.name}] Encontrados {len(resultados)} torrents para '{query}'")
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

        # Detecta qualidade e dublado
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
            for tag in ["DUBLADO", "DUAL ÁUDIO", "DUAL AUDIO", "DUAL", "NACIONAL"]
        )

    def _extrair_tamanho(self, soup: BeautifulSoup) -> str | None:
        """Tenta extrair o tamanho do arquivo da página"""
        texto = soup.get_text()
        match = re.search(r"(\d+[.,]?\d*\s*(?:GB|MB|TB))", texto, re.IGNORECASE)
        return match.group(1).strip() if match else None
