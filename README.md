# TicketFlash — MongoDB + Redis com Python

**Integrantes:**

| RM | Nome | Turma |
|----|------|------|
| RM564887 | Manuela de Lacerda Soares |2TDSPG|
| RM563829 | Sofia Siqueira Fontes |2TDSPG|

## Como executar
```bash
docker compose up --build
```
Sobe `api` (http://localhost:8000, docs em `/docs`), `mongo` e `redis`. Se o banco estiver vazio, a API roda o `seed.py`
(12 eventos, 25 usuários, 60 pedidos). Para repopular manualmente: `docker compose exec api python seed.py`.

Bônus Pub/Sub (em outro terminal): `docker compose exec api python subscriber.py`

Requisições de teste: `ticketflash.http` (VS Code REST Client; também importável no Insomnia).

## Endpoints
| Método | Rota | Descrição |
|---|---|---|
| POST/GET | `/usuarios` | cria (e-mail único → 409) / lista usuários |
| POST | `/eventos` | cria evento |
| GET | `/eventos?categoria=&cidade=` | lista com filtros aplicados no MongoDB |
| GET | `/eventos/{id}` | cache-aside; retorna `"origem": "cache"` ou `"banco"` e faz ZINCRBY |
| PUT | `/eventos/{id}` | atualiza e invalida `evento:{id}` |
| DELETE | `/eventos/{id}` | remove e invalida `evento:{id}` |
| GET | `/eventos/ranking` | top 5 (Sorted Set) |
| GET | `/relatorios/faturamento` | faturamento por evento ($group + $lookup) |
| GET | `/relatorios/ocupacao/{id}` | % vendido por setor ($unwind + $project) |
| GET | `/admin/explain?categoria=Show` | plano de execução do índice composto |
| POST | `/reservas` | limite (429) → checa vagas → `SET NX EX 600` |
| POST | `/pedidos/confirmar` | 410 se expirou; senão `$inc` atômico + pedido + Pub/Sub |

## Requisito 1 — Justificativa da modelagem
- **Setores embutidos em `eventos`:** são sempre lidos junto com o evento, não existem sem ele e são poucos
  (limitados). Embutir evita `$lookup` na página mais acessada e permite o `$inc` atômico em `vendidos` no mesmo
  documento. Também vale para `local` (subdocumento) e `atracoes` (array de strings).
- **`pedidos` referencia `usuarioId` e `eventoId`:** pedidos crescem sem limite (embutir estouraria o documento de
  16 MB) e são consultados por usuário e por evento de forma independente (relatórios). A relação é 1:N com alta cardinalidade.
- **`usuarios` separado:** entidade independente, com e-mail único e ciclo de vida próprio.

## Redis — chaves usadas
| Chave | Tipo | Uso |
|---|---|---|
| `evento:{id}` | String (JSON), TTL 60 s | cache |
| `reserva:{usuarioId}:{eventoId}:{setor}` | String, `SET NX EX 600` | reserva temporária |
| `ranking:eventos` | Sorted Set | visualizações (`ZINCRBY`) |
| `limite:reservas:{usuarioId}` | String, `INCR` + `EXPIRE 60` | 10 reservas/min |
| `pedidos:confirmados` | Pub/Sub | bônus |

## Decisões de consistência
- Cache apagado em update e delete (Req. 9) **e** após confirmar pedido, pois `vendidos` muda.
- Confirmação usa `GETDEL` (lê e apaga a reserva atomicamente), evitando confirmar a mesma reserva duas vezes.
- Capacidade garantida no filtro do `update_one` (`$expr` + `$inc` com `arrayFilters`); se o pedido falhar ao gravar, o incremento é desfeito.

## Requisito 3 — Índices e explain()
Índices criados pelo código em `app/db.py` (função `criar_indices()`, executada na inicialização da API):

| Coleção | Índice | Tipo |
|---|---|---|
| `usuarios` | `email` (`uniq_email`) | único |
| `eventos` | `categoria` + `data` (`categoria_data`) | composto |
| `pedidos` | `usuarioId` (`pedidos_usuario`) | simples |
| `eventos` | `local.cidade` (`eventos_cidade`) | simples (extra, para o filtro por cidade) |

### Como gerar o explain()
Com o ambiente no ar, a consulta `find({categoria: "Show", data: {$gte: ...}}).sort("data", 1)` é explicada pelo endpoint:

```bash
curl "http://localhost:8000/admin/explain?categoria=Show"
```

### Saída do explain()
```json
{
  "stage": "FETCH",
  "inputStage": {
    "stage": "IXSCAN",
    "keyPattern": {"categoria": 1, "data": 1},
    "indexName": "categoria_data",
    "direction": "forward"
  }
}
```

### Leitura do plano
- `IXSCAN` em `categoria_data`: o MongoDB percorreu o índice composto em vez de ler a coleção inteira (`COLLSCAN`).
- O índice atende o filtro por `categoria`, o intervalo em `data` e a ordenação por `data`, sem etapa `SORT` em memória.
- `FETCH` vem depois: só os documentos encontrados no índice são buscados.
