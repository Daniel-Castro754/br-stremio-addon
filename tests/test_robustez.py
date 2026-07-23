import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper
from app.services.stream_aggregator import StreamAggregator


class _DummyScraper(BaseScraper):
    name = "Dummy"
    base_url = "http://dummy"

    async def search(self, query, imdb_id, type, season=None, episode=None):
        return []


def _ok_response() -> MagicMock:
    resposta = MagicMock(status_code=200)
    resposta.raise_for_status = MagicMock()
    return resposta


class TestRetryTransitorio:
    """
    Antes, qualquer timeout/erro de conexão/5xx desistia na primeira falha.
    Sites instáveis (comum nesses mirrors) perdiam resultado só por uma
    falha passageira que uma segunda tentativa resolveria.
    """

    @pytest.mark.asyncio
    async def test_retry_apos_timeout_ate_sucesso(self):
        scraper = _DummyScraper()
        ok = _ok_response()
        scraper.client.get = AsyncMock(side_effect=[httpx.TimeoutException("timeout"), ok])

        with patch("app.scrapers.base.asyncio.sleep", AsyncMock()):
            resultado = await scraper._get("http://dummy/x")

        assert resultado is ok
        assert scraper.client.get.await_count == 2
        await scraper.close()

    @pytest.mark.asyncio
    async def test_retry_apos_5xx_ate_sucesso(self):
        scraper = _DummyScraper()
        erro_503 = MagicMock(status_code=503)
        ok = _ok_response()
        scraper.client.get = AsyncMock(side_effect=[erro_503, ok])

        with patch("app.scrapers.base.asyncio.sleep", AsyncMock()):
            resultado = await scraper._get("http://dummy/x")

        assert resultado is ok
        assert scraper.client.get.await_count == 2
        await scraper.close()

    @pytest.mark.asyncio
    async def test_esgota_tentativas_retorna_none(self):
        scraper = _DummyScraper()
        scraper.client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("app.scrapers.base.asyncio.sleep", AsyncMock()):
            resultado = await scraper._get("http://dummy/x")

        assert resultado is None
        assert scraper.client.get.await_count == 2  # DEFAULT_RETRIES=1 → 2 tentativas
        await scraper.close()

    @pytest.mark.asyncio
    async def test_403_nao_tenta_de_novo(self):
        """403/429 são estados estáveis (bloqueio/limite) — retry não ajuda."""
        scraper = _DummyScraper()
        scraper.client.get = AsyncMock(return_value=MagicMock(status_code=403))

        resultado = await scraper._get("http://dummy/x")

        assert resultado is None
        assert scraper.client.get.await_count == 1
        await scraper.close()

    @pytest.mark.asyncio
    async def test_429_nao_tenta_de_novo(self):
        scraper = _DummyScraper()
        scraper.client.get = AsyncMock(return_value=MagicMock(status_code=429))

        resultado = await scraper._get("http://dummy/x")

        assert resultado is None
        assert scraper.client.get.await_count == 1
        await scraper.close()


def _health_inicial(**overrides) -> dict:
    base = {
        "status": "not_checked",
        "last_count": None,
        "last_error": None,
        "last_elapsed_ms": None,
        "last_checked_at": None,
        "consecutive_failures": 0,
        "skip_until": None,
    }
    base.update(overrides)
    return base


def _aggregator_vazio() -> StreamAggregator:
    with patch("app.services.stream_aggregator._build_scraper_list", return_value=[]):
        return StreamAggregator()


