import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scrapers.archive_org import ArchiveOrgScraper
from app.scrapers.bencode import parse_torrent


def _bstr(s: bytes) -> bytes:
    return str(len(s)).encode() + b":" + s


def _build_test_torrent(
    announce: bytes = b"udp://tracker.example.com:80",
    announce_list: list[list[bytes]] | None = None,
    name: bytes = b"filme.teste.mp4",
) -> tuple[bytes, bytes]:
    """Monta um .torrent bencode válido à mão e retorna (bytes, bytes_do_info)."""
    info = (
        b"d"
        + _bstr(b"length") + b"i123e"
        + _bstr(b"name") + _bstr(name)
        + _bstr(b"piece length") + b"i16384e"
        + _bstr(b"pieces") + _bstr(b"A" * 20)
        + b"e"
    )

    body = _bstr(b"announce") + _bstr(announce)

    if announce_list is not None:
        lista = b"l"
        for grupo in announce_list:
            lista += b"l" + b"".join(_bstr(u) for u in grupo) + b"e"
        lista += b"e"
        body += _bstr(b"announce-list") + lista

    body += _bstr(b"info") + info

    torrent = b"d" + body + b"e"
    return torrent, info


class TestBencodeParseTorrent:
    def test_extrai_info_hash_correto(self):
        torrent_bytes, info_bytes = _build_test_torrent()
        esperado = hashlib.sha1(info_bytes).hexdigest()

        top, info_hash = parse_torrent(torrent_bytes)

        assert info_hash == esperado
        assert top[b"announce"] == b"udp://tracker.example.com:80"

    def test_extrai_announce_list(self):
        torrent_bytes, _ = _build_test_torrent(
            announce_list=[[b"udp://tracker1.example.com:80"], [b"udp://tracker2.example.com:80"]]
        )

        top, info_hash = parse_torrent(torrent_bytes)

        assert info_hash is not None
        assert top[b"announce-list"] == [
            [b"udp://tracker1.example.com:80"],
            [b"udp://tracker2.example.com:80"],
        ]

    def test_dados_invalidos_nao_estouram_excecao_incomum(self):
        top, info_hash = parse_torrent(b"nao e bencode valido")
        assert info_hash is None
        assert top == {}

    def test_dict_sem_chave_info_retorna_hash_none(self):
        torrent = b"d" + _bstr(b"announce") + _bstr(b"udp://x") + b"e"
        top, info_hash = parse_torrent(torrent)
        assert info_hash is None
        assert top[b"announce"] == b"udp://x"


class TestArchiveOrgScraper:
    @pytest.mark.asyncio
    async def test_filme_busca_e_monta_magnet_com_trackers(self):
        scraper = ArchiveOrgScraper()
        torrent_bytes, info_bytes = _build_test_torrent(
            announce_list=[[b"udp://tracker1.example.com:80"]]
        )
        esperado_hash = hashlib.sha1(info_bytes).hexdigest()

        search_response = MagicMock()
        search_response.json.return_value = {
            "response": {
                "docs": [{"identifier": "night_of_the_living_dead_1968", "title": "Night of the Living Dead"}]
            }
        }
        torrent_response = MagicMock()
        torrent_response.content = torrent_bytes

        async def _get_side_effect(url):
            if url.endswith(".torrent"):
                return torrent_response
            return search_response

        with patch.object(scraper, "_get", AsyncMock(side_effect=_get_side_effect)):
            resultados = await scraper.search(
                "Night of the Living Dead", "tt0063350", "movie"
            )

        assert len(resultados) == 1
        torrent = resultados[0]
        assert torrent.info_hash == esperado_hash
        assert torrent.source == "Internet Archive"
        assert f"urn:btih:{esperado_hash}" in torrent.magnet
        assert "tr=udp" in torrent.magnet
        await scraper.close()

    @pytest.mark.asyncio
    async def test_serie_retorna_vazio(self):
        """IA não separa por episódio de forma confiável — escopo só filme por ora."""
        scraper = ArchiveOrgScraper()
        resultados = await scraper.search("Show", "tt1234567", "series", season=1, episode=2)
        assert resultados == []
        await scraper.close()

    @pytest.mark.asyncio
    async def test_titulo_irrelevante_e_descartado(self):
        scraper = ArchiveOrgScraper()
        search_response = MagicMock()
        search_response.json.return_value = {
            "response": {"docs": [{"identifier": "algo_sem_relacao", "title": "Completamente Sem Relação"}]}
        }

        with patch.object(scraper, "_get", AsyncMock(return_value=search_response)) as get_mock:
            resultados = await scraper.search("Interstellar", "tt0816692", "movie")

        assert resultados == []
        # Só chamou a busca — não tentou baixar torrent de um item irrelevante.
        assert get_mock.await_count == 1
        await scraper.close()

    @pytest.mark.asyncio
    async def test_busca_sem_resposta_retorna_vazio(self):
        scraper = ArchiveOrgScraper()
        with patch.object(scraper, "_get", AsyncMock(return_value=None)):
            resultados = await scraper.search("Qualquer", "tt0000000", "movie")
        assert resultados == []
        await scraper.close()
