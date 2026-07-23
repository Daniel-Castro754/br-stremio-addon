from unittest.mock import patch

from app.models.torrent import TorrentResult
from app.services.stream_aggregator import StreamAggregator, _sort_streams


def _torrent(**overrides) -> TorrentResult:
    base = dict(
        title="Filme Teste 2026",
        info_hash="a" * 40,
        magnet="magnet:?xt=urn:btih:" + "a" * 40,
        quality="1080p",
        dubbed=False,
        source="Fonte A",
        size=None,
        seeders=None,
    )
    base.update(overrides)
    return TorrentResult(**base)


class TestRankingTorrentMorto:
    """
    Antes, seeders só entrava como desempate por último — um torrent 4K
    com 0 seeders confirmados aparecia ACIMA de um 1080p saudável, mesmo
    sendo inútil pra P2P (não tem de onde baixar).
    """

    def test_4k_morto_fica_atras_de_1080p_vivo(self):
        morto_4k = _torrent(quality="4K", seeders=0, info_hash="a" * 40)
        vivo_1080p = _torrent(quality="1080p", seeders=50, info_hash="b" * 40)

        ordenado = _sort_streams([(morto_4k, False), (vivo_1080p, False)])

        assert ordenado[0][0] is vivo_1080p
        assert ordenado[1][0] is morto_4k

    def test_seeders_none_nao_e_tratado_como_morto(self):
        """seeders=None (fonte não informa) não pode ser penalizado como morto."""
        desconhecido_4k = _torrent(quality="4K", seeders=None, info_hash="a" * 40)
        vivo_1080p = _torrent(quality="1080p", seeders=50, info_hash="b" * 40)

        ordenado = _sort_streams([(desconhecido_4k, False), (vivo_1080p, False)])

        # 4K vence por qualidade — seeders=None não é "morto confirmado"
        assert ordenado[0][0] is desconhecido_4k

    def test_confirmado_em_multiplas_fontes_desempata_antes_de_seeders(self):
        cross_verificado = _torrent(
            source="Apache Torrent + Comando Filmes", seeders=10, info_hash="a" * 40
        )
        fonte_unica = _torrent(source="Apache Torrent", seeders=15, info_hash="b" * 40)

        ordenado = _sort_streams([(fonte_unica, False), (cross_verificado, False)])

        assert ordenado[0][0] is cross_verificado


class TestDeduplicacaoReconciliaMetadados:
    def _aggregator(self) -> StreamAggregator:
        with patch(
            "app.services.stream_aggregator._build_scraper_list", return_value=[]
        ):
            return StreamAggregator()

    def test_qualidade_desconhecida_e_substituida_por_qualidade_identificada(self):
        aggregator = self._aggregator()
        primeiro = _torrent(quality="Desconhecida", source="Brazuca Torrents")
        segundo = _torrent(quality="1080p", source="Apache Torrent")

        resultado = aggregator._deduplicate([primeiro, segundo])

        assert len(resultado) == 1
        assert resultado[0].quality == "1080p"

    def test_dublado_e_aditivo_entre_fontes(self):
        aggregator = self._aggregator()
        primeiro = _torrent(dubbed=False, source="Brazuca Torrents")
        segundo = _torrent(dubbed=True, source="Apache Torrent")

        resultado = aggregator._deduplicate([primeiro, segundo])

        assert resultado[0].dubbed is True

    def test_titulo_mais_descritivo_prevalece(self):
        aggregator = self._aggregator()
        curto = _torrent(title="Filme", source="Brazuca Torrents")
        descritivo = _torrent(
            title="Filme.Teste.2026.1080p.WEB-DL.Dual.Audio-GRUPO",
            source="Apache Torrent",
        )

        resultado = aggregator._deduplicate([curto, descritivo])

        assert resultado[0].title == descritivo.title

    def test_titulo_so_troca_com_diferenca_significativa(self):
        aggregator = self._aggregator()
        primeiro = _torrent(title="Filme Teste 2026 1080p", source="Brazuca Torrents")
        segundo = _torrent(title="Filme Teste 2026 1080p ", source="Apache Torrent")

        resultado = aggregator._deduplicate([primeiro, segundo])

        assert resultado[0].title == primeiro.title