class TestCircuitBreaker:
    """
    Antes, uma fonte fora do ar consumia o timeout inteiro (até
    SCRAPER_TIMEOUT_SECONDS) em TODO request, mesmo sabendo já de
    requests anteriores que ela estava quebrada.
    """

    @pytest.mark.asyncio
    async def test_abre_circuit_breaker_apos_falhas_seguidas(self):
        aggregator = _aggregator_vazio()
        fonte = MagicMock()
        fonte.name = "Fonte Instável"
        fonte.last_error = None
        fonte.search = AsyncMock(side_effect=RuntimeError("fora do ar"))
        aggregator.scrapers = [fonte]
        aggregator.source_health = {"Fonte Instável": _health_inicial()}

        for _ in range(3):
            await aggregator._run_scrapers(
                "Query", "tt0000000", "movie", "req", "ptbr", budget=5.0
            )

        health = aggregator.source_health["Fonte Instável"]
        assert health["consecutive_failures"] == 3
        assert health["skip_until"] is not None

    @pytest.mark.asyncio
    async def test_fonte_em_cooldown_e_pulada(self):
        aggregator = _aggregator_vazio()
        fonte = MagicMock()
        fonte.name = "Fonte Instável"
        fonte.last_error = None
        fonte.search = AsyncMock(return_value=[])
        aggregator.scrapers = [fonte]
        aggregator.source_health = {
            "Fonte Instável": _health_inicial(
                consecutive_failures=3, skip_until=time.monotonic() + 120
            )
        }

        resultado = await aggregator._run_scrapers(
            "Query", "tt0000000", "movie", "req", "ptbr", budget=5.0
        )

        assert resultado == []
        fonte.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_sucesso_zera_falhas_e_fecha_circuit_breaker(self):
        aggregator = _aggregator_vazio()
        fonte = MagicMock()
        fonte.name = "Fonte Instável"
        fonte.last_error = None
        torrent = TorrentResult(
            title="Filme", info_hash="a" * 40, magnet="magnet:?xt=urn:btih:" + "a" * 40,
            quality="1080p", dubbed=False, source="Fonte Instável",
        )
        fonte.search = AsyncMock(return_value=[torrent])
        aggregator.scrapers = [fonte]
        aggregator.source_health = {
            "Fonte Instável": _health_inicial(status="error", consecutive_failures=2)
        }

        await aggregator._run_scrapers("Query", "tt0000000", "movie", "req", "ptbr", budget=5.0)

        health = aggregator.source_health["Fonte Instável"]
        assert health["consecutive_failures"] == 0
        assert health["skip_until"] is None

    @pytest.mark.asyncio
    async def test_resultado_vazio_sem_erro_nao_conta_como_falha(self):
        """Busca sem resultado (fonte respondeu bem, só não achou nada) não é falha."""
        aggregator = _aggregator_vazio()
        fonte = MagicMock()
        fonte.name = "Fonte OK"
        fonte.last_error = None
        fonte.search = AsyncMock(return_value=[])
        aggregator.scrapers = [fonte]
        aggregator.source_health = {"Fonte OK": _health_inicial(consecutive_failures=1)}

        await aggregator._run_scrapers("Query", "tt0000000", "movie", "req", "ptbr", budget=5.0)

        health = aggregator.source_health["Fonte OK"]
        assert health["consecutive_failures"] == 0
        assert health["status"] == "empty"


class TestGetSourceHealthCooldown:
    def test_expoe_segundos_restantes_em_vez_de_timestamp_monotonic(self):
        aggregator = _aggregator_vazio()
        aggregator.source_health["Apache Torrent"] = _health_inicial(
            status="error", consecutive_failures=3, skip_until=time.monotonic() + 120,
        )

        itens = aggregator.get_source_health()
        apache = next(i for i in itens if i["source"] == "Apache Torrent")

        assert apache["enabled"] is True
        assert 100 <= apache["cooldown_remaining_seconds"] <= 120
        assert "skip_until" not in apache

    def test_sem_cooldown_ativo_retorna_zero(self):
        aggregator = _aggregator_vazio()
        aggregator.source_health["Apache Torrent"] = _health_inicial(status="ok")

        itens = aggregator.get_source_health()
        apache = next(i for i in itens if i["source"] == "Apache Torrent")

        assert apache["cooldown_remaining_seconds"] == 0
