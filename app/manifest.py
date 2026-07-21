def get_manifest() -> dict:
    """Retorna o manifest do addon no formato esperado pelo Stremio"""
    return {
        "id": "community.br-streams",
        "version": "1.0.0",
        "name": "BR Streams 🇧🇷",
        "description": "Agregador de torrents PT-BR com Real-Debrid",
        "resources": ["stream"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "behaviorHints": {"configurable": False},
        "catalogs": [],
    }
