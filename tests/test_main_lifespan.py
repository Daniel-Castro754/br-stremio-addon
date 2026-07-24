from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, lifespan


class TestLifespan:
    """
    O lifespan nunca tinha teste — inclusive a ordem cache.init() ->
    restore_health_from_cache() no startup (que importa: restaurar antes de
    init deixaria o backend de cache não pronto pra ler nada) e
    delete_expired() -> close() no shutdown.
    """

    @pytest.mark.asyncio
    async def test_startup_e_shutdown_chamam_na_ordem_certa(self):
        chamadas: list[str] = []

        mock_cache = AsyncMock()
        mock_cache.init.side_effect = lambda: chamadas.append("cache.init")
        mock_cache.delete_expired.side_effect = lambda: chamadas.append("cache.delete_expired")
        mock_cache.close.side_effect = lambda: chamadas.append("cache.close")

        mock_aggregator = AsyncMock()
        mock_aggregator.restore_health_from_cache.side_effect = (
            lambda: chamadas.append("restore_health")
        )

        with patch("app.main.cache", mock_cache), patch("app.main.aggregator", mock_aggregator):
            async with lifespan(app):
                # dentro do "yield" — só o startup deve ter rodado até aqui
                assert chamadas == ["cache.init", "restore_health"]

        # depois de sair do context manager, o shutdown deve ter rodado
        assert chamadas == ["cache.init", "restore_health", "cache.delete_expired", "cache.close"]

    @pytest.mark.asyncio
    async def test_restore_health_so_roda_depois_do_cache_init(self):
        """Restaurar saúde antes do cache estar pronto não faria sentido —
        o backend SQLite ainda não teria conexão aberta."""
        ordem: list[str] = []

        mock_cache = AsyncMock()
        mock_cache.init.side_effect = lambda: ordem.append("init")

        mock_aggregator = AsyncMock()
        mock_aggregator.restore_health_from_cache.side_effect = lambda: ordem.append("restore")

        with patch("app.main.cache", mock_cache), patch("app.main.aggregator", mock_aggregator):
            async with lifespan(app):
                pass

        assert ordem == ["init", "restore"]


class TestManifestERaizSemLifespan:
    """
    Usa ASGITransport direto (sem disparar o lifespan_context real) — mesmo
    padrão já usado no resto da suíte, evita tocar o SQLite de verdade só
    pra testar uma rota que devolve um dict estático.
    """

    @pytest.mark.asyncio
    async def test_manifest_json(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/manifest.json")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "community.br-streams"
        assert "stream" in data["resources"]

    @pytest.mark.asyncio
    async def test_manifest_com_token_rd(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/algum-token/manifest.json")

        assert resp.status_code == 200
        assert resp.json()["id"] == "community.br-streams"

    @pytest.mark.asyncio
    async def test_manifest_hibrido_com_token(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/hybrid/algum-token/manifest.json")

        assert resp.status_code == 200
        assert resp.json()["id"] == "community.br-streams"

    @pytest.mark.asyncio
    async def test_raiz_redireciona_para_configure(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=False
        ) as client:
            resp = await client.get("/")

        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/configure"
