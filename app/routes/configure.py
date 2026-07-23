from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.manifest import get_manifest
from app.models.config import settings
from app.services.stream_aggregator import SCRAPER_REGISTRY

router = APIRouter()

SCRAPER_UI_INFO: dict[str, dict[str, str]] = {
    "ENABLE_APACHE_TORRENT": {
        "emoji": "&#x1F525;",
        "description": "WordPress BR com foco em dublado e dual audio.",
    },
    "ENABLE_COMANDO_FILMES": {
        "emoji": "&#x1F3AC;",
        "description": "Acervo BR em WordPress com parsing semelhante ao Apache Torrent.",
    },
    "ENABLE_HDR_TORRENT": {
        "emoji": "&#x1F4FA;",
        "description": "Fonte BR com foco em 4K, HDR e Dolby Vision.",
    },
    "ENABLE_MICOLEAO": {
        "emoji": "&#x1F981;",
        "description": "Fonte BR focada em conteudo dublado.",
    },
    "ENABLE_BRAZUCA": {
        "emoji": "&#x1F310;",
        "description": "Consome JSON de um addon Stremio existente, com baixo risco de anti-bot.",
    },
    "ENABLE_YTS": {
        "emoji": "&#x1F39E;",
        "description": "API JSON oficial do YTS, muito confiavel, mas majoritariamente legendado.",
    },
    "ENABLE_TORRENT_GALAXY": {
        "emoji": "&#x1F6E1;",
        "description": "Desativado por padrao. Plain HTTP scraping costuma bater em anti-bot.",
    },
    "ENABLE_1337X": {
        "emoji": "&#x1F512;",
        "description": "Desativado por padrao. Plain HTTP scraping costuma falhar em cloud.",
    },
    "ENABLE_RUTRACKER": {
        "emoji": "&#x1F510;",
        "description": "Desativado por padrao. Busca publica limitada e topicos frequentemente exigem login.",
    },
}

STABILITY_LABELS: dict[str, str] = {
    "estável": "Estavel",
    "bloqueado_antibot": "Bloqueado por anti-bot",
    "não_confiável_cloud": "Nao confiavel em cloud",
}


