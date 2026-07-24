from fastapi.testclient import TestClient

from app.main import app


class TestHealthEndpoint:
    """
    Bug: /health estava registrado duas vezes — uma em configure.py (sem
    telemetria de fontes) e outra em main.py (com aggregator.get_source_health()).
    Como configure_router é incluído antes das rotas do próprio app, a
    versão SEM telemetria sempre respondia primeiro, e a versão rica nunca
    era alcançada — mesmo com a rota "certa" presente no código.
    """

    def test_health_expoe_telemetria_de_fontes(self):
        client = TestClient(app)
        resposta = client.get("/health")

        assert resposta.status_code == 200
        corpo = resposta.json()
        assert corpo["status"] == "ok"
        assert "sources" in corpo
        assert isinstance(corpo["sources"], list)
        assert len(corpo["sources"]) > 0

    def test_health_tambem_expoe_versao_e_configuracao(self):
        client = TestClient(app)
        resposta = client.get("/health")

        corpo = resposta.json()
        assert "version" in corpo
        assert "storage_backend" in corpo
        assert "request_budget_seconds" in corpo
        assert "scraper_timeout_seconds" in corpo

    def test_apenas_uma_rota_health_registrada(self):
        """Trava contra o bug voltar: duas rotas com o mesmo path fazem uma
        delas ficar inalcançável silenciosamente, sem erro nenhum no startup."""
        rotas_health = [
            route for route in app.routes
            if getattr(route, "path", None) == "/health"
        ]
        assert len(rotas_health) == 1
