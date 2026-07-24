import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.torrent import TorrentResult
from app.scrapers.apache_torrent import ApacheTorrentScraper
from app.scrapers.archive_org import ArchiveOrgScraper
from app.scrapers.comando_filmes import ComandoFilmesScraper


def _fake_wordpress_html(links: list[str]) -> str:
    posts = "".join(
        f'<article class="post"><h2 class="entry-title"><a href="{link}">titulo</a></h2></article>'
        for link in links
    )
    return f"<html><body>{posts}</body></html>"


async def _tracked_extrair_torrent(link: str, tracker: dict) -> TorrentResult:
    """Simula _extrair_torrent registrando concorrência real via contador."""
    tracker["atual"] += 1
    tracker["max"] = max(tracker["max"], tracker["atual"])
    await asyncio.sleep(0.01)
    tracker["atual"] -= 1
    h = hashlib.sha1(link.encode()).hexdigest()
    return TorrentResult(
        title="Filme Teste 2026 1080p Dublado",
        info_hash=h,
        magnet=f"magnet:?xt=urn:btih:{h}",
        quality="1080p",
        dubbed=True,
        source="teste",
    )


class TestProfundidadeApacheTorrent:
    @pytest.mark.asyncio
    async def test_processa_ate_max_detail_pages_em_paralelo(self):
        scraper = ApacheTorrentScraper()
        links = [f"https://apachetorrent.com/post-{i}/" for i in range(15)]
        response = MagicMock()
        response.url = "https://apachetorrent.com/?s=Filme"
        response.text = _fake_wordpress_html(links)

        tracker = {"atual": 0, "max": 0}

        async def _side_effect(link: str) -> TorrentResult:
            return await _tracked_extrair_torrent(link, tracker)

        with patch.object(scraper, "_get_with_fallback", AsyncMock(return_value=response)):
            with patch.object(
                scraper, "_extrair_torrent", AsyncMock(side_effect=_side_effect),
            ):
                resultados = await scraper.search("Filme Teste", "tt0000000", "movie")

        # Antes o limite era 5 (e sequencial) — agora processa MAX_DETAIL_PAGES (10),
        # mesmo havendo 15 links disponíveis na página de busca.
        assert len(resultados) == scraper.MAX_DETAIL_PAGES == 10
        # Rodou de fato em paralelo, mas respeitando o teto do semáforo.
        assert tracker["max"] > 1
        assert tracker["max"] <= scraper.MAX_CONCURRENT_DETAIL_FETCHES
        await scraper.close()


class TestProfundidadeComandoFilmes:
    @pytest.mark.asyncio
    async def test_processa_ate_max_detail_pages_em_paralelo(self):
        scraper = ComandoFilmesScraper()
        links = [f"https://baixafilmestorrent.org/post-{i}/" for i in range(15)]
        response = MagicMock()
        response.url = "https://baixafilmestorrent.org/?s=Filme"
        response.text = _fake_wordpress_html(links)

        tracker = {"atual": 0, "max": 0}

        async def _side_effect(link: str) -> TorrentResult:
            return await _tracked_extrair_torrent(link, tracker)

        with patch.object(scraper, "_get_with_fallback", AsyncMock(return_value=response)):
            with patch.object(
                scraper, "_extrair_torrent", AsyncMock(side_effect=_side_effect),
            ):
                resultados = await scraper.search("Filme Teste", "tt0000000", "movie")

        assert len(resultados) == scraper.MAX_DETAIL_PAGES == 10
        assert tracker["max"] > 1
        assert tracker["max"] <= scraper.MAX_CONCURRENT_DETAIL_FETCHES
        await scraper.close()


class TestProfundidadeArchiveOrg:
    @pytest.mark.asyncio
    async def test_avalia_ate_max_candidates_em_paralelo(self):
        scraper = ArchiveOrgScraper()
        docs = [
            {"identifier": f"filme_teste_{i}", "title": "Filme Teste 2026"} for i in range(12)
        ]
        search_response = MagicMock()
        search_response.json.return_value = {"response": {"docs": docs}}

        tracker = {"atual": 0, "max": 0}

        async def fake_extrair_torrent(identifier, titulo):
            tracker["atual"] += 1
            tracker["max"] = max(tracker["max"], tracker["atual"])
            await asyncio.sleep(0.01)
            tracker["atual"] -= 1
            h = hashlib.sha1(identifier.encode()).hexdigest()
            return TorrentResult(
                title=f"{titulo} [Internet Archive]",
                info_hash=h,
                magnet=f"magnet:?xt=urn:btih:{h}",
                quality="Desconhecida",
                dubbed=False,
                source=scraper.name,
            )

        with patch.object(scraper, "_get", AsyncMock(return_value=search_response)):
            with patch.object(scraper, "_extrair_torrent", AsyncMock(side_effect=fake_extrair_torrent)):
                resultados = await scraper.search("Filme Teste", "tt0000000", "movie")

        # Antes o limite era 5 — agora MAX_CANDIDATES (8), mesmo com 12 docs no retorno.
        assert len(resultados) == scraper.MAX_CANDIDATES == 8
        assert tracker["max"] > 1
        assert tracker["max"] <= scraper.MAX_CONCURRENT_TORRENT_FETCHES
        await scraper.close()
