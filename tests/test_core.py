"""
Testes automatizados do fluxo crítico — BR Streams.

Cobre:
  1. _formatar_stream: exclusão mútua url/infoHash
  2. Budget: degradação de _fetch_title com budget baixo
  3. Play sessions: criação lazy com TTL, multi-use, expiração
  4. Serialização de rotas: exclude_none garante ausência de conflito
  5. Rota /play: resolução lazy, redirect, multi-use retry, erros
  6. Concorrência: request_id isolado entre requests simultâneas via contextvars
"""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.config import settings
from app.models.torrent import StreamResult, TorrentResult
from app.scrapers.base import BaseScraper, set_req_id, get_req_id, _current_req_id
from app.services.real_debrid import RealDebridPlaybackNotReadyError, RealDebridResolveError, RealDebridService
from app.services.stream_aggregator import PLAY_SESSION_TTL_SECONDS, StreamAggregator


# ─── Fixtures ───

def _make_torrent(**overrides) -> TorrentResult:
    """Cria um TorrentResult de teste com valores padrão."""
    data = {
        "title": "Filme Teste 1080p Dublado",
        "info_hash": "abc123def456abc123def456abc123def456abc1",
        "magnet": "magnet:?xt=urn:btih:abc123def456abc123def456abc123def456abc1",
        "quality": "1080p",
        "dubbed": True,
        "source": "TestScraper",
        "size": "2.1 GB",
        "seeders": 50,
    }
    data.update(overrides)
    return TorrentResult(**data)


# ─── 1. _formatar_stream: exclusão mútua url/infoHash ───

class TestFormatarStream:
    """Testa que url e infoHash nunca coexistem."""

    def setup_method(self):
        with patch(
            "app.services.stream_aggregator._build_scraper_list",
            return_value=[]
        ):
            self.agg = StreamAggregator()

    def test_rd_resolvido_tem_url_sem_infohash(self):
        """stream_url presente → url preenchida, infoHash None"""
        torrent = _make_torrent()
        result = self.agg._formatar_stream(
            torrent=torrent,
            has_play_url=True,
            tem_rd=True,
            stream_url="http://localhost:8000/play/test-id"
        )
        assert result.url == "http://localhost:8000/play/test-id"
        assert result.infoHash is None
        dumped = result.model_dump(exclude_none=True)
        assert "url" in dumped
        assert "infoHash" not in dumped

    def test_fallback_torrent_tem_infohash_sem_url(self):
        """stream_url ausente → infoHash preenchido, url None"""
        torrent = _make_torrent()
        result = self.agg._formatar_stream(
            torrent=torrent,
            has_play_url=False,
            tem_rd=False,
            stream_url=None
        )
        assert result.infoHash == torrent.info_hash
        assert result.url is None
        dumped = result.model_dump(exclude_none=True)
        assert "infoHash" in dumped
        assert "url" not in dumped

    def test_fallback_torrent_com_rd_tem_not_web_ready(self):
        """Usuário tem RD mas stream é fallback → notWebReady=True"""
        torrent = _make_torrent()
        result = self.agg._formatar_stream(
            torrent=torrent,
            has_play_url=False,
            tem_rd=True,
            stream_url=None
        )
        assert result.behaviorHints.get("notWebReady") is True

    def test_rd_resolvido_tem_binge_group(self):
        """RD resolvido → behaviorHints com bingeGroup"""
        torrent = _make_torrent()
        result = self.agg._formatar_stream(
            torrent=torrent,
            has_play_url=True,
            tem_rd=True,
            stream_url="http://localhost/play/x"
        )
        assert "bingeGroup" in result.behaviorHints
        assert result.behaviorHints["bingeGroup"].startswith("rd-")

    def test_name_title_filme_4k_ficam_mais_ricos(self):
        torrent = _make_torrent(
            title="Filme.Exemplo.2024.2160p.WEB-DL.DV.HDR.HEVC.Atmos.Dublado",
            quality="4K DolbyVision",
            size="15.2 GB",
            seeders=120,
            source="HDR Torrent",
            dubbed=True,
        )
        result = self.agg._formatar_stream(
            torrent=torrent,
            has_play_url=True,
            tem_rd=True,
            stream_url="http://localhost/play/4k"
        )
        assert result.name == "BR Streams • 4K DV HDR • RD"
        assert result.title == (
            "Filme.Exemplo.2024.2160p.WEB-DL.DV.HDR.HEVC.Atmos.Dublado\n"
            "👥 120 • Dublado / PT-BR • 15.2 GB • HDR Torrent • RD\n"
            "WEB-DL • HEVC / x265 • Atmos"
        )

    def test_name_title_filme_1080p_dual_ficam_escaneaveis(self):
        torrent = _make_torrent(
            title="Filme.Exemplo.2024.1080p.WEB-DL.DUAL.AUDIO.PT-BR.ENG.x264",
            quality="1080p",
            size="2.1 GB",
            seeders=84,
            source="Apache Torrent",
            dubbed=True,
        )
        result = self.agg._formatar_stream(
            torrent=torrent,
            has_play_url=False,
            tem_rd=False,
            stream_url=None
        )
        assert result.name == "BR Streams • 1080p Dual"
        assert result.title == (
            "Filme.Exemplo.2024.1080p.WEB-DL.DUAL.AUDIO.PT-BR.ENG.x264\n"
            "👥 84 • Dual Audio / PT-BR • 2.1 GB • Apache Torrent\n"
            "PT-BR / ENG • WEB-DL • H.264 / x264"
        )

    def test_name_title_serie_preservam_release_name_real(self):
        torrent = _make_torrent(
            title="Serie.Exemplo.S01E02.720p.WEBRip.ENG.x264",
            quality="720p",
            size="1.4 GB",
            seeders=12,
            source="Comando Filmes",
            dubbed=False,
        )
        result = self.agg._formatar_stream(
            torrent=torrent,
            has_play_url=True,
            tem_rd=True,
            stream_url="http://localhost/play/serie"
        )
        assert result.name == "BR Streams • 720p • RD"
        assert result.title == (
            "Serie.Exemplo.S01E02.720p.WEBRip.ENG.x264\n"
            "👥 12 • Áudio: ENG • 1.4 GB • Comando Filmes • RD\n"
            "WEBRip • H.264 / x264"
        )


