import re

from app.models.torrent import TorrentResult

ADDON_LABEL = "BR Streams"
UNKNOWN_QUALITY = "DESCONHECIDA"


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    """Retorna True quando qualquer padrao simples aparece no texto."""
    return any(pattern in text for pattern in patterns)


def _unique(items: list[str]) -> list[str]:
    """Remove duplicatas preservando ordem."""
    unique_items: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        unique_items.append(item)
    return unique_items


def _tokenize_title(title: str) -> set[str]:
    """Quebra o release name em tokens para detectar tags com menos ruído."""
    return {token for token in re.split(r"[^A-Z0-9]+", title.upper()) if token}


def _is_dual_audio(title_upper: str) -> bool:
    """Detecta dual audio com base no release name."""
    return _contains_any(
        title_upper,
        ("DUAL AUDIO", "DUAL ÁUDIO", "DUAL-AUDIO", "DUAL"),
    )


def _is_dubbed_ptbr(title_upper: str, dubbed: bool) -> bool:
    """Detecta audio PT-BR quando o scraper marcou dublado ou o titulo sugere isso."""
    return dubbed or _contains_any(
        title_upper,
        ("DUBLADO", "NACIONAL", "PT-BR", "PTBR", "PORTUGUES", "PORTUGUESE"),
    )


def _has_dolby_vision(title_upper: str, quality_upper: str) -> bool:
    """Detecta Dolby Vision com cuidado para nao confundir com DVD."""
    return (
        "DOLBYVISION" in title_upper
        or "DOLBY VISION" in title_upper
        or "DOLBYVISION" in quality_upper
        or "DOLBY VISION" in quality_upper
        or bool(re.search(r"(?<![A-Z0-9])DV(?![A-Z0-9])", title_upper))
    )


def _has_hdr(title_upper: str, quality_upper: str) -> bool:
    """Detecta tags HDR vindas do release name ou do campo quality."""
    return "HDR" in title_upper or "HDR" in quality_upper


def _base_quality_label(quality: str, title_upper: str) -> str | None:
    """Resolve a qualidade principal sem exibir placeholders como Desconhecida."""
    quality_upper = quality.upper().strip()

    if "4K" in quality_upper or "2160P" in quality_upper or "4K" in title_upper or "2160P" in title_upper:
        return "4K"
    if "1080P" in quality_upper or "1080P" in title_upper:
        return "1080p"
    if "720P" in quality_upper or "720P" in title_upper:
        return "720p"
    if "480P" in quality_upper or "480P" in title_upper:
        return "480p"
    if quality_upper and quality_upper != UNKNOWN_QUALITY:
        return quality.strip()

    return None


def _display_quality_label(torrent: TorrentResult) -> str | None:
    """Monta a qualidade curta para o campo name."""
    title_upper = torrent.title.upper()
    quality_upper = torrent.quality.upper()
    quality_parts: list[str] = []

    base_quality = _base_quality_label(torrent.quality, title_upper)
    if base_quality:
        quality_parts.append(base_quality)

    if _has_dolby_vision(title_upper, quality_upper):
        quality_parts.append("DV")
    if _has_hdr(title_upper, quality_upper):
        quality_parts.append("HDR")

    return " ".join(_unique(quality_parts)) or None


def _detect_languages(title_upper: str, dubbed: bool) -> list[str]:
    """Extrai idiomas apenas quando ha sinais minimamente confiaveis no titulo."""
    tokens = _tokenize_title(title_upper)
    languages: list[str] = []

    if _is_dubbed_ptbr(title_upper, dubbed):
        languages.append("PT-BR")

    token_map: list[tuple[str, tuple[str, ...]]] = [
        ("ENG", ("ENG", "ENGLISH", "INGLES", "INGLÊS")),
        ("ITA", ("ITA", "ITALIAN", "ITALIANO")),
        ("ESP", ("ESP", "SPA", "SPANISH", "ESPANOL", "ESPANHOL")),
        ("FRA", ("FRA", "FRENCH", "FRANCES", "FRANCAIS")),
        ("GER", ("GER", "DEU", "GERMAN")),
        ("JPN", ("JPN", "JAP", "JAPANESE")),
    ]

    for label, variants in token_map:
        if any(variant in tokens for variant in variants):
            languages.append(label)

    return _unique(languages)