def _get_scraper_entries() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Separa scrapers ativos e desativados com base no registry real."""
    enabled_entries: list[dict[str, str]] = []
    disabled_entries: list[dict[str, str]] = []

    for flag_name, scraper_cls in SCRAPER_REGISTRY:
        meta = SCRAPER_UI_INFO.get(flag_name, {})
        stability = getattr(scraper_cls, "stability", "estável")
        entry = {
            "flag": flag_name,
            "name": getattr(scraper_cls, "name", scraper_cls.__name__),
            "emoji": meta.get("emoji", "&#x1F4E6;"),
            "description": meta.get("description", "Sem nota operacional cadastrada."),
            "stability": STABILITY_LABELS.get(stability, stability.replace("_", " ").title()),
            "enabled": "true" if getattr(settings, flag_name, False) else "false",
        }
        if entry["enabled"] == "true":
            enabled_entries.append(entry)
        else:
            disabled_entries.append(entry)

    return enabled_entries, disabled_entries


def _render_source_section(title: str, badge_label: str, badge_class: str, entries: list[dict[str, str]]) -> str:
    """Renderiza uma secao simples de scrapers para a pagina /configure."""
    if not entries:
        return ""

    items = []
    for entry in entries:
        items.append(
            f"""
    <div class="source-item">
      <span class="source-emoji">{entry["emoji"]}</span>
      <div class="source-info">
        <div class="source-head">
          <div class="source-name">{entry["name"]}</div>
          <span class="source-badge {badge_class}">{badge_label}</span>
        </div>
        <div class="source-desc">{entry["description"]}</div>
        <div class="source-meta">{entry["stability"]} • {entry["flag"]}</div>
      </div>
    </div>"""
        )

    return f"""
  <div class="card">
    <p class="sources-title">{title}</p>
    {''.join(items)}
  </div>"""


def _build_config_html() -> str:
    """Monta a pagina de configuracao com base no registry real de scrapers."""
    enabled_entries, disabled_entries = _get_scraper_entries()
    sections_html = (
        _render_source_section("Fontes ativas nesta instancia", "Ativa", "badge-on", enabled_entries)
        + _render_source_section(
            "Fontes suportadas, mas desligadas nesta instancia",
            "Desligada",
            "badge-off",
            disabled_entries,
        )
    )

    return CONFIG_HTML_TEMPLATE.replace("__SCRAPER_SECTIONS__", sections_html)


@router.get("/configure", response_class=HTMLResponse)
async def configure_page() -> HTMLResponse:
    """Pagina de configuracao do addon."""
    return HTMLResponse(content=_build_config_html())


@router.get("/health")
async def health() -> dict:
    """Health check simples e sem segredos."""
    manifest = get_manifest()
    return {
        "status": "ok",
        "version": manifest["version"],
        "storage_backend": settings.STORAGE_BACKEND,
        "request_budget_seconds": settings.REQUEST_BUDGET_SECONDS,
        "scraper_timeout_seconds": settings.SCRAPER_TIMEOUT_SECONDS,
    }


CONFIG_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BR Streams - Configuracao</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
    background: #0f0f0f;
    color: #e0e0e0;
    min-height: 100vh;
    display: flex;
    justify-content: center;
    padding: 2rem 1rem;
  }

  .container {
    width: 100%;
    max-width: 680px;
  }

  .header {
    text-align: center;
    margin-bottom: 2rem;
  }

  .header h1 {
    font-size: 2.2rem;
    font-weight: 800;
    color: #fff;
    margin-bottom: 0.4rem;
  }

  .header p {
    color: #888;
    font-size: 0.95rem;
  }

  .card {
    background: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
  }

  .form-group { margin-bottom: 1.25rem; }

  .form-group label {
    display: block;
    font-weight: 600;
    font-size: 0.9rem;
    margin-bottom: 0.4rem;
    color: #ccc;
  }

  .form-group input[type="text"] {
    width: 100%;
    padding: 0.75rem 1rem;
    background: #111;
    border: 1px solid #333;
    border-radius: 8px;
    color: #fff;
    font-size: 0.95rem;
    outline: none;
    transition: border-color 0.2s;
  }

  .form-group input[type="text"]:focus {
    border-color: #00b4d8;
  }

  .form-group .hint {
    display: inline-block;
    margin-top: 0.4rem;
    font-size: 0.8rem;
    color: #00b4d8;
    text-decoration: none;
    transition: color 0.2s;
  }

  .form-group .hint:hover { color: #48cae4; }

  .security-note {
    margin-top: 0.65rem;
    font-size: 0.8rem;
    color: #a9a9a9;
    line-height: 1.45;
  }

  .mode-option {
    margin-bottom: 1.25rem;
    padding: 1rem;
    background: #111;
    border: 1px solid #303030;
    border-radius: 8px;
  }

  .checkbox-row {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    cursor: pointer;
    color: #e0e0e0;
  }

  .checkbox-row input {
    width: 1.1rem;
    height: 1.1rem;
    margin-top: 0.15rem;
    accent-color: #00b4d8;
  }

  .checkbox-row span {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }

  .checkbox-row small,
  .mode-summary {
    color: #929292;
    font-size: 0.8rem;
    line-height: 1.4;
  }

  .mode-summary {
    margin-top: 0.75rem;
    padding-top: 0.75rem;
    border-top: 1px solid #282828;
  }

  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0.7rem 1.25rem;
    border: none;
    border-radius: 8px;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
    transition: transform 0.15s, opacity 0.2s;
    text-decoration: none;
    color: #fff;
  }

  .btn:hover { transform: translateY(-1px); opacity: 0.9; }
  .btn:active { transform: translateY(0); }

  .btn-primary {
    background: #00b4d8;
    width: 100%;
  }

  .btn-secondary {
    background: #2a2a2a;
    border: 1px solid #3a3a3a;
  }

  .btn-stremio {
    background: #7b5bf5;
  }

  .btn-group {
    display: flex;
    gap: 0.6rem;
    margin-top: 0.75rem;
    flex-wrap: wrap;
  }

  .btn-group .btn { flex: 1; min-width: 140px; }

  .result {
    max-height: 0;
    overflow: hidden;
    opacity: 0;
    transition: max-height 0.4s ease, opacity 0.3s ease, margin 0.3s ease;
    margin-top: 0;
  }

  .result.visible {
    max-height: 300px;
    opacity: 1;
    margin-top: 1.25rem;
  }

  .result-url {
    width: 100%;
    padding: 0.75rem 1rem;
    background: #111;
    border: 1px solid #333;
    border-radius: 8px;
    color: #00b4d8;
    font-size: 0.85rem;
    font-family: monospace;
    outline: none;
  }

  .result-label {
    font-size: 0.8rem;
    color: #888;
    margin-bottom: 0.4rem;
  }

  .toast {
    position: fixed;
    bottom: 2rem;
    left: 50%;
    transform: translateX(-50%) translateY(20px);
    background: #00b4d8;
    color: #000;
    padding: 0.6rem 1.5rem;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 600;
    opacity: 0;
    transition: opacity 0.3s, transform 0.3s;
    pointer-events: none;
    z-index: 100;
  }

  .toast.show {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }

  .sources-title {
    font-weight: 700;
    font-size: 1rem;
    margin-bottom: 1rem;
    color: #fff;
  }

  .source-item {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    padding: 0.8rem 0;
    border-bottom: 1px solid #222;
  }

  .source-item:last-child { border-bottom: none; }

  .source-emoji {
    font-size: 1.4rem;
    width: 2rem;
    text-align: center;
    flex-shrink: 0;
  }

  .source-info { flex: 1; }

  .source-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 0.2rem;
  }

  .source-name {
    font-weight: 600;
    font-size: 0.95rem;
    color: #e0e0e0;
  }

  .source-desc {
    font-size: 0.84rem;
    color: #b0b0b0;
    line-height: 1.45;
  }

  .source-meta {
    font-size: 0.76rem;
    color: #7e7e7e;
    margin-top: 0.25rem;
  }

  .source-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0.18rem 0.5rem;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 700;
    white-space: nowrap;
  }

  .badge-on {
    background: rgba(0, 180, 216, 0.14);
    color: #63d8ef;
    border: 1px solid rgba(0, 180, 216, 0.28);
  }

  .badge-off {
    background: rgba(255, 181, 71, 0.12);
    color: #ffcf7e;
    border: 1px solid rgba(255, 181, 71, 0.24);
  }

  .footer {
    text-align: center;
    margin-top: 2rem;
    padding-top: 1rem;
    border-top: 1px solid #1a1a1a;
  }

  .footer a {
    color: #555;
    font-size: 0.8rem;
    text-decoration: none;
    transition: color 0.2s;
  }

  .footer a:hover { color: #00b4d8; }

  @media (max-width: 480px) {
    .header h1 { font-size: 1.6rem; }
    .btn-group { flex-direction: column; }
    .btn-group .btn { min-width: 100%; }
    .source-head { align-items: flex-start; flex-direction: column; }
  }
</style>
</head>
<body>

<div class="container">
  <div class="header">
    <h1>BR Streams &#x1F1E7;&#x1F1F7;</h1>
    <p>P2P gratuito com Real-Debrid opcional</p>
  </div>

  <div class="card">
    <div class="form-group">
      <label for="rd-token">Token Real-Debrid</label>
      <input type="text" id="rd-token" placeholder="Insira seu token da API do Real-Debrid" autocomplete="off" spellcheck="false">
      <a href="https://real-debrid.com/apitoken" target="_blank" rel="noopener" class="hint">Onde encontro meu token? &rarr;</a>
      <p class="security-note">
        O token e opcional. Sem token, o addon usa P2P. Com token, o modo padrao
        usa Real-Debrid. Nunca compartilhe uma URL que contenha seu token.
      </p>
    </div>

    <div class="mode-option">
      <label class="checkbox-row" for="include-p2p">
        <input type="checkbox" id="include-p2p">
        <span>
          <strong>Tambem mostrar opcoes P2P</strong>
          <small>Com token preenchido, exibe RD e P2P juntos.</small>
        </span>
      </label>
      <p class="mode-summary" id="mode-summary">
        Sem token: o link sera gerado no modo P2P.
      </p>
    </div>

    <button class="btn btn-primary" id="btn-generate" type="button">Gerar link de instalacao</button>

    <div class="result" id="result-area">
      <p class="result-label">URL do Manifest:</p>
      <input type="text" class="result-url" id="manifest-url" readonly>
      <div class="btn-group">
        <button class="btn btn-secondary" id="btn-copy" type="button">Copiar URL</button>
        <button class="btn btn-stremio" id="btn-stremio" type="button">Instalar no Stremio</button>
        <button class="btn btn-secondary" id="btn-web" type="button">Instalar no Stremio Web</button>
      </div>
    </div>
  </div>

__SCRAPER_SECTIONS__

  <div class="card">
    <p class="sources-title">Como compartilhar com outras pessoas</p>
    <div class="share-info">
      <p style="color:#b0b0b0;font-size:0.9rem;line-height:1.6;margin-bottom:0.75rem;">
        Cada pessoa pode instalar sem token no modo P2P ou usar o proprio
        token Real-Debrid. Compartilhe apenas esta pagina de configuracao.
      </p>
      <div class="form-group" style="margin-bottom:0.75rem;">
        <label for="share-url">URL desta pagina</label>
        <div style="display:flex;gap:0.5rem;">
          <input type="text" id="share-url" readonly style="flex:1;color:#00b4d8;font-family:monospace;font-size:0.85rem;">
          <button class="btn btn-secondary" id="btn-share" type="button" style="flex:none;min-width:auto;padding:0.7rem 1rem;">Copiar</button>
        </div>
      </div>
      <p style="color:#ff6b6b;font-size:0.82rem;line-height:1.5;">
        &#x26A0; Nunca compartilhe sua URL de manifest — ela contem seu token RD
        e permite streaming na sua conta.
      </p>
    </div>
  </div>

  <div class="footer">
    <a href="#" target="_blank" rel="noopener">GitHub &middot; BR Streams</a>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
(function() {
  var tokenInput = document.getElementById('rd-token');
  var includeP2PInput = document.getElementById('include-p2p');
  var modeSummary = document.getElementById('mode-summary');
  var resultArea = document.getElementById('result-area');
  var manifestInput = document.getElementById('manifest-url');
  var toast = document.getElementById('toast');
  var manifestUrl = '';

  document.getElementById('btn-generate').addEventListener('click', function() {
    var token = tokenInput.value.trim();
    var baseUrl = window.location.origin;

    if (token && includeP2PInput.checked) {
      manifestUrl = baseUrl + '/hybrid/' + encodeURIComponent(token) + '/manifest.json';
    } else if (token) {
      manifestUrl = baseUrl + '/' + encodeURIComponent(token) + '/manifest.json';
    } else {
      manifestUrl = baseUrl + '/manifest.json';
    }

    manifestInput.value = manifestUrl;
    resultArea.classList.add('visible');
  });

  function updateModeSummary() {
    var token = tokenInput.value.trim();

    if (!token) {
      modeSummary.textContent = 'Modo P2P: nao exige token e depende de seeders.';
    } else if (includeP2PInput.checked) {
      modeSummary.textContent = 'Modo hibrido: resultados RD e P2P aparecerao juntos.';
    } else {
      modeSummary.textContent = 'Modo Real-Debrid: somente opcoes HTTP via RD.';
    }
  }

  tokenInput.addEventListener('input', updateModeSummary);
  includeP2PInput.addEventListener('change', updateModeSummary);
  updateModeSummary();

  document.getElementById('btn-copy').addEventListener('click', function() {
    navigator.clipboard.writeText(manifestUrl).then(function() {
      showToast('URL copiada');
    });
  });

  document.getElementById('btn-stremio').addEventListener('click', function() {
    window.open('stremio://install?manifest=' + encodeURIComponent(manifestUrl), '_blank');
  });

  document.getElementById('btn-web').addEventListener('click', function() {
    var webUrl = 'https://web.stremio.com/#/addons?addon=' + encodeURIComponent(manifestUrl);
    window.open(webUrl, '_blank');
  });

  // URL de compartilhamento (pagina /configure)
  var shareInput = document.getElementById('share-url');
  var configureUrl = window.location.origin + '/configure';
  shareInput.value = configureUrl;

  document.getElementById('btn-share').addEventListener('click', function() {
    navigator.clipboard.writeText(configureUrl).then(function() {
      showToast('URL de configuracao copiada');
    });
  });

  function showToast(msg) {
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(function() { toast.classList.remove('show'); }, 2000);
  }
})();
</script>

</body>
</html>
"""