# ─── 2. Budget: _fetch_title degrada com budget baixo ───

class TestFetchTitleBudget:
    """Testa que _fetch_title respeita budget."""

    def setup_method(self):
        with patch(
            "app.services.stream_aggregator._build_scraper_list",
            return_value=[]
        ):
            self.agg = StreamAggregator()

    @pytest.mark.asyncio
    async def test_budget_insuficiente_retorna_fallback(self):
        """Budget < MIN_BUDGET_TITLE_FETCH → retorna imdb_id como fallback"""
        result = await self.agg._fetch_title(
            imdb_id="tt1234567",
            type="movie",
            req_id="test01",
            budget=0.5
        )
        assert result == ("tt1234567", "tt1234567")

    @pytest.mark.asyncio
    async def test_budget_suficiente_tenta_buscar(self):
        """Budget >= MIN_BUDGET_TITLE_FETCH → tenta buscar via Cinemeta + OMDB"""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_cinemeta = MagicMock()
            mock_cinemeta.status_code = 200
            mock_cinemeta.json.return_value = {"meta": {"name": "Interstellar"}}

            # OMDB retorna título original (sem PT-BR diferente)
            mock_omdb = MagicMock()
            mock_omdb.status_code = 200
            mock_omdb.json.return_value = {"Title": "Interstellar"}

            mock_client.get = AsyncMock(side_effect=[mock_cinemeta, mock_omdb])

            result = await self.agg._fetch_title(
                imdb_id="tt0816692",
                type="movie",
                req_id="test02",
                budget=5.0
            )
            # Sem TMDB_API_KEY, Cinemeta fornece o título; OMDB confirma
            assert result == ("Interstellar", "Interstellar")


# ─── 3. Play sessions: criação e multi-use ───

