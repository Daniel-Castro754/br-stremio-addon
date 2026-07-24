import time

import pytest

from app.services.cache import SQLiteCacheBackend


async def _backend(tmp_path) -> SQLiteCacheBackend:
    backend = SQLiteCacheBackend(db_path=str(tmp_path / "test_cache.db"))
    await backend.init()
    return backend


class TestSQLiteCacheBackendBasico:
    @pytest.mark.asyncio
    async def test_init_cria_arquivo_e_permite_operar(self, tmp_path):
        db_path = tmp_path / "sub" / "cache.db"
        backend = SQLiteCacheBackend(db_path=str(db_path))
        await backend.init()

        assert db_path.exists()
        await backend.close()

    @pytest.mark.asyncio
    async def test_set_e_get_roundtrip_dict(self, tmp_path):
        backend = await _backend(tmp_path)
        await backend.set("chave", {"a": 1, "b": [1, 2, 3]})

        resultado = await backend.get("chave")

        assert resultado == {"a": 1, "b": [1, 2, 3]}
        await backend.close()

    @pytest.mark.asyncio
    async def test_set_e_get_roundtrip_list(self, tmp_path):
        backend = await _backend(tmp_path)
        await backend.set("chave", [{"x": 1}, {"x": 2}])

        resultado = await backend.get("chave")

        assert resultado == [{"x": 1}, {"x": 2}]
        await backend.close()

    @pytest.mark.asyncio
    async def test_get_chave_inexistente_retorna_none(self, tmp_path):
        backend = await _backend(tmp_path)
        assert await backend.get("nao-existe") is None
        await backend.close()

    @pytest.mark.asyncio
    async def test_set_sobrescreve_valor_anterior(self, tmp_path):
        backend = await _backend(tmp_path)
        await backend.set("chave", {"v": 1})
        await backend.set("chave", {"v": 2})

        assert await backend.get("chave") == {"v": 2}
        await backend.close()


class TestSQLiteCacheBackendTTL:
    async def _expirar_manualmente(self, backend: SQLiteCacheBackend, key: str) -> None:
        """Força a expiração sem depender de sleep — regrava created_at no passado."""
        await backend._db.execute(
            "UPDATE stream_cache SET created_at = ? WHERE key = ?",
            (time.time() - 999999, key),
        )
        await backend._db.commit()

    @pytest.mark.asyncio
    async def test_get_expirado_retorna_none_e_remove_a_linha(self, tmp_path):
        backend = await _backend(tmp_path)
        await backend.set("chave", {"v": 1}, ttl=3600)
        await self._expirar_manualmente(backend, "chave")

        assert await backend.get("chave") is None

        # A linha expirada some do banco — segunda consulta é MISS, não EXPIRED.
        cursor = await backend._db.execute(
            "SELECT COUNT(*) FROM stream_cache WHERE key = ?", ("chave",)
        )
        (count,) = await cursor.fetchone()
        assert count == 0
        await backend.close()

    @pytest.mark.asyncio
    async def test_get_with_status_distingue_hit_expired_miss(self, tmp_path):
        backend = await _backend(tmp_path)

        # miss: nunca existiu
        _, status = await backend.get_with_status("nunca-existiu")
        assert status == "miss"

        # hit: existe e válido
        await backend.set("valida", {"v": 1}, ttl=3600)
        data, status = await backend.get_with_status("valida")
        assert status == "hit"
        assert data == {"v": 1}

        # expired: existia mas TTL passou
        await backend.set("expirada", {"v": 2}, ttl=3600)
        await self._expirar_manualmente(backend, "expirada")
        data, status = await backend.get_with_status("expirada")
        assert status == "expired"
        assert data is None

        await backend.close()

    @pytest.mark.asyncio
    async def test_ttl_zero_e_respeitado_em_vez_de_virar_default(self, tmp_path):
        """
        ttl or settings.CACHE_TTL trata 0 como falsy — um ttl=0 explícito
        virava silenciosamente o default (geralmente 3600s) em vez de ser
        respeitado como está. Comportamento corrigido: usa `is not None`.
        """
        backend = await _backend(tmp_path)
        await backend.set("chave", {"v": 1}, ttl=0)

        cursor = await backend._db.execute(
            "SELECT ttl FROM stream_cache WHERE key = ?", ("chave",)
        )
        (ttl_salvo,) = await cursor.fetchone()
        assert ttl_salvo == 0
        await backend.close()


