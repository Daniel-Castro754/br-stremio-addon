from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.real_debrid import RealDebridResolveError, RealDebridService


class TestFalhaInesperadaViraResolveError:
    """
    Bug: o parâmetro `type` do método sombreava o builtin type() dentro do
    escopo inteiro de get_stream_url. No handler de exceção genérica,
    `type(exc).__name__` tentava chamar a STRING `type` (ex: "movie") como
    função — TypeError: 'str' object is not callable, disparado durante o
    próprio tratamento de erro, antes de conseguir levantar o
    RealDebridResolveError esperado. Resultado: qualquer falha inesperada
    (não-HTTPStatusError) no fluxo do RD virava um crash não tratado em vez
    do 502 documentado no OPERATIONS.md.
    """

    @pytest.mark.asyncio
    async def test_resposta_sem_campo_id_vira_resolve_error_nao_crash(self):
        service = RealDebridService(api_token="token-teste")

        resposta_sem_id = MagicMock()
        resposta_sem_id.raise_for_status = MagicMock()
        resposta_sem_id.json.return_value = {}  # sem "id" -> KeyError no fluxo

        service.client.post = AsyncMock(return_value=resposta_sem_id)

        with pytest.raises(RealDebridResolveError):
            await service.get_stream_url(
                magnet="magnet:?xt=urn:btih:" + "a" * 40, type="movie"
            )

        await service.close()

    @pytest.mark.asyncio
    async def test_funciona_igual_para_type_series(self):
        """O bug dependia do valor de `type` ser usado como se fosse chamável
        — reproduz com type='series' pra garantir que não é específico de
        'movie'."""
        service = RealDebridService(api_token="token-teste")

        resposta_sem_id = MagicMock()
        resposta_sem_id.raise_for_status = MagicMock()
        resposta_sem_id.json.return_value = {}

        service.client.post = AsyncMock(return_value=resposta_sem_id)

        with pytest.raises(RealDebridResolveError):
            await service.get_stream_url(
                magnet="magnet:?xt=urn:btih:" + "a" * 40,
                type="series",
                stremio_id="tt1234567:1:5",
            )

        await service.close()