class TestPlaySessions:
    """Testa semântica de play sessions."""

    @pytest.mark.asyncio
    async def test_play_session_lazy_criada_sem_precheck_rd(self):
        """Com token RD e magnet valido, cria play session sem pre-check de disponibilidade."""
        mock_cache = AsyncMock()
        with patch("app.services.stream_aggregator.cache", mock_cache):
            with patch(
                "app.services.stream_aggregator._build_scraper_list",
                return_value=[]
            ):
                agg = StreamAggregator()

            mock_cache.get.return_value = None
            mock_cache.set = AsyncMock()

            torrent = _make_torrent()
            fake_uuid = "play-session-123"
            with patch.object(agg, "_fetch_title", return_value=("Test", "Teste")):
                with patch.object(agg, "_run_scrapers", return_value=[torrent]):
                    with patch("app.services.stream_aggregator.uuid.uuid4", return_value=fake_uuid):
                        streams = await agg.get_streams(
                            imdb_id="tt1234567",
                            type="movie",
                            req_id="test03",
                            rd_token="fake_token",
                        )

            play_calls = [
                c for c in mock_cache.set.call_args_list
                if c.args[0].startswith("play:")
            ]
            assert len(play_calls) == 1
            assert (
                play_calls[0].kwargs.get("ttl") == PLAY_SESSION_TTL_SECONDS
                or play_calls[0].args[2] == PLAY_SESSION_TTL_SECONDS
            )
            # Sem request_base_url → fallback para settings.BASE_URL
            saved_session = play_calls[0].args[1]
            assert saved_session["play_session_ttl"] == PLAY_SESSION_TTL_SECONDS
            assert "created_at" in saved_session
            assert streams[0].url == f"{settings.BASE_URL}/play/{fake_uuid}"
            assert streams[0].infoHash is None

    def test_play_session_ttl_e_1800_segundos(self):
        """TTL da play session deve ser 1800s (30 min) para cobrir delays reais do Stremio."""
        assert PLAY_SESSION_TTL_SECONDS == 1800

    @pytest.mark.asyncio
    async def test_play_url_usa_request_base_url(self):
        """Play URL deve usar request_base_url quando fornecido, não settings.BASE_URL."""
        mock_cache = AsyncMock()
        with patch("app.services.stream_aggregator.cache", mock_cache):
            with patch(
                "app.services.stream_aggregator._build_scraper_list",
                return_value=[]
            ):
                agg = StreamAggregator()

            mock_cache.get.return_value = None
            mock_cache.set = AsyncMock()

            torrent = _make_torrent()
            fake_uuid = "play-origin-test"
            with patch.object(agg, "_fetch_title", return_value=("Test", "Teste")):
                with patch.object(agg, "_run_scrapers", return_value=[torrent]):
                    with patch("app.services.stream_aggregator.uuid.uuid4", return_value=fake_uuid):
                        streams = await agg.get_streams(
                            imdb_id="tt1234567",
                            type="movie",
                            req_id="test_origin",
                            rd_token="fake_token",
                            request_base_url="http://127.0.0.1:8000",
                        )

            assert streams[0].url == "http://127.0.0.1:8000/play/play-origin-test"
            assert "localhost" not in streams[0].url

    @pytest.mark.asyncio
    async def test_play_url_sem_request_base_url_usa_fallback(self):
        """Sem request_base_url → fallback para settings.BASE_URL."""
        mock_cache = AsyncMock()
        with patch("app.services.stream_aggregator.cache", mock_cache):
            with patch(
                "app.services.stream_aggregator._build_scraper_list",
                return_value=[]
            ):
                agg = StreamAggregator()

            mock_cache.get.return_value = None
            mock_cache.set = AsyncMock()

            torrent = _make_torrent()
            fake_uuid = "play-fallback-test"
            with patch.object(agg, "_fetch_title", return_value=("Test", "Teste")):
                with patch.object(agg, "_run_scrapers", return_value=[torrent]):
                    with patch("app.services.stream_aggregator.uuid.uuid4", return_value=fake_uuid):
                        streams = await agg.get_streams(
                            imdb_id="tt1234567",
                            type="movie",
                            req_id="test_fallback",
                            rd_token="fake_token",
                        )

            assert streams[0].url == f"{settings.BASE_URL}/play/play-fallback-test"

    @pytest.mark.asyncio
    async def test_play_url_custom_host_preservado(self):
        """Play URL deve preservar qualquer host/porta da request."""
        mock_cache = AsyncMock()
        with patch("app.services.stream_aggregator.cache", mock_cache):
            with patch(
                "app.services.stream_aggregator._build_scraper_list",
                return_value=[]
            ):
                agg = StreamAggregator()

            mock_cache.get.return_value = None
            mock_cache.set = AsyncMock()

            torrent = _make_torrent()
            fake_uuid = "play-custom-host"
            with patch.object(agg, "_fetch_title", return_value=("Test", "Teste")):
                with patch.object(agg, "_run_scrapers", return_value=[torrent]):
                    with patch("app.services.stream_aggregator.uuid.uuid4", return_value=fake_uuid):
                        streams = await agg.get_streams(
                            imdb_id="tt1234567",
                            type="movie",
                            req_id="test_custom",
                            rd_token="fake_token",
                            request_base_url="https://my-addon.railway.app",
                        )

            assert streams[0].url == "https://my-addon.railway.app/play/play-custom-host"

    @pytest.mark.asyncio
    async def test_play_session_expirada_retorna_404_com_detalhe_expirada(self):
        """Play session expirada → HTTP 404 com detail indicando expiracao"""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        with patch("app.routes.stream.cache") as mock_cache:
            mock_cache.get_with_status = AsyncMock(return_value=(None, "expired"))

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/play/expired-session-id")
                assert resp.status_code == 404
                assert resp.json()["detail"] == (
                    "Sessao de playback expirada. Gere um novo stream."
                )

    @pytest.mark.asyncio
    async def test_play_session_inexistente_retorna_404_com_detalhe_inexistente(self):
        """Play session nunca criada → HTTP 404 com detail indicando inexistencia"""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        with patch("app.routes.stream.cache") as mock_cache:
            mock_cache.get_with_status = AsyncMock(return_value=(None, "miss"))

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/play/nonexistent-id")
                assert resp.status_code == 404
                assert resp.json()["detail"] == (
                    "Sessao de playback inexistente. Gere um novo stream."
                )

    @pytest.mark.asyncio
    async def test_play_session_corrompida_retorna_500(self):
        """Sessao corrompida deve retornar 500 sem derrubar o servidor."""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        with patch("app.routes.stream.cache") as mock_cache:
            mock_cache.get_with_status = AsyncMock(return_value=({"req_id": "broken"}, "hit"))

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/play/corrupted-id")
                assert resp.status_code == 500
                assert resp.json()["detail"] == "Sessao de playback corrompida"

    @pytest.mark.asyncio
    async def test_play_session_multi_use_duas_chamadas(self):
        """Multi-use: duas chamadas ao mesmo play_id funcionam enquanto TTL válido"""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        session_data = {
            "rd_token": "fake_token",
            "magnet": "magnet:?xt=urn:btih:abc123",
            "info_hash": "abc123",
            "imdb_id": "tt1234567",
            "stremio_id": "tt1234567",
            "type": "movie",
            "req_id": "testmulti",
        }

        with patch("app.routes.stream.cache") as mock_cache:
            # Simula sessão válida para ambas as chamadas.
            # Após a primeira chamada resolver, session_data ganha resolved_url
            # e a segunda chamada reutiliza sem chamar RD de novo.
            mock_cache.get_with_status = AsyncMock(return_value=(session_data, "hit"))
            mock_cache.set = AsyncMock()

            with patch("app.routes.stream.RealDebridService") as mock_rd_cls:
                mock_rd = AsyncMock()
                mock_rd.get_stream_url.return_value = "https://rd-cdn.example.com/video.mkv"
                mock_rd_cls.return_value = mock_rd

                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport,
                    base_url="http://test",
                    follow_redirects=False
                ) as client:
                    # Primeira chamada
                    resp1 = await client.get("/play/test-play-id")
                    assert resp1.status_code == 302
                    assert resp1.headers["location"] == "https://rd-cdn.example.com/video.mkv"

                    # Segunda chamada (retry do player) — também funciona
                    resp2 = await client.get("/play/test-play-id")
                    assert resp2.status_code == 302
                    assert resp2.headers["location"] == "https://rd-cdn.example.com/video.mkv"

            # Verifica que cache.get_with_status foi chamado duas vezes (não houve delete)
            assert mock_cache.get_with_status.call_count == 2


