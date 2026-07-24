from unittest.mock import AsyncMock, MagicMock

import pytest

from app.scrapers.apache_torrent import ApacheTorrentScraper
from app.scrapers.comando_filmes import ComandoFilmesScraper

SCRAPER_CLASSES = [ApacheTorrentScraper, ComandoFilmesScraper]


@pytest.mark.parametrize("scraper_cls", SCRAPER_CLASSES)
class TestDeteccaoDeQualidade:
    def test_4k(self, scraper_cls):
        s = scraper_cls()
        assert s._detectar_qualidade("Filme.2026.4K.HDR") == "4K"

    def test_2160p_tambem_conta_como_4k(self, scraper_cls):
        s = scraper_cls()
        assert s._detectar_qualidade("Filme.2026.2160p") == "4K"

    def test_1080p(self, scraper_cls):
        s = scraper_cls()
        assert s._detectar_qualidade("Filme.2026.1080p.WEB-DL") == "1080p"

    def test_720p(self, scraper_cls):
        s = scraper_cls()
        assert s._detectar_qualidade("Filme.2026.720p") == "720p"

    def test_480p(self, scraper_cls):
        s = scraper_cls()
        assert s._detectar_qualidade("Filme.2026.480p") == "480p"

    def test_sem_qualidade_reconhecida(self, scraper_cls):
        s = scraper_cls()
        assert s._detectar_qualidade("Filme Sem Info Nenhuma") == "Desconhecida"

    def test_prioriza_4k_sobre_outras_tags_presentes(self, scraper_cls):
        s = scraper_cls()
        # título malformado com duas tags — 4K deve vencer por checagem em ordem
        assert s._detectar_qualidade("Filme 4K 1080p Remux") == "4K"


@pytest.mark.parametrize("scraper_cls", SCRAPER_CLASSES)
class TestDeteccaoDeDublado:
    @pytest.mark.parametrize("tag", ["DUBLADO", "DUAL", "NACIONAL", "PORTUGUES", "PT-BR"])
    def test_reconhece_tags_pt_br(self, scraper_cls, tag):
        s = scraper_cls()
        assert s._detectar_dublado(f"Filme.2026.1080p.{tag}") is True

    def test_titulo_sem_tag_ptbr_e_false(self, scraper_cls):
        s = scraper_cls()
        assert s._detectar_dublado("Movie.2026.1080p.ENGLISH") is False

    def test_case_insensitive(self, scraper_cls):
        s = scraper_cls()
        assert s._detectar_dublado("filme.2026.dublado") is True


@pytest.mark.parametrize("scraper_cls", SCRAPER_CLASSES)
class TestExtracaoDeTamanho:
    def test_extrai_gb(self, scraper_cls):
        s = scraper_cls()
        soup = _soup("<div>Tamanho: 4.5 GB — Qualidade: 1080p</div>")
        assert s._extrair_tamanho(soup) == "4.5 GB"

    def test_extrai_mb(self, scraper_cls):
        s = scraper_cls()
        soup = _soup("<div>Tamanho: 700 MB</div>")
        assert s._extrair_tamanho(soup) == "700 MB"

    def test_extrai_com_virgula_decimal(self, scraper_cls):
        s = scraper_cls()
        soup = _soup("<div>Tamanho: 4,5 GB</div>")
        assert s._extrair_tamanho(soup) == "4,5 GB"

    def test_sem_tamanho_na_pagina_retorna_none(self, scraper_cls):
        s = scraper_cls()
        soup = _soup("<div>Sem informação de tamanho aqui</div>")
        assert s._extrair_tamanho(soup) is None


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


