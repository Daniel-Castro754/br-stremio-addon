from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scrapers.brazuca_addon import BrazucaAddonScraper
from app.scrapers.yts import YTSScraper
from app.services.stream_aggregator import StreamAggregator


class TestFlagUsesTextQuery:
    def test_yts_marcado_como_independente_de_query(self):
        assert YTSScraper.USES_TEXT_QUERY is False

    def test_brazuca_marcado_como_independente_de_query(self):
        assert BrazucaAddonScraper.USES_TEXT_QUERY is False


def _aggregator_vazio() -> StreamAggregator:
    with patch("app.services.stream_aggregator._build_scraper_list", return_value=[]):
        return StreamAggregator()


class TestSegundaRodadaPulaFontesIndependentes:
    """
    Antes, a segunda rodada (título original) reexecutava TODAS as fontes —
    inclusive YTS e Brazuca, que buscam só por imdb_id e ignoram o texto da
    query. Rodar essas de novo com um título diferente nunca muda o
    resultado: é rede/tempo desperdiçado garantido.
    """

    @pytest.mark.asyncio
    async def test_pula_fonte_independente_quando_flag_ativa(self):
        aggregator = _aggregator_vazio()

        fonte_independente = MagicMock()
        fonte_independente.name = "YTS"
        fonte_independente.last_error = None
        fonte_independente.USES_TEXT_QUERY = False
        fonte_independente.search = AsyncMock(return_value=[])

        fonte_dependente = MagicMock()
        fonte_dependente.name = "Apache Torrent"
        fonte_dependente.last_error = None
        fonte_dependente.USES_TEXT_QUERY = True
        fonte_dependente.search = AsyncMock(return_value=[])

        aggregator.scrapers = [fonte_independente, fonte_dependente]
        aggregator.source_health = {
            "YTS": {"consecutive_failures": 0, "skip_until": None},
            "Apache Torrent": {"consecutive_failures": 0, "skip_until": None},
        }

        await aggregator._run_scrapers(
            "Titulo Original", "tt0000000", "movie", "req", "original",
            budget=5.0, skip_query_independent=True,
        )

        fonte_independente.search.assert_not_called()
        fonte_dependente.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_primeira_rodada_roda_todo_mundo_normalmente(self):
        aggregator = _aggregator_vazio()

        fonte_independente = MagicMock()
        fonte_independente.name = "YTS"
        fonte_independente.last_error = None
        fonte_independente.USES_TEXT_QUERY = False
        fonte_independente.search = AsyncMock(return_value=[])

        aggregator.scrapers = [fonte_independente]
        aggregator.source_health = {"YTS": {"consecutive_failures": 0, "skip_until": None}}

        # skip_query_independent=False (padrão) — primeira rodada roda tudo.
        await aggregator._run_scrapers(
            "Titulo PT-BR", "tt0000000", "movie", "req", "ptbr", budget=5.0,
        )

        fonte_independente.search.assert_called_once()