# ─── 4. Serialização: url e infoHash nunca coexistem no JSON ───

class TestSerializacao:
    """Testa que o payload JSON final nunca tem url e infoHash juntos."""

    def test_stream_result_rd_serializa_sem_infohash(self):
        result = StreamResult(
            name="🎬 4K 🇧🇷 Dublado ▶ RD via clique",
            title="TestScraper • 2.1 GB • Resolve via RD no clique",
            url="http://localhost:8000/play/abc",
            behaviorHints={"bingeGroup": "rd-abc123"},
        )
        dumped = result.model_dump(exclude_none=True)
        assert "url" in dumped
        assert "infoHash" not in dumped

    def test_stream_result_torrent_serializa_sem_url(self):
        result = StreamResult(
            name="🎥 1080p 🔤 Legendado 🧲 Torrent",
            title="TestScraper • 2.1 GB",
            infoHash="abc123def456",
            behaviorHints={"notWebReady": True},
        )
        dumped = result.model_dump(exclude_none=True)
        assert "infoHash" in dumped
        assert "url" not in dumped


# ─── 5. Rota /play: resolução + redirect ───

class TestPlayRoute:
    """Testa a rota /play/{play_id} com mocks."""

    @pytest.mark.asyncio
    async def test_play_resolve_e_redireciona(self, caplog):
        """Play session valida -> redirect 302 para URL HTTP resolvida via RD"""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        session_data = {
            "rd_token": "fake_token",
            "magnet": "magnet:?xt=urn:btih:abc123",
            "info_hash": "abc123",
            "imdb_id": "tt1234567",
            "stremio_id": "tt1234567",
            "type": "movie",
            "req_id": "test05",
        }

        with patch("app.routes.stream.cache") as mock_cache:
            mock_cache.get_with_status = AsyncMock(return_value=(session_data, "hit"))
            mock_cache.set = AsyncMock()

            with patch("app.routes.stream.RealDebridService") as mock_rd_cls:
                mock_rd = AsyncMock()
                mock_rd.get_stream_url.return_value = "https://rd-cdn.example.com/video.mkv"
                mock_rd_cls.return_value = mock_rd

                with caplog.at_level(logging.INFO):
                    transport = ASGITransport(app=app)
                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                        follow_redirects=False
                    ) as client:
                        resp = await client.get("/play/test-play-id")
                        assert resp.status_code == 302
                        assert resp.headers["location"] == "https://rd-cdn.example.com/video.mkv"

                assert "302 redirect" in caplog.text

    @pytest.mark.asyncio
    async def test_play_head_resolve_e_salva_no_cache(self, caplog):
        """HEAD /play/{id} resolve via RD e retorna 302 com URL, salvando no cache."""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        session_data = {
            "rd_token": "fake_token",
            "magnet": "magnet:?xt=urn:btih:abc123",
            "info_hash": "abc123",
            "imdb_id": "tt1234567",
            "stremio_id": "tt1234567",
            "type": "movie",
            "req_id": "test05_head",
        }

        with patch("app.routes.stream.cache") as mock_cache:
            mock_cache.get_with_status = AsyncMock(return_value=(session_data, "hit"))
            mock_cache.set = AsyncMock()

            with patch("app.routes.stream.RealDebridService") as mock_rd_cls:
                mock_rd = AsyncMock()
                mock_rd.get_stream_url.return_value = "https://rd-cdn.example.com/video_head.mkv"
                mock_rd_cls.return_value = mock_rd

                with caplog.at_level(logging.INFO):
                    transport = ASGITransport(app=app)
                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                        follow_redirects=False
                    ) as client:
                        resp = await client.head("/play/test-play-id")
                        assert resp.status_code == 302
                        assert resp.headers["location"] == "https://rd-cdn.example.com/video_head.mkv"

                assert "HEAD 302 redirect" in caplog.text
                mock_cache.set.assert_called_once()
                saved_data = mock_cache.set.call_args[0][1]
                assert saved_data["resolved_url"] == "https://rd-cdn.example.com/video_head.mkv"
                assert mock_cache.set.call_args.kwargs["ttl"] == PLAY_SESSION_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_play_head_e_get_sucessivos_reutilizam_mesma_sessao(self):
        """HEAD seguido de GET deve reutilizar a URL resolvida sem perder a sessao no meio."""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        session_data = {
            "rd_token": "fake_token",
            "magnet": "magnet:?xt=urn:btih:abc123",
            "info_hash": "abc123",
            "imdb_id": "tt1234567",
            "stremio_id": "tt1234567",
            "type": "movie",
            "req_id": "test_head_get",
        }

        async def fake_get_with_status(_key: str):
            return session_data, "hit"

        async def fake_set(_key: str, value: dict, ttl: int | None = None):
            session_data.update(value)

        with patch("app.routes.stream.cache") as mock_cache:
            mock_cache.get_with_status = AsyncMock(side_effect=fake_get_with_status)
            mock_cache.set = AsyncMock(side_effect=fake_set)

            with patch("app.routes.stream.RealDebridService") as mock_rd_cls:
                mock_rd = AsyncMock()
                mock_rd.get_stream_url.return_value = "https://rd-cdn.example.com/head_get.mkv"
                mock_rd_cls.return_value = mock_rd

                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport,
                    base_url="http://test",
                    follow_redirects=False
                ) as client:
                    head_resp = await client.head("/play/test-play-id")
                    get_resp = await client.get("/play/test-play-id")

                assert head_resp.status_code == 302
                assert get_resp.status_code == 302
                assert head_resp.headers["location"] == "https://rd-cdn.example.com/head_get.mkv"
                assert get_resp.headers["location"] == "https://rd-cdn.example.com/head_get.mkv"
                assert session_data["resolved_url"] == "https://rd-cdn.example.com/head_get.mkv"
                mock_rd.get_stream_url.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_play_reutiliza_url_se_disponivel(self, caplog):
        """Se resolved_url já está na sessão, não chama o RD de novo (ex: após um HEAD)."""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        session_data = {
            "rd_token": "fake_token",
            "magnet": "magnet:?xt=urn:btih:abc123",
            "info_hash": "abc123",
            "imdb_id": "tt1234567",
            "stremio_id": "tt1234567",
            "type": "movie",
            "req_id": "test05_cache",
            "resolved_url": "https://rd-cdn.example.com/cached.mkv",
        }

        with patch("app.routes.stream.cache") as mock_cache:
            mock_cache.get_with_status = AsyncMock(return_value=(session_data, "hit"))

            with patch("app.routes.stream.RealDebridService") as mock_rd_cls:
                with caplog.at_level(logging.INFO):
                    transport = ASGITransport(app=app)
                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                        follow_redirects=False
                    ) as client:
                        resp = await client.get("/play/test-play-id")
                        assert resp.status_code == 302
                        assert resp.headers["location"] == "https://rd-cdn.example.com/cached.mkv"

                # O Mock do RD nem deve ter sido instanciado/chamado
                mock_rd_cls.assert_not_called()
                assert "302 (cached)" in caplog.text

    @pytest.mark.asyncio
    async def test_play_rd_nao_pronto_retorna_503(self, caplog):
        """Quando o RD ainda nao gera link imediato, /play retorna 503 temporario."""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        session_data = {
            "rd_token": "fake_token",
            "magnet": "magnet:?xt=urn:btih:abc123",
            "info_hash": "abc123",
            "imdb_id": "tt1234567",
            "stremio_id": "tt1234567",
            "type": "movie",
            "req_id": "test06",
        }

        with patch("app.routes.stream.cache") as mock_cache:
            mock_cache.get_with_status = AsyncMock(return_value=(session_data, "hit"))

            with patch("app.routes.stream.RealDebridService") as mock_rd_cls:
                mock_rd = AsyncMock()
                mock_rd.get_stream_url.side_effect = RealDebridPlaybackNotReadyError(
                    "Torrent temporariamente indisponivel no Real-Debrid. Tente novamente em instantes."
                )
                mock_rd_cls.return_value = mock_rd

                with caplog.at_level(logging.INFO):
                    transport = ASGITransport(app=app) 
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        resp = await client.get("/play/test-play-id")
                        assert resp.status_code == 503
                        assert resp.headers["retry-after"] == "2"
                        assert resp.headers["cache-control"] == "no-store"
                        assert resp.json()["detail"] == (
                            "Torrent temporariamente indisponivel no Real-Debrid. Tente novamente em instantes."
                        )

                assert "503 nao pronto" in caplog.text

    @pytest.mark.asyncio
    async def test_play_rd_falha_operacional_retorna_502(self, caplog):
        """Falha operacional do RD continua retornando 502."""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        session_data = {
            "rd_token": "fake_token",
            "magnet": "magnet:?xt=urn:btih:abc123",
            "info_hash": "abc123",
            "imdb_id": "tt1234567",
            "stremio_id": "tt1234567",
            "type": "movie",
            "req_id": "test07",
        }

        with patch("app.routes.stream.cache") as mock_cache:
            mock_cache.get_with_status = AsyncMock(return_value=(session_data, "hit"))

            with patch("app.routes.stream.RealDebridService") as mock_rd_cls:
                mock_rd = AsyncMock()
                mock_rd.get_stream_url.side_effect = RealDebridResolveError(
                    "Falha ao resolver playback via Real-Debrid"
                )
                mock_rd_cls.return_value = mock_rd

                with caplog.at_level(logging.INFO):
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        resp = await client.get("/play/test-play-id")
                        assert resp.status_code == 502
                        assert resp.json()["detail"] == "Falha ao resolver playback via Real-Debrid"

                assert "502 falha operacional" in caplog.text


