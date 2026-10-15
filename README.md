# TicketFlash — MongoDB + Redis com Python

**Integrantes (preencher):**

| RM | Nome |
|----|------|
|    |      |

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
Índices criados em `app/db.py`: `usuarios.email` (único), `eventos (categoria, data)` (composto), `pedidos.usuarioId`
(+ `eventos.local.cidade`).

Gerar a saída: `curl "http://localhost:8000/admin/explain?categoria=Show"`

> **COLE AQUI A SAÍDA REAL DO SEU AMBIENTE.** Resultado esperado (formato):
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
`IXSCAN` (e não `COLLSCAN`) mostra que o índice composto atende o filtro e a ordenação.
