import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

LINK_READY_RETRY_DELAYS: tuple[float, ...] = (0.75, 0.75)


def _summarize_http_error(exc: httpx.HTTPStatusError) -> str:
    """Resume erro HTTP sem expor URLs sensiveis ou payloads."""
    response = exc.response
    request = response.request
    return f"HTTP {response.status_code} em {request.method}"


class RealDebridError(Exception):
    """Erro base do fluxo de resolucao via Real-Debrid."""


class RealDebridPlaybackNotReadyError(RealDebridError):
    """Torrent ainda nao esta pronto para playback imediato."""


class RealDebridResolveError(RealDebridError):
    """Falha operacional ao resolver um link via Real-Debrid."""


class RealDebridService:
    """Cliente para a API do Real-Debrid."""

    def __init__(
        self,
        api_token: str,
        req_id: str | None = None,
        play_ref: str | None = None,
    ) -> None:
        self.base_url = "https://api.real-debrid.com/rest/1.0"
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=15.0,
        )
        prefix = []
        if req_id:
            prefix.append(f"[{req_id}]")
        prefix.append("[PLAY]")
        prefix.append(f"[RD {play_ref}]" if play_ref else "[RD]")
        self.log_prefix = " ".join(prefix)

    def _log(self, stage: str, message: str, level: int = logging.INFO) -> None:
        """Centraliza logs curtos do fluxo RD sem expor token ou URL final."""
        logger.log(level, f"{self.log_prefix} {stage} -> {message}")

    async def _get_torrent_info(self, torrent_id: str, purpose: str) -> dict:
        """Lê o estado atual do torrent no RD."""
        self._log("info", purpose)
        resp_info = await self.client.get(f"{self.base_url}/torrents/info/{torrent_id}")
        resp_info.raise_for_status()
        return resp_info.json()

    async def _wait_for_links(self, torrent_id: str) -> list[str]:
        """
        Faz retries curtos e controlados apos selectFiles.

        Motivo operacional:
          O RD pode levar alguns instantes para popular `links` logo apos o
          `selectFiles`. Um retry curto reduz falso negativo imediato sem
          transformar /play em polling longo.
        """
        total_attempts = 1 + len(LINK_READY_RETRY_DELAYS)
        for attempt in range(1, total_attempts + 1):
            torrent_info = await self._get_torrent_info(
                torrent_id,
                f"checando links ({attempt}/{total_attempts})",
            )
            links = torrent_info.get("links", [])
            if links:
                self._log("info", f"links prontos ({attempt}/{total_attempts})")
                return links

            status = torrent_info.get("status", "desconhecido")
            if attempt < total_attempts:
                delay = LINK_READY_RETRY_DELAYS[attempt - 1]
                self._log(
                    "info",
                    f"sem links ainda (status={status}), retry em {delay:.2f}s",
                )
                await asyncio.sleep(delay)
                continue

            self._log(
                "info",
                f"sem links apos {total_attempts} consultas curtas (status={status})",
                level=logging.WARNING,
            )
            raise RealDebridPlaybackNotReadyError(
                "Torrent temporariamente indisponivel no Real-Debrid. Tente novamente em instantes."
            )

    async def get_stream_url(
        self, magnet: str, type: str = "movie", stremio_id: str = ""
    ) -> str:
        """
        Resolve um magnet no clique usando apenas endpoints suportados.

        Fluxo lazy:
          1. addMagnet
          2. info para inspecionar arquivos
          3. selectFiles
          4. info para obter links
          5. unrestrict/link
        """
        stage = "addMagnet"
        try:
            self._log("addMagnet", "enviando magnet")
            resp_add = await self.client.post(
                f"{self.base_url}/torrents/addMagnet",
                data={"magnet": magnet},
            )
            resp_add.raise_for_status()
            torrent_id = resp_add.json()["id"]
            self._log("addMagnet", "torrent criado")

            stage = "torrents/info"
            torrent_info = await self._get_torrent_info(
                torrent_id,
                "lendo arquivos do torrent",
            )

            files = torrent_info.get("files", [])
            valid_files = []

            invalid_exts = (".txt", ".nfo", ".srt", ".jpg", ".png", ".exe")
            invalid_words = ("sample", "trailer", "extras")

            for file_info in files:
                path = file_info["path"].lower()
                if any(path.endswith(ext) for ext in invalid_exts):
                    continue
                if any(word in path for word in invalid_words):
                    continue
                valid_files.append(file_info)

            if not valid_files:
                self._log(
                    "info",
                    "nenhum arquivo de video valido",
                    level=logging.WARNING,
                )
                raise RealDebridPlaybackNotReadyError(
                    "Torrent temporariamente indisponivel no Real-Debrid. Tente novamente em instantes."
                )

            selected_file_id: str | None = None
            if type == "series" and ":" in stremio_id:
                parts = stremio_id.split(":")
                if len(parts) >= 3:
                    season = parts[1].zfill(2)
                    episode = parts[2].zfill(2)
                    target_str = f"s{season}e{episode}"
                    for file_info in valid_files:
                        if target_str in file_info["path"].lower():
                            selected_file_id = str(file_info["id"])
                            break

            if not selected_file_id:
                largest_file = max(valid_files, key=lambda item: item["bytes"])
                selected_file_id = str(largest_file["id"])

            stage = "selectFiles"
            self._log("selectFiles", "selecionando arquivo principal")
            resp_select = await self.client.post(
                f"{self.base_url}/torrents/selectFiles/{torrent_id}",
                data={"files": selected_file_id},
            )
            resp_select.raise_for_status()
            self._log("selectFiles", "arquivo selecionado")

            stage = "torrents/info.links"
            links = await self._wait_for_links(torrent_id)

            stage = "unrestrict/link"
            self._log("unrestrict/link", "gerando link HTTP")
            resp_unrestrict = await self.client.post(
                f"{self.base_url}/unrestrict/link",
                data={"link": links[0]},
            )
            resp_unrestrict.raise_for_status()
            download_url: str = resp_unrestrict.json()["download"]

            self._log("unrestrict/link", "link HTTP resolvido")
            return download_url

        except RealDebridPlaybackNotReadyError:
            raise
        except httpx.HTTPStatusError as exc:
            self._log(
                stage,
                f"falha HTTP: {_summarize_http_error(exc)}",
                level=logging.ERROR,
            )
            raise RealDebridResolveError(
                "Falha ao resolver playback via Real-Debrid"
            ) from exc
        except Exception as exc:
            self._log(
                stage,
                f"falha inesperada: {type(exc).__name__}",
                level=logging.ERROR,
            )
            raise RealDebridResolveError(
                "Falha inesperada ao resolver playback via Real-Debrid"
            ) from exc

    async def close(self) -> None:
        """Fecha o cliente HTTP."""
        await self.client.aclose()
