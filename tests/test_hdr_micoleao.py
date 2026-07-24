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
