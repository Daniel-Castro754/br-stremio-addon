from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import unquote

# Termos de release que não ajudam a identificar a obra.
_NOISE = {
    "baixar", "download", "torrent", "filme", "filmes", "serie", "series",
    "temporada", "completa", "completo", "dublado", "dublada", "legendado",
    "legendada", "dual", "audio", "pt", "br", "web", "dl", "webrip",
    "bluray", "blu", "ray", "remux", "hdr", "dv", "dolby", "vision",
    "x264", "x265", "h264", "h265", "hevc", "aac", "atmos", "imax",
}


def normalize_release_title(value: str) -> str:
    """Normaliza título/slug para comparação de relevância."""
    value = unquote(value or "")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    tokens = re.findall(r"[a-z0-9]+", value.lower())

    cleaned: list[str] = []
    for token in tokens:
        if token in _NOISE:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", token):
            continue
        if re.fullmatch(r"\d{3,4}p", token):
            continue
        if re.fullmatch(r"s\d{1,2}(?:e\d{1,3})?", token):
            continue
        if re.fullmatch(r"e\d{1,3}", token):
            continue
        cleaned.append(token)

    return " ".join(cleaned)


def is_relevant_release(query: str, candidate_title: str, candidate_url: str = "") -> bool:
    """
    Rejeita falsos positivos grosseiros sem exigir igualdade literal.

    A comparação fuzzy permite pequenas diferenças de tradução/grafia, como
    ``Interstellar`` x ``Interestelar``, mas rejeita resultados sem relação,
    como ``Troy`` x ``Zoey 102``.
    """
    query_norm = normalize_release_title(query)
    candidate_norm = normalize_release_title(f"{candidate_title} {candidate_url}")

    if not query_norm or not candidate_norm:
        return False

    if query_norm in candidate_norm or candidate_norm in query_norm:
        return True

    query_tokens = query_norm.split()
    candidate_tokens = candidate_norm.split()
    query_set = set(query_tokens)
    candidate_set = set(candidate_tokens)

    exact_coverage = len(query_set & candidate_set) / max(1, len(query_set))
    if exact_coverage >= 0.60:
        return True

    fuzzy_matches = 0
    for query_token in query_tokens:
        best = max(
            (
                SequenceMatcher(None, query_token, candidate_token).ratio()
                for candidate_token in candidate_tokens
                if len(candidate_token) >= 3
            ),
            default=0.0,
        )
        if best >= 0.88:
            fuzzy_matches += 1

    if fuzzy_matches / max(1, len(query_tokens)) >= 0.75:
        return True

    return SequenceMatcher(None, query_norm, candidate_norm).ratio() >= 0.72


# Padrões de season/episode em releases PT-BR e internacionais.
_EP_PATTERNS = [
    re.compile(r"s(\d{1,2})e(\d{1,3})", re.IGNORECASE),
    re.compile(r"(\d{1,2})x(\d{1,3})", re.IGNORECASE),
    re.compile(r"temporada\s*(\d{1,2}).{0,15}?epis[oó]dio\s*(\d{1,3})", re.IGNORECASE),
]

# Marca pacote de temporada completa (sem episódio específico).
_SEASON_PACK_PATTERNS = [
    re.compile(r"s(\d{1,2})(?!e\d)", re.IGNORECASE),
    re.compile(r"(\d{1,2})[ªa]?\s*temporada", re.IGNORECASE),
    re.compile(r"temporada\s*(\d{1,2})", re.IGNORECASE),
    re.compile(r"complet[ao]", re.IGNORECASE),
]


def matches_episode(candidate_title: str, season: int | None, episode: int | None) -> bool:
    """
    Verifica se um release de série bate com a temporada/episódio pedidos.

    Sem season/episode informado (filme, ou série sem essa info) -> sempre aceita.
    Com season/episode:
      - Se o título tem S{season}E{episode} (ou 1x05) explícito, exige o
        season E episode exatos.
      - Se o título só menciona a temporada (pacote completo, sem episódio),
        aceita — o pacote contém o episódio pedido.
      - Se o título menciona uma temporada diferente da pedida, rejeita.
      - Sem nenhuma marca de temporada/episódio identificável, aceita
        (não penaliza títulos que não seguem o padrão de nomenclatura).
    """
    if season is None or episode is None:
        return True

    texto = candidate_title or ""

    for pattern in _EP_PATTERNS:
        match = pattern.search(texto)
        if match:
            found_season, found_episode = int(match.group(1)), int(match.group(2))
            return found_season == season and found_episode == episode

    for pattern in _SEASON_PACK_PATTERNS:
        match = pattern.search(texto)
        if match and match.groups():
            found_season = int(match.group(1))
            return found_season == season
        if match:
            # "completo/completa" sem número — aceita, não dá pra confirmar a temporada
            return True

    # Nenhuma marca de temporada/episódio no título — não rejeita por isso.
    return True


def build_series_queries(query: str, season: int | None, episode: int | None) -> list[str]:
    """
    Monta variantes de busca para séries, em ordem de prioridade:
      1. "{título} S01E05" — pega releases do episódio específico.
      2. "{título}" (query original) — pega pacotes de temporada completa.

    Para filmes (season/episode None), retorna só a query original.
    """
    if season is None or episode is None:
        return [query]
    tag = f"S{season:02d}E{episode:02d}"
    return [f"{query} {tag}", query]
