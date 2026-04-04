from pydantic import BaseModel


class TorrentResult(BaseModel):
    """Resultado de um torrent encontrado por um scraper"""

    title: str  # ex: "Interestelar 1080p Dublado"
    info_hash: str  # hash do torrent, lowercase, sem espaço
    magnet: str  # link magnet completo
    quality: str  # ex: "1080p", "720p", "4K"
    dubbed: bool  # True se dublado PT-BR
    source: str  # nome da fonte, ex: "Apache Torrent"
    size: str | None = None
    seeders: int | None = None


class StreamResult(BaseModel):
    """Resultado formatado para a API do Stremio"""

    name: str  # label exibido no Stremio, ex: "🇧🇷 1080p | Dublado | RD ✅"
    title: str  # subtítulo com detalhes
    url: str | None = None  # link HTTP direto do RD
    infoHash: str | None = None  # fallback magnet (campo camelCase para Stremio)
    behaviorHints: dict = {}  # behaviorHints do Stremio
