import asyncio
import logging
from urllib.parse import quote

from app.models.torrent import TorrentResult
from app.scrapers.base import BaseScraper
from app.scrapers.bencode import parse_torrent
from app.scrapers.relevance import is_relevant_release

logger = logging.getLogger(__name__)


class ArchiveOrgScraper(BaseScraper):
    """
    Busca filmes de domínio público / licença aberta no Internet Archive.

    Diferente das outras fontes, essa não depende de scraping de HTML nem
    de contornar proteção anti-bot: é a API pública e documentada do
    archive.org, e os torrents retornados são os próprios .torrent que o
    Internet Archive hospeda e semeia para cada item do acervo — não é
    conteúdo de tracker de pirataria.

    Cobertura real: o acervo do IA é forte em filmes de domínio público
    (pré-1929 nos EUA, clássicos, Prelinger Archive) e material com
    licença Creative Commons — não em lançamentos mainstream recentes.
    Por isso essa fonte deve ser vista como um complemento de catálogo,
    não substituto das demais.
    """

    name = "Internet Archive"
    base_url = "https://archive.org"
    stability = "estável"

    # Quantos itens candidatos avaliar por busca (cada um custa um download
    # extra do .torrent). Processados em paralelo (limitado por semáforo),
    # então aumentar isso não multiplica o tempo de resposta.
    MAX_CANDIDATES = 8
    MAX_CONCURRENT_TORRENT_FETCHES = 4

    async def search(
        self,
        query: str,
        imdb_id: str,
        type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[TorrentResult]:
        """Busca itens de mídia no Internet Archive. Só cobre filmes por ora —
        pacotes de série do IA não têm uma forma confiável de isolar por
        episódio sem inspecionar os arquivos internos do torrent."""
        resultados: list[TorrentResult] = []
        if type != "movie":
            return resultados

        search_query = f'title:("{query}") AND mediatype:(movies)'
        search_url = (
            f"{self.base_url}/advancedsearch.php"
            f"?q={quote(search_query)}"
            "&fl[]=identifier&fl[]=title"
            f"&rows={self.MAX_CANDIDATES}&page=1&output=json"
        )
        response = await self._get(search_url)
        if not response:
            return resultados

        try:
            data = response.json()
        except Exception as e:
            logger.error(f"[{self.name}] Erro ao parsear busca: {e}")
            return resultados

        docs = data.get("response", {}).get("docs", [])
        candidatos: list[tuple[str, str]] = []
        rejeitados = 0
        for doc in docs[: self.MAX_CANDIDATES]:
            identifier = doc.get("identifier")
            titulo = str(doc.get("title") or identifier or "").strip()
            if not identifier or not titulo:
                continue
            if not is_relevant_release(query, titulo):
                rejeitados += 1
                continue
            candidatos.append((identifier, titulo))

        # Baixa e decodifica os .torrent em paralelo (limitado por semáforo)
        # em vez de sequencial — mais candidatos sem multiplicar a latência.
        semaforo = asyncio.Semaphore(self.MAX_CONCURRENT_TORRENT_FETCHES)

        async def _extrair_com_limite(identifier: str, titulo: str) -> TorrentResult | None:
            async with semaforo:
                try:
                    return await self._extrair_torrent(identifier, titulo)
                except Exception as e:
                    logger.error(f"[{self.name}] Erro ao processar {identifier}: {e}")
                    return None

        torrents_brutos = await asyncio.gather(
            *[_extrair_com_limite(identifier, titulo) for identifier, titulo in candidatos]
        )
        resultados = [t for t in torrents_brutos if t is not None]

        logger.info(
            f"[{self.name}] Encontrados {len(resultados)} itens para '{query}' "
            f"({rejeitados} descartados por relevância)"
        )
        return resultados

    async def _extrair_torrent(self, identifier: str, titulo: str) -> TorrentResult | None:
        """Baixa o .torrent oficial do item (hospedado e semeado pelo IA) e
        monta o magnet a partir do info_hash real do arquivo."""
        torrent_url = f"{self.base_url}/download/{identifier}/{identifier}_archive.torrent"
        response = await self._get(torrent_url)
        if not response:
            return None

        try:
            top, info_hash = parse_torrent(response.content)
        except Exception as e:
            logger.warning(f"[{self.name}] Falha ao decodificar torrent de '{identifier}': {e}")
            return None

        if not info_hash:
            return None

        trackers = self._extrair_trackers(top)
        magnet = self._montar_magnet(info_hash, titulo, trackers)

        return TorrentResult(
            title=f"{titulo} [Internet Archive]",
            info_hash=info_hash,
            magnet=magnet,
            quality="Desconhecida",
            dubbed=False,
            source=self.name,
            size=None,
            seeders=None,
        )

    def _extrair_trackers(self, top: dict) -> list[str]:
        """Reaproveita os trackers já embutidos no .torrent do IA, além do DHT."""
        trackers: list[str] = []

        announce = top.get(b"announce")
        if isinstance(announce, bytes):
            trackers.append(announce.decode("utf-8", "ignore"))

        for grupo in top.get(b"announce-list") or []:
            if not isinstance(grupo, list):
                continue
            for item in grupo:
                if isinstance(item, bytes):
                    url = item.decode("utf-8", "ignore")
                    if url and url not in trackers:
                        trackers.append(url)

        return trackers

    def _montar_magnet(self, info_hash: str, titulo: str, trackers: list[str]) -> str:
        partes = [f"magnet:?xt=urn:btih:{info_hash}", f"dn={quote(titulo)}"]
        for tracker in trackers:
            partes.append(f"tr={quote(tracker, safe=':/')}")
        return "&".join(partes)
