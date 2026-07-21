"""
Camada de storage abstraída para cache e play sessions.

Decisão de design (Fase 3):
  O código do addon nunca acessa o backend de armazenamento diretamente.
  Tudo passa pela interface CacheBackend, que pode ser:
    - SQLiteCacheBackend  (padrão, single-instance)
    - RedisCacheBackend   (futuro, multi-instance)

  O singleton `cache` é criado pela factory `create_cache()` no startup,
  baseado na env var STORAGE_BACKEND.

Critérios para migrar para Redis:
  1. Múltiplas instâncias do app (workers/containers independentes)
     compartilhando play sessions — SQLite não compartilha entre processos.
  2. Lock frequente do SQLite observado nos logs:
     "[CACHE] Lock/contenção detectada" ou "database is locked".
  3. Volume de play sessions > 1000 simultâneas com TTL curto
     gerando churn excessivo de I/O no arquivo .db.
  4. Latência de cache.get/set > 50ms observada nos logs de métricas.

  Enquanto nenhum desses critérios for atingido, SQLite é suficiente.
"""

import json
import logging
import os
import time
from abc import ABC, abstractmethod

import aiosqlite

from app.models.config import settings

logger = logging.getLogger(__name__)


class CacheBackend(ABC):
    """Interface comum para armazenamento de cache e play sessions."""

    @abstractmethod
    async def init(self) -> None:
        """Inicializa conexão/recurso do backend."""
        ...

    @abstractmethod
    async def get(self, key: str) -> list | dict | None:
        """Busca valor no cache. Retorna None se expirado ou inexistente."""
        ...

    @abstractmethod
    async def set(self, key: str, value: list | dict, ttl: int | None = None) -> None:
        """Salva valor no cache com TTL em segundos."""
        ...

    async def get_with_status(self, key: str) -> tuple[list | dict | None, str]:
        """
        Busca valor no cache e retorna (data, status).

        status pode ser:
          - "hit":     chave existe e TTL valido
          - "expired": chave existia mas TTL expirou
          - "miss":    chave nunca existiu

        Implementacao padrao delega para get() sem distinguir expired/miss.
        Backends que conseguem distinguir podem sobrescrever.
        """
        data = await self.get(key)
        return (data, "hit") if data is not None else (None, "miss")

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove uma chave específica do cache."""
        ...

    @abstractmethod
    async def delete_expired(self) -> None:
        """Remove entradas expiradas do cache."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Fecha conexão/recurso do backend."""
        ...


class SQLiteCacheBackend(CacheBackend):
    """Backend SQLite — adequado para single-instance."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.CACHE_DB_PATH
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS stream_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                created_at REAL NOT NULL,
                ttl INTEGER NOT NULL
            )
        """)
        await self._db.commit()
        logger.info(f"[CACHE] SQLite inicializado em {self.db_path}")

    async def get(self, key: str) -> list | dict | None:
        if not self._db:
            return None

        t0 = time.monotonic()
        try:
            cursor = await self._db.execute(
                "SELECT value, created_at, ttl FROM stream_cache WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.warning(f"[CACHE] Lock/contenção detectada em GET ({elapsed:.0f}ms): {e}")
            return None

        elapsed = (time.monotonic() - t0) * 1000

        if not row:
            logger.debug(f"[CACHE] MISS {key} ({elapsed:.0f}ms)")
            return None

        value_json, created_at, ttl = row

        if created_at + ttl < time.time():
            await self._db.execute("DELETE FROM stream_cache WHERE key = ?", (key,))
            await self._db.commit()
            logger.debug(f"[CACHE] EXPIRED {key} ({elapsed:.0f}ms)")
            return None

        try:
            data = json.loads(value_json)
        except json.JSONDecodeError:
            return None

        if elapsed > 50:
            logger.warning(f"[CACHE] GET lento: {key} levou {elapsed:.0f}ms")
        else:
            logger.debug(f"[CACHE] HIT {key} ({elapsed:.0f}ms)")

        return data

    async def get_with_status(self, key: str) -> tuple[list | dict | None, str]:
        """Distingue hit/expired/miss consultando a row antes de deletar."""
        if not self._db:
            return None, "miss"

        try:
            cursor = await self._db.execute(
                "SELECT value, created_at, ttl FROM stream_cache WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
        except Exception as e:
            logger.warning(f"[CACHE] Lock/contenção em get_with_status: {e}")
            return None, "miss"

        if not row:
            return None, "miss"

        value_json, created_at, ttl = row

        if created_at + ttl < time.time():
            await self._db.execute("DELETE FROM stream_cache WHERE key = ?", (key,))
            await self._db.commit()
            logger.debug(f"[CACHE] EXPIRED (with_status) {key}")
            return None, "expired"

        try:
            data = json.loads(value_json)
        except json.JSONDecodeError:
            return None, "miss"

        return data, "hit"

    async def set(self, key: str, value: list | dict, ttl: int | None = None) -> None:
        if not self._db:
            return

        ttl = ttl or settings.CACHE_TTL
        value_json = json.dumps(value, ensure_ascii=False)

        t0 = time.monotonic()
        try:
            await self._db.execute(
                "INSERT OR REPLACE INTO stream_cache (key, value, created_at, ttl) VALUES (?, ?, ?, ?)",
                (key, value_json, time.time(), ttl),
            )
            await self._db.commit()
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            logger.warning(f"[CACHE] Lock/contenção detectada em SET ({elapsed:.0f}ms): {e}")
            return

        elapsed = (time.monotonic() - t0) * 1000
        if elapsed > 50:
            logger.warning(f"[CACHE] SET lento: {key} levou {elapsed:.0f}ms")

    async def delete(self, key: str) -> None:
        if not self._db:
            return
        await self._db.execute("DELETE FROM stream_cache WHERE key = ?", (key,))
        await self._db.commit()

    async def delete_expired(self) -> None:
        if not self._db:
            return
        result = await self._db.execute(
            "DELETE FROM stream_cache WHERE created_at + ttl < ?",
            (time.time(),),
        )
        await self._db.commit()
        logger.info(f"[CACHE] {result.rowcount} entradas expiradas removidas")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None


class RedisCacheBackend(CacheBackend):
    """
    Backend Redis — para multi-instance / alta concorrência.

    STUB: não implementado nesta fase.
    Quando necessário, instalar `redis[hiredis]` e implementar
    usando `redis.asyncio.Redis`.

    Ativar com: STORAGE_BACKEND=redis REDIS_URL=redis://host:6379
    """

    async def init(self) -> None:
        raise NotImplementedError(
            "Redis backend ainda não implementado. "
            "Defina STORAGE_BACKEND=sqlite ou implemente RedisCacheBackend."
        )

    async def get(self, key: str) -> list | dict | None:
        raise NotImplementedError

    async def set(self, key: str, value: list | dict, ttl: int | None = None) -> None:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def delete_expired(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


def create_cache() -> CacheBackend:
    """Factory: cria o backend de cache baseado na configuração."""
    backend = settings.STORAGE_BACKEND.lower()
    if backend == "redis":
        return RedisCacheBackend()
    return SQLiteCacheBackend()


# Singleton global
cache: CacheBackend = create_cache()