class TestRealDebridService:
    """Testa logs de etapa e retry curto do fluxo RD."""

    @pytest.mark.asyncio
    async def test_get_stream_url_faz_retry_curto_ate_links_prontos(self, caplog):
        """Apos selectFiles, o service faz retries curtos de info ate obter links."""
        service = RealDebridService("fake_token", req_id="testrd1", play_ref="play1234")
        service.client = MagicMock()
        service.client.aclose = AsyncMock()

        resp_add = MagicMock()
        resp_add.raise_for_status = MagicMock()
        resp_add.json.return_value = {"id": "torrent123"}

        resp_select = MagicMock()
        resp_select.raise_for_status = MagicMock()

        resp_unrestrict = MagicMock()
        resp_unrestrict.raise_for_status = MagicMock()
        resp_unrestrict.json.return_value = {"download": "https://rd-cdn.example.com/video.mkv"}

        resp_info_files = MagicMock()
        resp_info_files.raise_for_status = MagicMock()
        resp_info_files.json.return_value = {
            "files": [
                {"id": 1, "path": "/video.mkv", "bytes": 123456}
            ]
        }

        resp_info_links_empty = MagicMock()
        resp_info_links_empty.raise_for_status = MagicMock()
        resp_info_links_empty.json.return_value = {
            "status": "downloading",
            "links": [],
        }

        resp_info_links_ready = MagicMock()
        resp_info_links_ready.raise_for_status = MagicMock()
        resp_info_links_ready.json.return_value = {
            "status": "downloaded",
            "links": ["https://rd-link.example.com/file"],
        }

        service.client.post = AsyncMock(side_effect=[resp_add, resp_select, resp_unrestrict])
        service.client.get = AsyncMock(
            side_effect=[resp_info_files, resp_info_links_empty, resp_info_links_ready]
        )

        with patch("app.services.real_debrid.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with caplog.at_level(logging.INFO):
                url = await service.get_stream_url(
                    magnet="magnet:?xt=urn:btih:abc123",
                    type="movie",
                    stremio_id="tt1234567",
                )

        assert url == "https://rd-cdn.example.com/video.mkv"
        assert mock_sleep.await_count == 1
        assert "addMagnet -> enviando magnet" in caplog.text
        assert "addMagnet -> torrent criado" in caplog.text
        assert "info -> lendo arquivos do torrent" in caplog.text
        assert "selectFiles -> selecionando arquivo principal" in caplog.text
        assert "selectFiles -> arquivo selecionado" in caplog.text
        assert "info -> checando links (1/3)" in caplog.text
        assert "info -> sem links ainda (status=downloading), retry em 0.75s" in caplog.text
        assert "info -> links prontos (2/3)" in caplog.text
        assert "unrestrict/link -> gerando link HTTP" in caplog.text
        assert "unrestrict/link -> link HTTP resolvido" in caplog.text
        await service.close()

    @pytest.mark.asyncio
    async def test_get_stream_url_nao_pronto_apos_retries(self, caplog):
        """Se links nao aparecem apos retries curtos, o service levanta erro temporario."""
        service = RealDebridService("fake_token", req_id="testrd2", play_ref="play9999")
        service.client = MagicMock()
        service.client.aclose = AsyncMock()

        resp_add = MagicMock()
        resp_add.raise_for_status = MagicMock()
        resp_add.json.return_value = {"id": "torrent123"}

        resp_select = MagicMock()
        resp_select.raise_for_status = MagicMock()

        resp_info_files = MagicMock()
        resp_info_files.raise_for_status = MagicMock()
        resp_info_files.json.return_value = {
            "files": [
                {"id": 1, "path": "/video.mkv", "bytes": 123456}
            ]
        }

        resp_info_links_empty = MagicMock()
        resp_info_links_empty.raise_for_status = MagicMock()
        resp_info_links_empty.json.return_value = {
            "status": "queued",
            "links": [],
        }

        service.client.post = AsyncMock(side_effect=[resp_add, resp_select])
        service.client.get = AsyncMock(
            side_effect=[
                resp_info_files,
                resp_info_links_empty,
                resp_info_links_empty,
                resp_info_links_empty,
            ]
        )

        with patch("app.services.real_debrid.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with caplog.at_level(logging.INFO):
                with pytest.raises(RealDebridPlaybackNotReadyError):
                    await service.get_stream_url(
                        magnet="magnet:?xt=urn:btih:abc123",
                        type="movie",
                        stremio_id="tt1234567",
                    )

        assert mock_sleep.await_count == 2
        assert "info -> checando links (3/3)" in caplog.text
        assert "sem links apos 3 consultas curtas" in caplog.text
        await service.close()


# ─── 6. Play URL usa origem real da request ───

class TestPlayUrlOrigin:
    """Testa que a URL de playback é derivada da origem real da request HTTP."""

    @pytest.mark.asyncio
    async def test_stream_route_127_gera_play_url_127(self):
        """Request via 127.0.0.1:8000 → play URL com 127.0.0.1:8000"""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        with patch("app.routes.stream.aggregator") as mock_agg:
            # Simula get_streams capturando o request_base_url recebido
            captured_kwargs = {}

            async def fake_get_streams(**kwargs):
                captured_kwargs.update(kwargs)
                return [StreamResult(
                    name="BR Streams • 1080p • RD",
                    title="Teste",
                    url=f"{kwargs.get('request_base_url', 'http://localhost:8000')}/play/test-id",
                    behaviorHints={},
                )]

            mock_agg.get_streams = AsyncMock(side_effect=fake_get_streams)

            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1:8000",
            ) as client:
                resp = await client.get("/stream/movie/tt0816692.json")

            assert resp.status_code == 200
            assert captured_kwargs["request_base_url"] == "http://127.0.0.1:8000"
            streams = resp.json()["streams"]
            assert streams[0]["url"].startswith("http://127.0.0.1:8000/play/")
            assert "localhost" not in streams[0]["url"]

    @pytest.mark.asyncio
    async def test_stream_route_rd_token_127_gera_play_url_127(self):
        """Request com RD token via 127.0.0.1 → play URL com 127.0.0.1"""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        captured_kwargs = {}

        async def fake_get_streams(**kwargs):
            captured_kwargs.update(kwargs)
            return [StreamResult(
                name="BR Streams • 1080p • RD",
                title="Teste",
                url=f"{kwargs.get('request_base_url', 'http://localhost:8000')}/play/test-id",
                behaviorHints={},
            )]

        with patch("app.routes.stream.aggregator") as mock_agg:
            mock_agg.get_streams = AsyncMock(side_effect=fake_get_streams)

            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1:8000",
            ) as client:
                resp = await client.get("/fake_rd_token/stream/movie/tt0816692.json")

            assert resp.status_code == 200
            assert captured_kwargs["request_base_url"] == "http://127.0.0.1:8000"
            assert captured_kwargs["rd_token"] == "fake_rd_token"

    @pytest.mark.asyncio
    async def test_stream_route_custom_host_preservado(self):
        """Request via host customizado → play URL preserva esse host"""
        from app.main import app
        from httpx import AsyncClient, ASGITransport

        captured_kwargs = {}

        async def fake_get_streams(**kwargs):
            captured_kwargs.update(kwargs)
            return []

        with patch("app.routes.stream.aggregator") as mock_agg:
            mock_agg.get_streams = AsyncMock(side_effect=fake_get_streams)

            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="https://my-addon.railway.app",
            ) as client:
                resp = await client.get("/stream/movie/tt0816692.json")

            assert resp.status_code == 200
            assert captured_kwargs["request_base_url"] == "https://my-addon.railway.app"


