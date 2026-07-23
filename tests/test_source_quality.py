from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.torrent import TorrentResult
from app.scrapers.relevance import is_relevant_release, normalize_release_title
from app.scrapers.yts import YTSScraper
from app.services.stream_aggregator import StreamAggregator


def _torrent(info_hash: str, source: str, *, seeders: int | None = None) -> TorrentResult:
    return TorrentResult(
        title="Interstellar 1080p",
        info_hash=info_hash,
        magnet=f"magnet:?xt=urn:btih:{info_hash}",
        quality="1080p",
        dubbed=False,
        source=source,
        seeders=seeders,
    )


def test_normalizacao_remove_ruido_de_release():
    assert normalize_release_title("Filme.2024.1080p.WEB-DL.Dual.Audio") == ""


def test_relevancia_aceita_variacao_interstellar_interestelar():
    assert is_relevant_release(
        "Interstellar",
        "Interestelar IMAX 4K Torrent Dual Audio",
    )


def test_relevancia_rejeita_resultados_sem_relacao():
    assert not is_relevant_release("Interstellar", "Anne Frank, Minha Melhor Amiga")
    assert not is_relevant_release("Troy", "Zoey 102")
    assert not is_relevant_release("Oppenheimer", "O Homem de Seis Milhões de Dólares")


def test_deduplicacao_mescla_nomes_das_fontes():
    with patch("app.services.stream_aggregator._build_scraper_list", return_value=[]):
        aggregator = StreamAggregator()

    result = aggregator._deduplicate([
        _torrent("a" * 40, "Apache Torrent", seeders=2),
        _torrent("a" * 40, "Brazuca Torrents", seeders=8),
    ])

    assert len(result) == 1
    assert result[0].source == "Apache Torrent + Brazuca Torrents"
    assert result[0].seeders == 8


@pytest.mark.asyncio
async def test_yts_busca_por_imdb_e_descarta_imdb_divergente():
    scraper = YTSScraper()
    response = MagicMock()
    response.url = "https://yts.bz/api/v2/list_movies.json"
    response.json.return_value = {
        "data": {
            "movies": [
                {
                    "imdb_code": "tt9999999",
                    "title_long": "Filme Errado (2024)",
                    "torrents": [{"hash": "b" * 40, "quality": "1080p"}],
                },
                {
                    "imdb_code": "tt0816692",
                    "title_long": "Interstellar (2014)",
                    "torrents": [
                        {
                            "hash": "c" * 40,
                            "quality": "1080p",
                            "size": "2.0 GB",
                            "seeds": 10,
                        }
                    ],
                },
            ]
        }
    }

    with patch.object(scraper, "_get_with_fallback", AsyncMock(return_value=response)) as request:
        results = await scraper.search("Interstellar", "tt0816692", "movie")

    urls = request.await_args.args[0]
    assert all("query_term=tt0816692" in url for url in urls)
    assert len(results) == 1
    assert results[0].title.startswith("Interstellar")
    await scraper.close()