class TestSQLiteCacheBackendDelete:
    @pytest.mark.asyncio
    async def test_delete_remove_a_chave(self, tmp_path):
        backend = await _backend(tmp_path)
        await backend.set("chave", {"v": 1})
        await backend.delete("chave")

        assert await backend.get("chave") is None
        await backend.close()

    @pytest.mark.asyncio
    async def test_delete_chave_inexistente_nao_quebra(self, tmp_path):
        backend = await _backend(tmp_path)
        await backend.delete("nunca-existiu")  # não deve levantar exceção
        await backend.close()

    @pytest.mark.asyncio
    async def test_delete_expired_remove_so_as_expiradas(self, tmp_path):
        backend = await _backend(tmp_path)
        await backend.set("valida", {"v": 1}, ttl=3600)
        await backend.set("expirada_1", {"v": 2}, ttl=3600)
        await backend.set("expirada_2", {"v": 3}, ttl=3600)

        await backend._db.execute(
            "UPDATE stream_cache SET created_at = ? WHERE key IN ('expirada_1', 'expirada_2')",
            (time.time() - 999999,),
        )
        await backend._db.commit()

        await backend.delete_expired()

        assert await backend.get("valida") == {"v": 1}
        cursor = await backend._db.execute("SELECT COUNT(*) FROM stream_cache")
        (count,) = await cursor.fetchone()
        assert count == 1
        await backend.close()


class TestSQLiteCacheBackendDurabilidade:
    @pytest.mark.asyncio
    async def test_dados_sobrevivem_a_reconexao_no_mesmo_arquivo(self, tmp_path):
        """Valida a alegação do OPERATIONS.md: dados persistem entre restarts,
        já que o SQLite grava em arquivo e não em memória."""
        db_path = str(tmp_path / "cache.db")

        backend1 = SQLiteCacheBackend(db_path=db_path)
        await backend1.init()
        await backend1.set("chave", {"sobrevive": True})
        await backend1.close()

        backend2 = SQLiteCacheBackend(db_path=db_path)
        await backend2.init()
        resultado = await backend2.get("chave")

        assert resultado == {"sobrevive": True}
        await backend2.close()

    @pytest.mark.asyncio
    async def test_init_e_idempotente_create_table_if_not_exists(self, tmp_path):
        db_path = str(tmp_path / "cache.db")
        backend1 = SQLiteCacheBackend(db_path=db_path)
        await backend1.init()
        await backend1.set("chave", {"v": 1})
        await backend1.close()

        # Reabrir e reinicializar não deve apagar dados existentes.
        backend2 = SQLiteCacheBackend(db_path=db_path)
        await backend2.init()
        assert await backend2.get("chave") == {"v": 1}
        await backend2.close()


class TestSQLiteCacheBackendSemInit:
    """Operações antes de init() (self._db is None) devem ser no-ops seguros,
    não devem levantar exceção."""

    @pytest.mark.asyncio
    async def test_get_sem_init_retorna_none(self):
        backend = SQLiteCacheBackend(db_path="/tmp/nao-usado.db")
        assert await backend.get("chave") is None

    @pytest.mark.asyncio
    async def test_get_with_status_sem_init_retorna_miss(self):
        backend = SQLiteCacheBackend(db_path="/tmp/nao-usado.db")
        data, status = await backend.get_with_status("chave")
        assert data is None
        assert status == "miss"

    @pytest.mark.asyncio
    async def test_set_sem_init_nao_quebra(self):
        backend = SQLiteCacheBackend(db_path="/tmp/nao-usado.db")
        await backend.set("chave", {"v": 1})  # não deve levantar exceção

    @pytest.mark.asyncio
    async def test_delete_sem_init_nao_quebra(self):
        backend = SQLiteCacheBackend(db_path="/tmp/nao-usado.db")
        await backend.delete("chave")  # não deve levantar exceção

    @pytest.mark.asyncio
    async def test_delete_expired_sem_init_nao_quebra(self):
        backend = SQLiteCacheBackend(db_path="/tmp/nao-usado.db")
        await backend.delete_expired()  # não deve levantar exceção

    @pytest.mark.asyncio
    async def test_close_sem_init_nao_quebra(self):
        backend = SQLiteCacheBackend(db_path="/tmp/nao-usado.db")
        await backend.close()  # não deve levantar exceção