def _audio_summary(torrent: TorrentResult, languages: list[str]) -> str | None:
    """Resume o audio na linha principal de detalhes."""
    title_upper = torrent.title.upper()

    if _is_dual_audio(title_upper):
        return "Dual Audio / PT-BR"
    if _is_dubbed_ptbr(title_upper, torrent.dubbed):
        return "Dublado / PT-BR"
    if len(languages) >= 2:
        return "Áudio: " + " / ".join(languages)
    if len(languages) == 1:
        return "Áudio: " + languages[0]

    return None


def _extra_release_tags(title_upper: str) -> list[str]:
    """Extrai tags complementares sem repetir demais a qualidade principal."""
    tokens = _tokenize_title(title_upper)
    tags: list[str] = []

    if _contains_any(title_upper, ("WEB-DL", "WEBDL")):
        tags.append("WEB-DL")
    elif "WEBRIP" in title_upper:
        tags.append("WEBRip")
    elif "REMUX" in title_upper:
        tags.append("REMUX")
    elif _contains_any(title_upper, ("BLURAY", "BDRIP", "BDREMUX", "BRRIP")):
        tags.append("BluRay")

    if any(token in tokens for token in ("HEVC", "X265", "H265")):
        tags.append("HEVC / x265")
    elif any(token in tokens for token in ("X264", "H264", "AVC")):
        tags.append("H.264 / x264")

    if "ATMOS" in title_upper:
        tags.append("Atmos")
    elif "TRUEHD" in title_upper:
        tags.append("TrueHD")
    elif "DTS" in title_upper:
        tags.append("DTS")

    return _unique(tags)


def build_stream_name(torrent: TorrentResult, has_play_url: bool) -> str:
    """
    Monta um name curto e escaneavel.

    Exemplos:
      - BR Streams • 4K
      - BR Streams • 4K DV HDR • RD
      - BR Streams • 1080p Dual
    """
    title_upper = torrent.title.upper()
    quality_label = _display_quality_label(torrent)
    name_parts = [ADDON_LABEL]

    if quality_label:
        if _is_dual_audio(title_upper):
            name_parts.append(f"{quality_label} Dual")
        else:
            name_parts.append(quality_label)
    elif _is_dual_audio(title_upper):
        name_parts.append("Dual")

    if has_play_url:
        name_parts.append("RD")

    return " • ".join(name_parts)


def build_stream_title(torrent: TorrentResult, has_play_url: bool) -> str:
    """
    Monta um title rico em 2-3 linhas sem inventar metadados.

    Linha 1:
      release name real
    Linha 2:
      seeders / audio / tamanho / fonte / RD
    Linha 3:
      tags complementares detectadas do release
    """
    title_upper = torrent.title.upper()
    languages = _detect_languages(title_upper, torrent.dubbed)
    lines = [torrent.title.strip()]

    details_line: list[str] = []
    if torrent.seeders is not None:
        details_line.append(f"👥 {torrent.seeders}")

    audio_line = _audio_summary(torrent, languages)
    if audio_line:
        details_line.append(audio_line)

    if torrent.size:
        details_line.append(torrent.size)

    details_line.append(torrent.source)

    if has_play_url:
        details_line.append("RD")

    lines.append(" • ".join(details_line))

    extras_line: list[str] = []
    if _is_dual_audio(title_upper) and len(languages) >= 2:
        extras_line.append(" / ".join(languages))

    extras_line.extend(_extra_release_tags(title_upper))
    extras_line = _unique(extras_line)
    if extras_line:
        lines.append(" • ".join(extras_line))

    return "\n".join(lines)
