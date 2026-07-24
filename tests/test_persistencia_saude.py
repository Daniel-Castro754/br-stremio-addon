import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services.stream_aggregator import HEALTH_CACHE_KEY, StreamAggregator


def _aggregator_vazio() -> StreamAggregator:
    with patch("app.services.stream_aggregator._build_scraper_list", return_value=[]):
        return StreamAggregator()


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


class TestPersistenciaDeSaude:
    """
    Antes, consecutive_failures/skip_until/status viviam só em memória —
    um restart do container (comum em Railway/Render) zerava tudo, incluindo
    o circuit breaker de uma fonte que a gente tinha acabado de descobrir
    que estava quebrada.
    """

    @pytest.mark.asyncio
    async def test_persiste_convertendo_monotonic_para_wall_clock(self):
        aggregator = _aggregator_vazio()
        aggregator.source_health = {
            "Fonte X": _health_inicial(
                consecutive_failures=3, skip_until=time.monotonic() + 120
            )
        }

        mock_cache = AsyncMock()
        with patch("app.services.stream_aggregator.cache", mock_cache):
            await aggregator._persist_health()

        chave, snapshot = mock_cache.set.call_args.args[:2]
        assert chave == HEALTH_CACHE_KEY
        entry = snapshot["Fonte X"]
        assert "skip_until" not in entry  # não persiste o monotonic bruto
        assert entry["skip_until_wall"] is not None
        assert 100 <= entry["skip_until_wall"] - time.time() <= 120
        assert entry["consecutive_failures"] == 3

    @pytest.mark.asyncio
    async def test_restaura_cooldown_ainda_ativo_com_novo_monotonic(self):
        aggregator = _aggregator_vazio()
        aggregator.source_health = {"Fonte X": _health_inicial()}

        snapshot_salvo = {
            "Fonte X": {
                "status": "error",
                "last_count": 0,
                "last_error": "boom",
                "last_elapsed_ms": 100,
                "last_checked_at": time.time(),
                "consecutive_failures": 3,
                "skip_until_wall": time.time() + 60,  # ainda tem 60s de cooldown
            }
        }
        mock_cache = AsyncMock()
        mock_cache.get.return_value = snapshot_salvo

        with patch("app.services.stream_aggregator.cache", mock_cache):
            await aggregator.restore_health_from_cache()

        health = aggregator.source_health["Fonte X"]
        assert health["consecutive_failures"] == 3
        assert health["skip_until"] is not None
        # skip_until restaurado é um monotonic NOVO, ancorado no processo atual
        assert health["skip_until"] > time.monotonic()

    @pytest.mark.asyncio
    async def test_cooldown_ja_expirado_durante_o_restart_nao_e_restaurado(self):
        aggregator = _aggregator_vazio()
        aggregator.source_health = {"Fonte X": _health_inicial()}

        snapshot_salvo = {
            "Fonte X": {
                "status": "error",
                "consecutive_failures": 3,
                "skip_until_wall": time.time() - 30,  # expirou há 30s
            }
        }
        mock_cache = AsyncMock()
        mock_cache.get.return_value = snapshot_salvo

        with patch("app.services.stream_aggregator.cache", mock_cache):
            await aggregator.restore_health_from_cache()

        # Passou por baixo do cooldown enquanto o container tava fora do ar —
        # não faz sentido pular a fonte de novo sem tentar.
        assert aggregator.source_health["Fonte X"]["skip_until"] is None

    @pytest.mark.asyncio
    async def test_scraper_desconhecido_no_snapshot_e_ignorado(self):
        """Fonte que existia num deploy anterior mas não existe mais não deve quebrar nada."""
        aggregator = _aggregator_vazio()
        aggregator.source_health = {"Fonte Atual": _health_inicial()}

        mock_cache = AsyncMock()
        mock_cache.get.return_value = {"Fonte Que Não Existe Mais": {"consecutive_failures": 5}}

        with patch("app.services.stream_aggregator.cache", mock_cache):
            await aggregator.restore_health_from_cache()

        assert aggregator.source_health["Fonte Atual"]["consecutive_failures"] == 0
        assert "Fonte Que Não Existe Mais" not in aggregator.source_health

    @pytest.mark.asyncio
    async def test_cache_vazio_nao_quebra_nada(self):
        aggregator = _aggregator_vazio()
        aggregator.source_health = {"Fonte X": _health_inicial()}

        mock_cache = AsyncMock()
        mock_cache.get.return_value = None

        with patch("app.services.stream_aggregator.cache", mock_cache):
            await aggregator.restore_health_from_cache()

        assert aggregator.source_health["Fonte X"]["consecutive_failures"] == 0

    @pytest.mark.asyncio
    async def test_falha_ao_persistir_nao_propaga_excecao(self):
        aggregator = _aggregator_vazio()
        aggregator.source_health = {"Fonte X": _health_inicial()}

        mock_cache = AsyncMock()
        mock_cache.set.side_effect = RuntimeError("cache fora do ar")

        with patch("app.services.stream_aggregator.cache", mock_cache):
            await aggregator._persist_health()  # não deve levantar exceção
