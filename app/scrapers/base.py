import contextvars
import logging
import time
from abc import ABC, abstractmethod

import httpx

from app.models.config import settings
from app.models.torrent import TorrentResult

logger = logging.getLogger(__name__)

# ContextVar para request_id — seguro sob concorrência async.
# Cada asyncio.Task herda uma cópia do contexto do pai, portanto
# dois requests simultâneos usando a mesma instância de scraper
# nunca cruzam valores. Não há estado mutável no objeto scraper.
_current_req_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_req_id", default=""
)

# Headers realistas para evitar bloqueio
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


def set_req_id(req_id: str) -> contextvars.Token[str]:
    """Define o request_id no contexto atual. Retorna token para reset."""
    return _current_req_id.set(req_id)


def get_req_id() -> str:
    """Retorna o request_id do contexto atual."""
    return _current_req_id.get()


class BaseScraper(ABC):
    """Classe base para todos os scrapers de torrent"""

    name: str = ""
    base_url: str = ""

    # Classificação operacional — usada para documentação e triagem.
    stability: str = "estável"

    def __init__(self) -> None:
        self.last_error: str | None = None
        self.client = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=settings.SCRAPER_TIMEOUT_SECONDS,
        )

    def _log_prefix(self) -> str:
        """Prefixo de log com req_id do contexto atual."""
        req_id = get_req_id()
        if req_id:
            return f"[{req_id}] [{self.name}]"
        return f"[{self.name}]"

    async def _get(self, url: str) -> httpx.Response | None:
        """Faz GET com tratamento de erro, métricas de tempo e classificação de falha."""
        prefix = self._log_prefix()
        t0 = time.monotonic()
        self.last_error = None
        try:
            response = await self.client.get(url)
            elapsed = (time.monotonic() - t0) * 1000
            status = response.status_code

            if status == 403:
                self.last_error = "HTTP 403: provável bloqueio anti-bot/Cloudflare"
                logger.warning(
                    f"{prefix} HTTP 403 Forbidden ({elapsed:.0f}ms) "
                    f"— provável bloqueio anti-bot/Cloudflare"
                )
                return None
            if status == 429:
                self.last_error = "HTTP 429: limite de requisições"
                logger.warning(
                    f"{prefix} HTTP 429 Rate Limited ({elapsed:.0f}ms)"
                )
                return None

            response.raise_for_status()
            logger.debug(f"{prefix} GET {status} ({elapsed:.0f}ms)")
            return response

        except httpx.TimeoutException:
            elapsed = (time.monotonic() - t0) * 1000
            self.last_error = f"timeout após {elapsed:.0f}ms"
            logger.warning(
                f"{prefix} TIMEOUT após {elapsed:.0f}ms "
                f"(limite: {settings.SCRAPER_TIMEOUT_SECONDS}s)"
            )
            return None

        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            self.last_error = str(e)
            logger.error(f"{prefix} ERRO ({elapsed:.0f}ms): {e}")
            return None

    async def _get_with_fallback(self, urls: list[str]) -> httpx.Response | None:
        """Tenta cada URL da lista em ordem; na primeira que responder, atualiza self.base_url."""
        prefix = self._log_prefix()
        self.last_error = None
        for url in urls:
            try:
                response = await self.client.get(url)
                response.raise_for_status()
                # Extrai base_url da URL que funcionou
                from urllib.parse import urlparse
                parsed = urlparse(str(response.url))
                new_base = f"{parsed.scheme}://{parsed.netloc}"
                if new_base != self.base_url:
                    logger.info(f"{prefix} URL ativa: {new_base}")
                    self.base_url = new_base
                self.last_error = None
                return response
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
                self.last_error = str(e)
                logger.warning(f"{prefix} Falha em {url}: {e}")
                continue
            except Exception as e:
                self.last_error = str(e)
                logger.warning(f"{prefix} Erro inesperado em {url}: {e}")
                continue
        logger.error(f"{prefix} Todas as URLs falharam: {urls}")
        return None

    @abstractmethod
    async def search(self, query: str, imdb_id: str, type: str) -> list[TorrentResult]:
        """Busca torrents — implementar em cada scraper"""
        ...

    async def close(self) -> None:
        """Fecha o cliente HTTP"""
        await self.client.aclose()
