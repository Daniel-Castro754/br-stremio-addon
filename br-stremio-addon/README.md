# BR Streams 🇧🇷

> Addon Stremio agregador de torrents PT-BR com integração Real-Debrid

---

## O que é

Addon Stremio que agrega torrents de filmes e séries dublados/dual áudio em
português brasileiro de múltiplas fontes, com suporte a Real-Debrid para
streaming direto sem necessidade de VPN.

## Fontes

| Fonte | Tipo | Conteúdo |
|-------|------|----------|
| 🔥 Apache Torrent | Web scraping | Dublado / Dual Áudio |
| 🎬 Comando Filmes | Web scraping | Dublado / Dual Áudio |
| 🦁 MicoLeão Dublado | Web scraping | Especialista em dublagem |
| 📺 HDR Torrent | Web scraping | 4K / HDR / Dolby Vision |
| 🌐 Brazuca Torrents | Addon proxy | Acervo consolidado BR |

## Instalação rápida

1. Acesse a página de configuração do addon
2. Insira seu token Real-Debrid (opcional, mas recomendado)
3. Clique em "Instalar no Stremio"

## Rodando localmente

### Pré-requisitos
- Python 3.11+
- (Opcional) Token Real-Debrid: https://real-debrid.com/apitoken

### Passos

```bash
git clone https://github.com/seu-usuario/br-stremio-addon
cd br-stremio-addon

python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env

python -m app.main
```

Acesse http://localhost:8000/configure para configurar e instalar.

## Rodando com Docker

```bash
docker build -t br-stremio-addon .
docker run -p 8000:8000 -v $(pwd)/data:/app/data br-stremio-addon
```

## Deploy

### Railway (recomendado)
1. Fork este repositório
2. Crie um projeto no Railway.app
3. Conecte o repositório
4. Adicione `BASE_URL` com a URL pública gerada pelo Railway
5. Deploy automático via Dockerfile

### Render
1. Fork este repositório
2. Crie um Web Service no Render.com
3. Escolha "Docker" como runtime
4. Adicione `BASE_URL` nas env vars
5. O `render.yaml` já configura disco persistente para o cache SQLite

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| PORT | 8000 | Porta do servidor |
| BASE_URL | http://localhost:8000 | URL pública do addon |
| LOG_LEVEL | info | Nível de log (debug/info/warning) |
| CACHE_TTL | 3600 | Tempo de cache em segundos (1h) |
| CACHE_DB_PATH | data/cache.db | Caminho do banco SQLite |

## Arquitetura

```
Stremio → /stream/{type}/{imdb_id}
              ↓
    StreamAggregator
    ├── SQLiteCache (verifica cache)
    ├── [paralelo] ApacheTorrentScraper
    ├── [paralelo] ComandoFilmesScraper
    ├── [paralelo] MicoLeaoScraper
    ├── [paralelo] HDRTorrentScraper
    └── [paralelo] BrazucaAddonScraper
              ↓
    RealDebridService (check cache + unrestrict)
              ↓
    Streams ordenados (RD > qualidade > dublado > seeders)
              ↓
    Stremio exibe resultados
```

## Estrutura do projeto

```
br-stremio-addon/
├── app/
│   ├── main.py
│   ├── manifest.py
│   ├── models/
│   │   ├── config.py
│   │   └── torrent.py
│   ├── routes/
│   │   ├── stream.py
│   │   └── configure.py
│   ├── scrapers/
│   │   ├── base.py
│   │   ├── apache_torrent.py
│   │   ├── comando_filmes.py
│   │   ├── hdr_torrent.py
│   │   ├── micoleao.py
│   │   └── brazuca_addon.py
│   └── services/
│       ├── cache.py
│       ├── labeler.py
│       ├── real_debrid.py
│       └── stream_aggregator.py
├── data/
├── .env.example
├── .dockerignore
├── .github/workflows/docker.yml
├── Dockerfile
├── railway.json
├── render.yaml
└── requirements.txt
```

## Adicionando novas fontes

1. Criar `app/scrapers/nova_fonte.py` herdando `BaseScraper`
2. Implementar `async search(query, imdb_id, type) -> list[TorrentResult]`
3. Adicionar instância em `stream_aggregator.py`

## Licença

MIT
