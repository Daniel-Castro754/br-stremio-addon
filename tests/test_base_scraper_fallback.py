from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.scrapers.base import BaseScraper


class _DummyScraper(BaseScraper):
    name = "Dummy"
    base_url = "http://dummy-original.com"

    async def search(self, query, imdb_id, type, season=None, episode=None):
        return []


def _ok_response(url: str) -> MagicMock:
    resposta = MagicMock(status_code=200)
    resposta.raise_for_status = MagicMock()
    resposta.url = url
    return resposta


class TestGetComRetryEsgotado:
    @pytest.mark.asyncio
    async def test_5xx_persistente_esgota_tentativas_e_retorna_none(self):
        scraper = _DummyScraper()
        scraper.client.get = AsyncMock(return_value=MagicMock(status_code=503))

        with patch("app.scrapers.base.asyncio.sleep", AsyncMock()):
            resultado = await scraper._get("http://dummy/x")

        assert resultado is None
        assert scraper.client.get.await_count == 2  # DEFAULT_RETRIES=1 -> 2 tentativas
        assert "503" in scraper.last_error
        await scraper.close()


class TestGetWithFallback:
    @pytest.mark.asyncio
    async def test_primeira_url_funciona_direto(self):
        scraper = _DummyScraper()
        ok = _ok_response("https://mirror1.com/pagina")
        scraper.client.get = AsyncMock(return_value=ok)

        resultado = await scraper._get_with_fallback(
            ["https://mirror1.com/pagina", "https://mirror2.com/pagina"]
        )

        assert resultado is ok
        assert scraper.client.get.await_count == 1
        await scraper.close()

    @pytest.mark.asyncio
    async def test_primeira_falha_cai_para_segunda_url(self):
        scraper = _DummyScraper()
        ok = _ok_response("https://mirror2.com/pagina")
        scraper.client.get = AsyncMock(side_effect=[httpx.ConnectError("recusado"), ok])

        resultado = await scraper._get_with_fallback(
            ["https://mirror1.com/pagina", "https://mirror2.com/pagina"]
        )

        assert resultado is ok
        assert scraper.client.get.await_count == 2
        await scraper.close()

    @pytest.mark.asyncio
    async def test_atualiza_base_url_quando_mirror_diferente_responde(self):
        """Quando um mirror diferente do base_url original responde, o
        scraper deve passar a usar esse domínio nas próximas buscas."""
        scraper = _DummyScraper()
        ok = _ok_response("https://mirror-novo.com/pagina?s=x")
        scraper.client.get = AsyncMock(return_value=ok)

        await scraper._get_with_fallback(["https://mirror-novo.com/pagina?s=x"])

        assert scraper.base_url == "https://mirror-novo.com"
        await scraper.close()

    @pytest.mark.asyncio
    async def test_todas_as_urls_falham_retorna_none(self):
        scraper = _DummyScraper()
        scraper.client.get = AsyncMock(
            side_effect=[
                httpx.ConnectError("recusado"),
                httpx.TimeoutException("timeout"),
            ]
        )

        resultado = await scraper._get_with_fallback(
            ["https://mirror1.com", "https://mirror2.com"]
        )

        assert resultado is None
        await scraper.close()

    @pytest.mark.asyncio
    async def test_httpstatuserror_tambem_aciona_fallback(self):
        scraper = _DummyScraper()
        request = httpx.Request("GET", "https://mirror1.com")
        response_404 = httpx.Response(status_code=404, request=request)
        resposta_com_erro = MagicMock()
        resposta_com_erro.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found", request=request, response=response_404
        )
        ok = _ok_response("https://mirror2.com")

        scraper.client.get = AsyncMock(side_effect=[resposta_com_erro, ok])

        resultado = await scraper._get_with_fallback(
            ["https://mirror1.com", "https://mirror2.com"]
        )

        assert resultado is ok
        await scraper.close()

    @pytest.mark.asyncio
    async def test_erro_inesperado_tambem_e_capturado_e_continua(self):
        scraper = _DummyScraper()
        ok = _ok_response("https://mirror2.com")
        scraper.client.get = AsyncMock(side_effect=[ValueError("algo estranho"), ok])

        resultado = await scraper._get_with_fallback(
            ["https://mirror1.com", "https://mirror2.com"]
        )

        assert resultado is ok
        await scraper.close()

    @pytest.mark.asyncio
    async def test_lista_vazia_retorna_none(self):
        scraper = _DummyScraper()
        resultado = await scraper._get_with_fallback([])
        assert resultado is None
        await scraper.close()