@pytest.mark.parametrize("scraper_cls", SCRAPER_CLASSES)
class TestExtracaoDeMagnet:
    def test_encontra_magnet_direto(self, scraper_cls):
        s = scraper_cls()
        soup = _soup('<a href="magnet:?xt=urn:btih:abc123">baixar</a>')
        assert s._extrair_magnet(soup) == "magnet:?xt=urn:btih:abc123"

    def test_ignora_torrent_sem_magnet(self, scraper_cls):
        s = scraper_cls()
        soup = _soup('<a href="/download/filme.torrent">baixar .torrent</a>')
        assert s._extrair_magnet(soup) is None

    def test_sem_link_nenhum_retorna_none(self, scraper_cls):
        s = scraper_cls()
        soup = _soup("<div>nada aqui</div>")
        assert s._extrair_magnet(soup) is None

    def test_pega_o_primeiro_magnet_quando_ha_varios(self, scraper_cls):
        s = scraper_cls()
        soup = _soup(
            '<a href="magnet:?xt=urn:btih:primeiro">1</a>'
            '<a href="magnet:?xt=urn:btih:segundo">2</a>'
        )
        assert s._extrair_magnet(soup) == "magnet:?xt=urn:btih:primeiro"


@pytest.mark.parametrize("scraper_cls", SCRAPER_CLASSES)
class TestExtrairTorrentPaginaCompleta:
    """Testa _extrair_torrent com uma página de post realista (HTML completo),
    em vez de mockar a extração inteira como os testes de mais alto nível já
    fazem."""

    def _pagina_completa(self, titulo: str, info_hash: str) -> str:
        return f"""
        <html>
          <body>
            <h1>{titulo}</h1>
            <div class="post-content">
              <p>Tamanho: 4.2 GB</p>
              <a href="magnet:?xt=urn:btih:{info_hash}&dn={titulo}">Baixar via Magnet</a>
            </div>
          </body>
        </html>
        """

    @pytest.mark.asyncio
    async def test_extrai_torrent_com_titulo_hash_qualidade_e_tamanho(self, scraper_cls):
        s = scraper_cls()
        info_hash = "a" * 40
        response = MagicMock()
        response.text = self._pagina_completa("Filme Teste 2026 1080p Dublado", info_hash)

        import unittest.mock as um
        with um.patch.object(s, "_get", AsyncMock(return_value=response)):
            torrent = await s._extrair_torrent("https://site.com/filme-teste/")

        assert torrent is not None
        assert torrent.title == "Filme Teste 2026 1080p Dublado"
        assert torrent.info_hash == info_hash
        assert torrent.quality == "1080p"
        assert torrent.dubbed is True
        assert torrent.size == "4.2 GB"
        await s.close()

    @pytest.mark.asyncio
    async def test_sem_magnet_na_pagina_retorna_none(self, scraper_cls):
        s = scraper_cls()
        response = MagicMock()
        response.text = "<html><body><h1>Filme Sem Magnet</h1></body></html>"

        import unittest.mock as um
        with um.patch.object(s, "_get", AsyncMock(return_value=response)):
            torrent = await s._extrair_torrent("https://site.com/filme/")

        assert torrent is None
        await s.close()

    @pytest.mark.asyncio
    async def test_get_falha_retorna_none(self, scraper_cls):
        s = scraper_cls()
        import unittest.mock as um
        with um.patch.object(s, "_get", AsyncMock(return_value=None)):
            torrent = await s._extrair_torrent("https://site.com/filme/")

        assert torrent is None
        await s.close()

    @pytest.mark.asyncio
    async def test_sem_titulo_usa_slug_da_url(self, scraper_cls):
        s = scraper_cls()
        info_hash = "b" * 40
        response = MagicMock()
        response.text = (
            f'<html><body><a href="magnet:?xt=urn:btih:{info_hash}">baixar</a></body></html>'
        )

        import unittest.mock as um
        with um.patch.object(s, "_get", AsyncMock(return_value=response)):
            torrent = await s._extrair_torrent("https://site.com/meu-filme-incrivel/pagina")

        assert torrent is not None
        assert torrent.title == "meu-filme-incrivel"
        await s.close()
