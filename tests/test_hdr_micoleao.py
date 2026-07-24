from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest

from app.scrapers.hdr_torrent import HDRTorrentScraper
from app.scrapers.micoleao import MicoLeaoScraper


def _wordpress_html(entries: list[tuple[str, str]]) -> str:
    """entries: lista de (href, texto do link)."""
    posts = "".join(
        f'<article class="post"><h2><a href="{href}">{texto}</a></h2></article>'
        for href, texto in entries
    )
    return f"<html><body>{posts}</body></html>"


class _CasoDeTeste:
    """Agrupa os dois scrapers, que têm exatamente a mesma estrutura, num só
    conjunto de testes parametrizado por classe."""

    scraper_cls: type
    dominio: str


@pytest.mark.parametrize(
    "scraper_cls,dominio",
    [
        (HDRTorrentScraper, "https://www.hdrtorrent.net"),
        (MicoLeaoScraper, "https://www.micoleaodublado.net"),
    ],
)
class TestFiltroDeRelevancia:
    """
    Antes, esses dois scrapers pegavam QUALQUER link que batesse no seletor
    genérico (article a, .post a, h2 a, h3 a) — inclusive posts recentes
    da barra lateral, sem nenhuma relação com a busca.
    """

    @pytest.mark.asyncio
    async def test_descarta_resultado_sem_relacao_com_a_busca(self, scraper_cls, dominio):
        scraper = scraper_cls()
        html = _wordpress_html([
            (f"{dominio}/interestelar-1080p/", "Interestelar 1080p Dublado"),
            (f"{dominio}/anuncio-nao-relacionado/", "Anne Frank, Minha Melhor Amiga"),
        ])
        response = MagicMock()
        response.url = f"{dominio}/?s=Interestelar"
        response.text = html

        async def fake_extrair(url_post):
            from app.models.torrent import TorrentResult
            titulo = "Interestelar 1080p Dublado" if "interestelar" in url_post else "Anne Frank, Minha Melhor Amiga"
            h = str(abs(hash(url_post)))[:40].ljust(40, "0")
            return TorrentResult(
                title=titulo, info_hash=h, magnet=f"magnet:?xt=urn:btih:{h}",
                quality="1080p", dubbed=True, source=scraper_cls.name,
            )

        with patch.object(scraper, "_get_with_fallback", AsyncMock(return_value=response)):
            with patch.object(scraper, "_extrair_torrent", AsyncMock(side_effect=fake_extrair)):
                resultados = await scraper.search("Interestelar", "tt0816692", "movie")

        assert len(resultados) == 1
        assert "Interestelar" in resultados[0].title
        await scraper.close()

    @pytest.mark.asyncio
    async def test_query_e_url_encoded(self, scraper_cls, dominio):
        """Antes faltava quote() — espaço/acento ia cru pra URL."""
        scraper = scraper_cls()

        with patch.object(scraper, "_get_with_fallback", AsyncMock(return_value=None)) as get_mock:
            await scraper.search("João & Maria", "tt0000000", "movie")

        urls_chamadas = get_mock.await_args.args[0]
        assert all(quote("João & Maria") in url for url in urls_chamadas)
        await scraper.close()

    @pytest.mark.asyncio
    async def test_serie_tenta_query_com_episodio_primeiro(self, scraper_cls, dominio):
        scraper = scraper_cls()

        with patch.object(scraper, "_get_with_fallback", AsyncMock(return_value=None)) as get_mock:
            await scraper.search("Breaking Bad", "tt0903747", "series", season=1, episode=5)

        primeira_chamada_urls = get_mock.await_args_list[0].args[0]
        assert any(quote("Breaking Bad S01E05") in url for url in primeira_chamada_urls)
        await scraper.close()

    @pytest.mark.asyncio
    async def test_sem_links_retorna_vazio_sem_erro(self, scraper_cls, dominio):
        scraper = scraper_cls()
        response = MagicMock()
        response.url = f"{dominio}/?s=Nada"
        response.text = "<html><body>sem posts aqui</body></html>"

        with patch.object(scraper, "_get_with_fallback", AsyncMock(return_value=response)):
            resultados = await scraper.search("Nada", "tt0000000", "movie")

        assert resultados == []
        await scraper.close()


@pytest.mark.parametrize(
    "scraper_cls",
    [HDRTorrentScraper, MicoLeaoScraper],
)
class TestExtrairTorrentPaginaCompleta:
    """_extrair_torrent nunca tinha teste direto — só era exercitado via mock
    nos testes de mais alto nível."""

    @pytest.mark.asyncio
    async def test_extrai_titulo_hash_qualidade_e_tamanho(self, scraper_cls):
        scraper = scraper_cls()
        info_hash = "c" * 40
        response = MagicMock()
        response.text = f"""
        <html><body>
          <h1>Filme Teste 2026 1080p Dublado</h1>
          <p>Tamanho: 3.8 GB</p>
          <a href="magnet:?xt=urn:btih:{info_hash}">Magnet</a>
        </body></html>
        """

        with patch.object(scraper, "_get", AsyncMock(return_value=response)):
            torrent = await scraper._extrair_torrent("https://site.com/filme/")

        assert torrent is not None
        assert torrent.info_hash == info_hash
        assert torrent.quality == "1080p"
        assert torrent.dubbed is True
        assert torrent.size == "3.8 GB"
        await scraper.close()

    @pytest.mark.asyncio
    async def test_sem_magnet_retorna_none(self, scraper_cls):
        scraper = scraper_cls()
        response = MagicMock()
        response.text = "<html><body><h1>Sem magnet aqui</h1></body></html>"

        with patch.object(scraper, "_get", AsyncMock(return_value=response)):
            torrent = await scraper._extrair_torrent("https://site.com/filme/")

        assert torrent is None
        await scraper.close()


class TestHDRQualidadeEspecial:
    """HDR Torrent prioriza detecção de Dolby Vision/HDR sobre 4K genérico."""

    def test_dolby_vision_com_espaco_tem_prioridade_sobre_4k(self):
        scraper = HDRTorrentScraper()
        assert scraper._detectar_qualidade("Filme 2026 4K Dolby Vision") == "4K DolbyVision"

    def test_dolby_vision_com_pontos_tambem_e_reconhecido(self):
        """Bug: a checagem só cobria 'DOLBY VISION' com espaço — títulos no
        estilo scene release com pontos ('Dolby.Vision') não batiam."""
        scraper = HDRTorrentScraper()
        assert scraper._detectar_qualidade("Filme.2026.4K.Dolby.Vision") == "4K DolbyVision"

    def test_dv_abreviado_isolado_e_reconhecido(self):
        scraper = HDRTorrentScraper()
        assert scraper._detectar_qualidade("Filme.2026.2160p.DV.HDR10") == "4K DolbyVision"

    def test_dvdrip_nao_e_falso_positivo_de_dolby_vision(self):
        """Bug: 'DV' in titulo_upper é substring solta — 'DVDRip' contém
        'DV' e virava Dolby Vision incorretamente."""
        scraper = HDRTorrentScraper()
        assert scraper._detectar_qualidade("Filme.2026.DVDRip.Dual.Audio") != "4K DolbyVision"

    def test_hdr_com_4k_vira_4k_hdr(self):
        scraper = HDRTorrentScraper()
        assert scraper._detectar_qualidade("Filme.2026.4K.HDR10") == "4K HDR"

    def test_4k_sem_hdr_e_so_4k(self):
        scraper = HDRTorrentScraper()
        assert scraper._detectar_qualidade("Filme.2026.4K") == "4K"
