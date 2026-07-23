import logging
import re
from urllib.parse import quote, unquote, urlparse

from bs4 import BeautifulSoup

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper
from app.scrapers.relevance import is_relevant_release

logger = logging.getLogger(__name__)


class ComandoFilmesScraper(BaseScraper):
    """Scraper para o site Comando Filmes (WordPress)"""

    name = "Comando Filmes"
    base_url = "https://baixafilmestorrent.org"
    _fallback_urls = [
        "https://baixafilmestorrent.org",
        "https://www.baixafilmestorrent.net",
        "https://baixafilmestorrenthd.com",
        "https://baixafilmestorrenthd.org",
    ]

    async def search(self, query: str, imdb_id: str, type: str) -> list[TorrentResult]:
        """Busca torrents no Comando Filmes por título"""
        resultados: list[TorrentResult] = []

        urls_busca = [f"{base}/?s={quote(query)}" for base in self._fallback_urls]
        response = await self._get_with_fallback(urls_busca)
        if not response:
            return resultados

        soup = BeautifulSoup(response.text, "html.parser")
        dominio = urlparse(str(response.url)).netloc

        # Seletores em fallback — usa o primeiro que retornar elementos
        links_posts = self._extrair_links_posts(soup, dominio)

        if not links_posts:
            logger.debug(
                f"[{self.name}] Nenhum post em {response.url} — snippet: "
                f"{str(soup.body)[:500] if soup.body else 'body vazio'}"
            )
            return resultados

        # Limita a 5 páginas por busca e descarta falsos positivos.
        rejeitados = 0
        for link_post in links_posts[:5]:
            try:
                torrent = await self._extrair_torrent(link_post)
                if not torrent:
                    continue
                if not is_relevant_release(query, torrent.title, link_post):
                    rejeitados += 1
                    logger.warning(
                        f"[{self.name}] Descartado por baixa relevância: "
                        f"query='{query}' resultado='{torrent.title}'"
                    )
                    continue
                resultados.append(torrent)
            except Exception as e:
                logger.error(f"[{self.name}] Erro ao processar {link_post}: {e}")
                continue

        logger.info(
            f"[{self.name}] Encontrados {len(resultados)} torrents para '{query}' "
            f"({rejeitados} descartados)"
        )
        return resultados

    def _extrair_links_posts(self, soup: BeautifulSoup, dominio: str) -> list[str]:
        """Tenta múltiplos seletores WordPress em fallback"""
        seletores_article = [
            "article.post",
            "article",
            ".post",
        ]
        seletores_link = [
            "h2.entry-title a",
            ".entry-title a",
            "h2 a",
        ]

        # Tenta seletores de article primeiro
        for seletor in seletores_article:
            elementos = soup.select(seletor)
            if elementos:
                logger.debug(f"[{self.name}] Seletor '{seletor}' retornou {len(elementos)} elementos")
                links: list[str] = []
                for el in elementos:
                    link = self._extrair_link_de_article(el, dominio)
                    if link and link not in links:
                        links.append(link)
                if links:
                    return links

        # Tenta seletores de link direto
        for seletor in seletores_link:
            elementos = soup.select(seletor)
            if elementos:
                logger.debug(f"[{self.name}] Seletor '{seletor}' retornou {len(elementos)} elementos")
                links = []
                for el in elementos:
                    href = el.get("href", "")
                    if href and dominio in href and href not in links:
                        links.append(href)
                if links:
                    return links

        return []

    def _extrair_link_de_article(self, article: BeautifulSoup, dominio: str) -> str | None:
        """Extrai o link principal de um elemento <article>"""
        for sel in ["h2 a", "h3 a", ".entry-title a"]:
            tag = article.select_one(sel)
            if tag:
                href = tag.get("href", "")
                if href and dominio in href:
                    return href

        # Fallback: primeiro <a> com href contendo o domínio
        for tag in article.find_all("a", href=True):
            href = tag["href"]
            if dominio in href:
                return href
        return None

    async def _extrair_torrent(self, url_post: str) -> TorrentResult | None:
        """Acessa a página do torrent e extrai o magnet link"""
        response = await self._get(url_post)
        if not response:
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # Busca magnet link em fallback
        magnet = self._extrair_magnet(soup)
        if not magnet:
            return None

        # Extrai info_hash do magnet
        match = re.search(r"urn:btih:([a-fA-F0-9]+)", magnet)
        if not match:
            return None
        info_hash = match.group(1).lower()

        # Título da página
        titulo_tag = soup.find("h1") or soup.find("title")
        titulo = titulo_tag.get_text(strip=True) if titulo_tag else unquote(url_post.split("/")[-2])

        return TorrentResult(
            title=titulo,
            info_hash=info_hash,
            magnet=magnet,
            quality=self._detectar_qualidade(titulo),
            dubbed=self._detectar_dublado(titulo),
            source=self.name,
            size=self._extrair_tamanho(soup),
            seeders=None,
        )

    def _extrair_magnet(self, soup: BeautifulSoup) -> str | None:
        """Busca magnet link na página com fallbacks"""
        tag = soup.find("a", href=lambda h: h and h.startswith("magnet:"))
        if tag:
            return tag["href"]

        tags = soup.select("a[href^='magnet:']")
        if tags:
            return tags[0]["href"]

        # Fallback: link .torrent (loga mas ignora por ora)
        torrent_tag = soup.find("a", href=lambda h: h and ".torrent" in str(h))
        if torrent_tag:
            logger.debug(f"[{self.name}] Encontrou .torrent mas sem magnet: {torrent_tag['href']}")

        return None

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

    def _extrair_tamanho(self, soup: BeautifulSoup) -> str | None:
        """Tenta extrair o tamanho do arquivo da página"""
        texto = soup.get_text()
        match = re.search(r"(\d+[.,]?\d*\s*(?:GB|MB|TB))", texto, re.IGNORECASE)
        return match.group(1).strip() if match else None
