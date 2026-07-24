from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.config import settings
from app.services.stream_aggregator import StreamAggregator


def _aggregator() -> StreamAggregator:
    with patch("app.services.stream_aggregator._build_scraper_list", return_value=[]):
        return StreamAggregator()


def _client_mock(respostas: list) -> MagicMock:
    """Monta um patch de httpx.AsyncClient cujo .get() devolve `respostas` em sequência."""
    mock_client_cls = MagicMock()
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=respostas)
    return mock_client_cls


def _resp(status_code: int, json_data: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data
    return r


class TestFetchTitleTMDB:
    @pytest.mark.asyncio
    async def test_usa_tmdb_quando_api_key_configurada(self):
        agg = _aggregator()
        cinemeta = _resp(200, {"meta": {"name": "Interstellar"}})
        tmdb = _resp(200, {"movie_results": [{"title": "Interestelar"}]})

        with patch.object(settings, "TMDB_API_KEY", "chave-teste"):
            with patch("httpx.AsyncClient", _client_mock([cinemeta, tmdb])):
                original, ptbr = await agg._fetch_title(
                    imdb_id="tt0816692", type="movie", req_id="t1", budget=5.0
                )

        assert original == "Interstellar"
        assert ptbr == "Interestelar"

    @pytest.mark.asyncio
    async def test_tmdb_401_ignora_e_mantem_cinemeta(self):
        agg = _aggregator()
        cinemeta = _resp(200, {"meta": {"name": "Interstellar"}})
        tmdb_invalido = _resp(401, {"status_message": "Invalid API key"})
        omdb = _resp(200, {"Title": "Interstellar"})

        with patch.object(settings, "TMDB_API_KEY", "chave-invalida"):
            with patch("httpx.AsyncClient", _client_mock([cinemeta, tmdb_invalido, omdb])):
                original, ptbr = await agg._fetch_title(
                    imdb_id="tt0816692", type="movie", req_id="t2", budget=5.0
                )

        assert original == "Interstellar"
        assert ptbr == "Interstellar"

    @pytest.mark.asyncio
    async def test_tmdb_lanca_excecao_e_segue_o_fluxo(self):
        agg = _aggregator()
        cinemeta = _resp(200, {"meta": {"name": "Interstellar"}})
        omdb = _resp(200, {"Title": "Interstellar"})

        mock_client_cls = MagicMock()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        chamadas = {"n": 0}

        async def get_sequencial(url, *args, **kwargs):
            chamadas["n"] += 1
            if chamadas["n"] == 1:
                return cinemeta
            if chamadas["n"] == 2:
                raise ConnectionError("TMDB fora do ar")
            return omdb

        mock_client.get = AsyncMock(side_effect=get_sequencial)

        with patch.object(settings, "TMDB_API_KEY", "chave-teste"):
            with patch("httpx.AsyncClient", mock_client_cls):
                original, ptbr = await agg._fetch_title(
                    imdb_id="tt0816692", type="movie", req_id="t3", budget=5.0
                )

        # TMDB falhou, mas Cinemeta já tinha dado um título válido
        assert original == "Interstellar"
        assert ptbr == "Interstellar"


class TestFetchTitleOMDBFallback:
    @pytest.mark.asyncio
    async def test_omdb_usado_quando_cinemeta_nao_retorna_nome(self):
        agg = _aggregator()
        cinemeta_vazio = _resp(200, {"meta": {}})
        omdb = _resp(200, {"Title": "Interstellar"})

        # Loop do Cinemeta tenta [type, "movie", "series"] — com type="movie"
        # isso vira 3 chamadas (movie, movie, series) antes de desistir e
        # cair pro OMDB como a 4a chamada.
        respostas = [cinemeta_vazio, cinemeta_vazio, cinemeta_vazio, omdb]

        with patch("httpx.AsyncClient", _client_mock(respostas)):
            original, ptbr = await agg._fetch_title(
                imdb_id="tt0816692", type="movie", req_id="t4", budget=5.0
            )

        assert original == "Interstellar"
        assert ptbr == "Interstellar"

    @pytest.mark.asyncio
    async def test_omdb_lanca_excecao_mantem_fallback_imdb_id(self):
        agg = _aggregator()
        cinemeta_vazio = _resp(200, {"meta": {}})

        async def get_sequencial(url, *args, **kwargs):
            if "omdbapi" in url:
                raise ConnectionError("OMDB fora do ar")
            return cinemeta_vazio

        mock_client_cls = MagicMock()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=get_sequencial)

        with patch("httpx.AsyncClient", mock_client_cls):
            original, ptbr = await agg._fetch_title(
                imdb_id="tt0816692", type="movie", req_id="t5", budget=5.0
            )

        assert original == "tt0816692"
        assert ptbr == "tt0816692"


class TestFetchTitleErroGeral:
    @pytest.mark.asyncio
    async def test_erro_ao_criar_client_cai_no_fallback(self):
        agg = _aggregator()

        with patch("httpx.AsyncClient", side_effect=RuntimeError("boom")):
            original, ptbr = await agg._fetch_title(
                imdb_id="tt0816692", type="movie", req_id="t6", budget=5.0
            )

        assert original == "tt0816692"
        assert ptbr == "tt0816692"

    @pytest.mark.asyncio
    async def test_nunca_retorna_imdb_id_como_ptbr_se_tem_titulo_melhor(self):
        """Cinemeta falha (sem nome), mas OMDB só preenche titulo_original
        (por regra, não titulo_ptbr) — a salvaguarda final deve copiar
        titulo_original pra titulo_ptbr em vez de deixar o imdb_id cru."""
        agg = _aggregator()
        cinemeta_vazio = _resp(200, {"meta": {}})
        omdb = _resp(200, {"Title": "Interstellar"})
        respostas = [cinemeta_vazio, cinemeta_vazio, cinemeta_vazio, omdb]

        with patch("httpx.AsyncClient", _client_mock(respostas)):
            original, ptbr = await agg._fetch_title(
                imdb_id="tt0816692", type="movie", req_id="t7", budget=5.0
            )

        assert ptbr != "tt0816692"
        assert ptbr == original == "Interstellar"
