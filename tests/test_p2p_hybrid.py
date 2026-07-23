from unittest.mock import AsyncMock, patch

import pytest

from app.models.torrent import TorrentResult
from app.routes.configure import _build_config_html
from app.services.stream_aggregator import StreamAggregator


def _torrent() -> TorrentResult:
    return TorrentResult(
        title="Filme.Teste.2026.1080p.Dublado",
        info_hash="abc123def456abc123def456abc123def456abc1",
        magnet="magnet:?xt=urn:btih:abc123def456abc123def456abc123def456abc1",
        quality="1080p",
        dubbed=True,
        source="Fonte de Teste",
        size="2 GB",
        seeders=20,
    )


@pytest.mark.asyncio
async def test_sem_token_retorna_apenas_p2p():
    mock_cache = AsyncMock()
    mock_cache.get.return_value = None

    with patch("app.services.stream_aggregator.cache", mock_cache):
        with patch(
            "app.services.stream_aggregator._build_scraper_list",
            return_value=[],
        ):
            aggregator = StreamAggregator()

        with patch.object(
            aggregator,
            "_fetch_title",
            new=AsyncMock(return_value=("Teste", "Teste")),
        ):
            with patch.object(
                aggregator,
                "_run_scrapers",
                new=AsyncMock(return_value=[_torrent()]),
            ):
                streams = await aggregator.get_streams(
                    imdb_id="tt1234567",
                    type="movie",
                    req_id="p2p01",
                    rd_token=None,
                )

    assert len(streams) == 1
    assert streams[0].url is None
    assert streams[0].infoHash == _torrent().info_hash
    assert not any(
        call.args and str(call.args[0]).startswith("play:")
        for call in mock_cache.set.call_args_list
    )


@pytest.mark.asyncio
async def test_modo_hibrido_retorna_rd_e_p2p():
    mock_cache = AsyncMock()
    mock_cache.get.return_value = None

    with patch("app.services.stream_aggregator.cache", mock_cache):
        with patch(
            "app.services.stream_aggregator._build_scraper_list",
            return_value=[],
        ):
            aggregator = StreamAggregator()

        with patch.object(
            aggregator,
            "_fetch_title",
            new=AsyncMock(return_value=("Teste", "Teste")),
        ):
            with patch.object(
                aggregator,
                "_run_scrapers",
                new=AsyncMock(return_value=[_torrent()]),
            ):
                with patch(
                    "app.services.stream_aggregator.uuid.uuid4",
                    return_value="hybrid-play-id",
                ):
                    streams = await aggregator.get_streams(
                        imdb_id="tt1234567",
                        type="movie",
                        req_id="hybrid01",
                        rd_token="token-teste",
                        include_p2p=True,
                        request_base_url="http://localhost:8000",
                    )

    assert len(streams) == 2

    rd_stream, p2p_stream = streams
    assert rd_stream.url == "http://localhost:8000/play/hybrid-play-id"
    assert rd_stream.infoHash is None
    assert "RD" in rd_stream.name

    assert p2p_stream.url is None
    assert p2p_stream.infoHash == _torrent().info_hash
    assert "P2P" in p2p_stream.name


def test_pagina_configuracao_oferece_modo_hibrido():
    html = _build_config_html()

    assert 'id="include-p2p"' in html
    assert "/hybrid/" in html
    assert "Sem token: o link sera gerado no modo P2P." in html
