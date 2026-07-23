from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routes.stream import _parse_stremio_id
from app.scrapers.brazuca_addon import BrazucaAddonScraper
from app.scrapers.relevance import build_series_queries, matches_episode
from app.services.stream_aggregator import StreamAggregator


class TestParseStremioId:
    """
    Antes, todo endpoint fazia id.split(":")[0], descartando season/episode.
    Isso fazia o addon buscar torrents da série inteira, sem filtrar pelo
    episódio pedido, para qualquer episódio de qualquer série.
    """

    def test_filme_sem_season_episode(self):
        assert _parse_stremio_id("tt1234567.json") == ("tt1234567", None, None)

    def test_serie_extrai_season_e_episode(self):
        assert _parse_stremio_id("tt1234567:1:5.json") == ("tt1234567", 1, 5)

    def test_serie_temporada_e_episodio_de_dois_digitos(self):
        assert _parse_stremio_id("tt7654321:12:23.json") == ("tt7654321", 12, 23)

    def test_formato_invalido_cai_para_none(self):
        assert _parse_stremio_id("tt1234567:x:y.json") == ("tt1234567", None, None)


class TestBuildSeriesQueries:
    def test_filme_retorna_so_a_query_original(self):
        assert build_series_queries("Duna", None, None) == ["Duna"]

    def test_serie_tenta_episodio_especifico_antes_do_pacote(self):
        assert build_series_queries("Breaking Bad", 1, 5) == [
            "Breaking Bad S01E05",
            "Breaking Bad",
        ]

    def test_serie_formata_numeros_com_dois_digitos(self):
        variantes = build_series_queries("Show", 2, 3)
        assert variantes[0] == "Show S02E03"


class TestMatchesEpisode:
    def test_sem_season_episode_sempre_aceita(self):
        assert matches_episode("Qualquer Coisa 1080p", None, None)

    def test_episodio_especifico_correto_aceita(self):
        assert matches_episode("Breaking Bad S01E05 1080p Dublado", 1, 5)

    def test_episodio_especifico_errado_rejeita(self):
        assert not matches_episode("Breaking Bad S01E06 1080p Dublado", 1, 5)

    def test_temporada_diferente_rejeita(self):
        assert not matches_episode("Breaking Bad S02E05 1080p Dublado", 1, 5)

    def test_formato_1x05_reconhecido(self):
        assert matches_episode("Breaking Bad 1x05 Dublado", 1, 5)

    def test_pacote_de_temporada_completa_aceita(self):
        assert matches_episode("Breaking Bad 1a Temporada Completa Dublado", 1, 5)

    def test_pacote_de_temporada_diferente_rejeita(self):
        assert not matches_episode("Breaking Bad 2a Temporada Completa Dublado", 1, 5)

    def test_sem_marca_de_temporada_nao_rejeita(self):
        assert matches_episode("Breaking Bad Dublado 1080p", 1, 5)


class TestBrazucaUsaIdCompleto:
    """
    Brazuca é um proxy para outro addon Stremio — para séries, a origem
    precisa do id completo (imdb:season:episode) pra saber qual episódio
    retornar. Usando só o imdb_id puro, ela não tem como saber.
    """

    @pytest.mark.asyncio
    async def test_serie_usa_imdb_season_episode_na_url(self):
        scraper = BrazucaAddonScraper()
        response = MagicMock()
        response.json.return_value = {"streams": []}

        with patch.object(scraper, "_get", AsyncMock(return_value=response)) as get_mock:
            await scraper.search("Show", "tt1234567", "series", season=1, episode=5)

        url_chamada = get_mock.await_args.args[0]
        assert url_chamada.endswith("/stream/series/tt1234567:1:5.json")
        await scraper.close()

    @pytest.mark.asyncio
    async def test_filme_usa_so_imdb_id_na_url(self):
        scraper = BrazucaAddonScraper()
        response = MagicMock()
        response.json.return_value = {"streams": []}

        with patch.object(scraper, "_get", AsyncMock(return_value=response)) as get_mock:
            await scraper.search("Filme", "tt1234567", "movie")

        url_chamada = get_mock.await_args.args[0]
        assert url_chamada.endswith("/stream/movie/tt1234567.json")
        await scraper.close()


class TestCacheKeyIsoladoPorEpisodio:
    """
    Antes, a chave de cache era só streams:{versao}:{imdb_id}:{type} — todo
    episódio de uma série reaproveitava o cache buscado para qualquer outro
    episódio já consultado.
    """

    @pytest.mark.asyncio
    async def test_episodios_diferentes_usam_chaves_de_cache_diferentes(self):
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None

        with patch("app.services.stream_aggregator.cache", mock_cache):
            with patch(
                "app.services.stream_aggregator._build_scraper_list",
                return_value=[],
            ):
                aggregator = StreamAggregator()

            with patch.object(
                aggregator, "_fetch_title", new=AsyncMock(return_value=("Show", "Show"))
            ):
                with patch.object(
                    aggregator, "_run_scrapers", new=AsyncMock(return_value=[])
                ):
                    await aggregator.get_streams(
                        imdb_id="tt1234567", type="series", req_id="ep05",
                        season=1, episode=5,
                    )
                    await aggregator.get_streams(
                        imdb_id="tt1234567", type="series", req_id="ep06",
                        season=1, episode=6,
                    )

        chaves_consultadas = [call.args[0] for call in mock_cache.get.call_args_list]
        assert chaves_consultadas[0] != chaves_consultadas[1]
        assert chaves_consultadas[0].endswith(":1:5")
        assert chaves_consultadas[1].endswith(":1:6")
