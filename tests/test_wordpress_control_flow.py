from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scrapers.apache_torrent import ApacheTorrentScraper
from app.scrapers.comando_filmes import ComandoFilmesScraper

SCRAPER_CLASSES = [ApacheTorrentScraper, ComandoFilmesScraper]


@pytest.mark.parametrize("scraper_cls", SCRAPER_CLASSES)
class TestBuscarQueryFluxosDeControle:
    @pytest.mark.asyncio
    async def test_sem_resposta_de_nenhum_mirror_retorna_vazio(self, scraper_cls):
        scraper = scraper_cls()
        with patch.object(scraper, "_get_with_fallback", AsyncMock(return_value=None)):
            resultados = await scraper.search("Filme", "tt0000000", "movie")

        assert resultados == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_pagina_sem_posts_retorna_vazio(self, scraper_cls):
        scraper = scraper_cls()
        response = MagicMock()
        response.url = "https://site.com/?s=Filme"
        response.text = "<html><body><div>nenhum resultado encontrado</div></body></html>"

        with patch.object(scraper, "_get_with_fallback", AsyncMock(return_value=response)):
            resultados = await scraper.search("Filme", "tt0000000", "movie")

        assert resultados == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_excecao_ao_extrair_um_post_nao_derruba_os_outros(self, scraper_cls):
        """Uma falha ao processar um post específico não deve impedir que os
        outros candidatos da mesma busca sejam processados."""
        scraper = scraper_cls()
        from urllib.parse import urlparse
        dominio = urlparse(scraper.base_url).netloc
        links = [f"https://{dominio}/post-{i}/" for i in range(3)]

        response = MagicMock()
        response.url = f"https://{dominio}/?s=Filme"
        response.text = "".join(
            f'<article class="post"><h2 class="entry-title"><a href="{link}">t</a></h2></article>'
            for link in links
        )

        from app.models.torrent import TorrentResult

        async def extrair_com_falha_no_segundo(url_post):
            if url_post == links[1]:
                raise ValueError("erro ao parsear a página")
            h = "a" * 39 + str(links.index(url_post))
            return TorrentResult(
                title="Filme Teste 2026 1080p Dublado",
                info_hash=h, magnet=f"magnet:?xt=urn:btih:{h}",
                quality="1080p", dubbed=True, source=scraper_cls.name,
            )

        with patch.object(scraper, "_get_with_fallback", AsyncMock(return_value=response)):
            with patch.object(
                scraper, "_extrair_torrent", AsyncMock(side_effect=extrair_com_falha_no_segundo)
            ):
                resultados = await scraper.search("Filme Teste", "tt0000000", "movie")

        # 2 dos 3 posts processados com sucesso — o que falhou não derruba os outros
        assert len(resultados) == 2
        await scraper.close()