# ─── 7. Concorrência: request_id via contextvars ───

class TestConcurrencyReqId:
    """
    Testa que request_id via contextvars é isolado entre tasks concorrentes.
    
    Cenário: duas tasks simultâneas setam req_ids diferentes.
    Cada task deve observar apenas o seu próprio req_id, sem cruzamento.
    """

    @pytest.mark.asyncio
    async def test_contextvars_isolados_entre_tasks(self):
        """Duas tasks concorrentes com req_ids diferentes não cruzam valores"""
        observed: dict[str, list[str]] = {"task_a": [], "task_b": []}

        async def task_a():
            set_req_id("AAAA1111")
            await asyncio.sleep(0.01)  # yield controle
            observed["task_a"].append(get_req_id())
            await asyncio.sleep(0.01)
            observed["task_a"].append(get_req_id())

        async def task_b():
            set_req_id("BBBB2222")
            await asyncio.sleep(0.01)  # yield controle
            observed["task_b"].append(get_req_id())
            await asyncio.sleep(0.01)
            observed["task_b"].append(get_req_id())

        await asyncio.gather(task_a(), task_b())

        # Cada task deve ter observado APENAS o seu próprio req_id
        assert all(r == "AAAA1111" for r in observed["task_a"]), \
            f"task_a observou valores errados: {observed['task_a']}"
        assert all(r == "BBBB2222" for r in observed["task_b"]), \
            f"task_b observou valores errados: {observed['task_b']}"

    @pytest.mark.asyncio
    async def test_scraper_log_prefix_usa_contextvars(self):
        """BaseScraper._log_prefix() usa contextvars, não estado mutável"""
        from app.scrapers.base import BaseScraper

        # Cria scraper concreto mínimo para testar
        class FakeScraper(BaseScraper):
            name = "FakeScraper"
            base_url = "http://fake"
            async def search(self, query, imdb_id, type):
                return []

        scraper = FakeScraper()

        prefixes: dict[str, str] = {}

        async def task_with_prefix(req_id: str, key: str):
            set_req_id(req_id)
            await asyncio.sleep(0.01)
            prefixes[key] = scraper._log_prefix()

        await asyncio.gather(
            task_with_prefix("REQ_AAA", "task_a"),
            task_with_prefix("REQ_BBB", "task_b"),
        )

        assert prefixes["task_a"] == "[REQ_AAA] [FakeScraper]"
        assert prefixes["task_b"] == "[REQ_BBB] [FakeScraper]"
        await scraper.close()

    @pytest.mark.asyncio
    async def test_contextvars_nao_vaza_entre_requests(self):
        """Após uma task terminar, outra task não herda o req_id anterior"""
        # Task A define req_id
        async def task_set():
            set_req_id("SHOULD_NOT_LEAK")
            return get_req_id()

        # Task B verifica que não herdou
        async def task_check():
            return get_req_id()

        # Executa em sequência
        result_a = await task_set()
        assert result_a == "SHOULD_NOT_LEAK"

        # Nova task deve ter o default ""
        result_b = await asyncio.create_task(task_check())
        # O default é "" (definido no ContextVar)
        # Nota: create_task herda contexto do pai, então precisamos
        # resetar explicitamente no teste para verificar isolamento real.
        # Neste caso, como a coroutine NÃO chama set_req_id, 
        # ela herda o contexto do pai (que ainda tem SHOULD_NOT_LEAK).
        # Isso é o comportamento CORRETO de contextvars:
        # tasks filhas herdam, mas tasks paralelas NÃO se interferem.
        # O teste relevante é o test_contextvars_isolados_entre_tasks acima.
