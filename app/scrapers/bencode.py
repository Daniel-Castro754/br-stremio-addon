"""
Decoder bencode mínimo, só o suficiente para ler um arquivo .torrent e
extrair o info_hash (BTIH) e a lista de trackers.

Escrito à mão em vez de usar uma lib de terceiros porque as opções mais
conhecidas no PyPI (ex: bencodepy) são GPL — e isso não deveria virar uma
dependência de licença viral só pra decodificar um dicionário bencode,
que é um formato simples o bastante pra implementar em poucas linhas.
"""

from __future__ import annotations

import hashlib


def _read(data: bytes, pos: int) -> tuple[object, int]:
    """Decodifica um valor bencode a partir de `pos`. Retorna (valor, próxima_posição)."""
    marker = data[pos : pos + 1]

    if marker == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1 : end]), end + 1

    if marker == b"l":
        pos += 1
        items: list = []
        while data[pos : pos + 1] != b"e":
            item, pos = _read(data, pos)
            items.append(item)
        return items, pos + 1

    if marker == b"d":
        pos += 1
        result: dict = {}
        while data[pos : pos + 1] != b"e":
            key, pos = _read(data, pos)
            value, pos = _read(data, pos)
            result[key] = value
        return result, pos + 1

    # string: "<tamanho>:<bytes>"
    colon = data.index(b":", pos)
    length = int(data[pos:colon])
    start = colon + 1
    end = start + length
    return data[start:end], end


def parse_torrent(data: bytes) -> tuple[dict, str | None]:
    """
    Decodifica um arquivo .torrent e retorna (dicionário decodificado, info_hash).

    info_hash é o SHA1 dos bytes BRUTOS (não re-codificados) do valor da
    chave "info" do dicionário top-level — essa é a definição exata de
    info_hash no protocolo BitTorrent (BEP 3). Por isso o valor de "info"
    precisa ser lido a partir do offset real no arquivo original, e não
    reconstruído por um encoder (que poderia produzir bytes diferentes
    do original em casos de borda).
    """
    if data[:1] != b"d":
        return {}, None

    top, _ = _read(data, 0)
    if not isinstance(top, dict) or b"info" not in top:
        return top if isinstance(top, dict) else {}, None

    pos = 1  # depois do 'd' inicial
    info_hash: str | None = None
    while data[pos : pos + 1] != b"e":
        key, pos = _read(data, pos)
        if key == b"info":
            info_start = pos
            _, pos = _read(data, pos)
            info_hash = hashlib.sha1(data[info_start:pos]).hexdigest()
        else:
            _, pos = _read(data, pos)

    return top, info_hash
