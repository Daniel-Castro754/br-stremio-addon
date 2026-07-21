# BR Streams - Operacao Atual

## Fluxo real

### `/stream`
- Agrega resultados dos scrapers ativos.
- Quando existe `rd_token` e o torrent tem `magnet`, cria uma play session em `play:{id}`.
- O stream retornado usa `url=/play/{id}`.
- Quando nao existe `rd_token` ou nao existe `magnet`, o addon retorna fallback torrent com `infoHash`.

### `/play`
- Aceita `GET` e `HEAD`.
- Le a play session criada em `/stream`.
- Se a session ja tiver `resolved_url`, reutiliza a URL resolvida sem chamar o Real-Debrid de novo.
- Se ainda nao tiver `resolved_url`, tenta o fluxo lazy:
  - `addMagnet`
  - `info`
  - `selectFiles`
  - `info`
  - `unrestrict/link`
- Retorna:
  - `302` quando resolve com sucesso
  - `503` quando o RD ainda nao esta pronto para playback imediato
  - `502` em falha operacional do RD
  - `404` quando a session nao existe mais
  - `500` quando a session esta corrompida

## TTLs atuais

- Play session: `1800s` (`30 min`)
- `resolved_url`: `1800s` (`30 min`) — alinhada com a play session

## Motivo do ajuste

- O TTL de 5 min (original) e 15 min (anterior) ainda falhavam no runtime.
- O Stremio pode demorar vários minutos entre `/stream` e o clique real, especialmente quando o usuario navega entre conteudos ou recarrega o cliente.
- Alguns clientes fazem `HEAD` seguido de `GET`, consumindo dois requests na mesma sessao.
- A `resolved_url` fica armazenada na mesma chave `play:{id}`. Se ela for gravada com TTL menor, encurta a vida da propria play session.
- 30 min cobre melhor o uso real sem exagero. Sessions continuam efemeras e limpas por TTL.

## Diagnostico de sessao

- O backend SQLite agora distingue entre sessao **expirada** e sessao **inexistente** via `get_with_status()`.
- `/play` retorna 404 com `detail` diferente em cada caso:
  - `"Sessao de playback expirada. Gere um novo stream."` — TTL excedido
  - `"Sessao de playback inexistente. Gere um novo stream."` — nunca criada ou `play_id` invalido
  - `500` com `"Sessao de playback corrompida"` — dados invalidos na sessao
- Logs usam mensagens distintas para facilitar triagem: `sessao expirada`, `sessao inexistente`, `sessao corrompida`.

## Storage atual

- O backend padrao e SQLite em arquivo (`data/cache.db`).
- Redis continua opcional apenas no desenho. Nao e o backend operacional padrao nesta fase.

## Persistencia de play sessions

- Play sessions **sobrevivem ao restart** do servidor.
- O SQLite grava em arquivo persistente (`data/cache.db`), nao em memoria.
- `init()` usa `CREATE TABLE IF NOT EXISTS` — nao apaga dados existentes.
- `delete_expired()` roda apenas no **shutdown**, e so remove entradas com TTL ja expirado.
- Nao existe limpeza automatica no startup nem namespace por processo/boot.
- Validado por teste objetivo: sessao gravada antes do shutdown e lida com sucesso apos re-init.
- Excecao: se o arquivo `data/cache.db` for deletado manualmente, movido, ou o deploy substituir o diretorio (ex: container sem volume persistente), as sessions sao perdidas.

## Limitacoes reais

- Play sessions expiram por TTL apos 30 min, independente de uso.
- Se o cliente reutilizar um stream gerado ha mais de 30 min, a sessao tera expirado e sera necessario gerar novo stream.
- Em deploys com filesystem efemero (ex: container sem volume montado em `data/`), o arquivo `.db` pode ser perdido no redeploy — nesse caso as sessions nao sobrevivem.
- O token do Real-Debrid ainda faz parte do fluxo atual do addon e continua sendo usado na play session.
- O token no path do manifest/stream continua sendo uma limitacao aberta por compatibilidade.
