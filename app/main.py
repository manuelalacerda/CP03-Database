import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, HTTPException, Query
from pymongo.errors import DuplicateKeyError
from pydantic import BaseModel, Field

from app.db import db, r, criar_indices
from seed import seed

CACHE_TTL = 60          # Req. 5
RESERVA_TTL = 600       # Req. 6 (10 min)
LIMITE_RESERVAS = 10    # Req. 8
JANELA_LIMITE = 60
RANKING_KEY = "ranking:eventos"
CANAL = "pedidos:confirmados"


@asynccontextmanager
async def lifespan(app: FastAPI):
    criar_indices()
    if db.eventos.count_documents({}) == 0:
        seed()
    yield


app = FastAPI(title="TicketFlash", lifespan=lifespan)


# ---------- modelos ----------
class Local(BaseModel):
    cidade: str
    endereco: str


class Setor(BaseModel):
    nome: str
    preco: float = Field(gt=0)
    capacidade: int = Field(gt=0)
    vendidos: int = 0


class EventoIn(BaseModel):
    nome: str
    categoria: str
    data: datetime
    local: Local
    atracoes: list[str] = []
    setores: list[Setor]


class EventoUpdate(BaseModel):
    nome: Optional[str] = None
    categoria: Optional[str] = None
    data: Optional[datetime] = None
    local: Optional[Local] = None
    atracoes: Optional[list[str]] = None
    setores: Optional[list[Setor]] = None


class UsuarioIn(BaseModel):
    nome: str
    email: str
    cidade: str


class ReservaIn(BaseModel):
    usuarioId: str
    eventoId: str
    setor: str
    quantidade: int = Field(gt=0, le=10)


class ConfirmarIn(BaseModel):
    usuarioId: str
    eventoId: str
    setor: str


# ---------- helpers ----------
def oid(valor: str) -> ObjectId:
    try:
        return ObjectId(valor)
    except (InvalidId, TypeError):
        raise HTTPException(400, f"id invalido: {valor}")


def ser(obj):
    """Converte ObjectId/datetime para tipos JSON."""
    if isinstance(obj, dict):
        return {k: ser(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [ser(v) for v in obj]
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def invalidar_cache(evento_id: str):
    r.delete(f"evento:{evento_id}")


# ---------- usuarios ----------
@app.post("/usuarios", status_code=201)
def criar_usuario(u: UsuarioIn):
    doc = u.model_dump() | {"dataCadastro": datetime.now(timezone.utc)}
    try:
        res = db.usuarios.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "email ja cadastrado")
    return {"id": str(res.inserted_id)}


@app.get("/usuarios")
def listar_usuarios(limit: int = 20):
    return [ser(u) for u in db.usuarios.find().limit(limit)]


# ---------- Req. 2: CRUD de eventos ----------
@app.post("/eventos", status_code=201)
def criar_evento(e: EventoIn):
    res = db.eventos.insert_one(e.model_dump())
    return {"id": str(res.inserted_id)}


@app.get("/eventos")
def listar_eventos(categoria: Optional[str] = None, cidade: Optional[str] = None,
                   limit: int = Query(50, le=200)):
    filtro = {}
    if categoria:
        filtro["categoria"] = categoria
    if cidade:
        filtro["local.cidade"] = cidade
    return [ser(e) for e in db.eventos.find(filtro).sort("data", 1).limit(limit)]


# Req. 7 (declarada ANTES de /eventos/{id} para nao ser capturada pelo parametro)
@app.get("/eventos/ranking")
def ranking():
    top = r.zrevrange(RANKING_KEY, 0, 4, withscores=True)
    ids = [oid(i) for i, _ in top]
    nomes = {str(e["_id"]): e["nome"] for e in db.eventos.find({"_id": {"$in": ids}}, {"nome": 1})}
    return [{"posicao": n + 1, "eventoId": i, "nome": nomes.get(i), "visualizacoes": int(s)}
            for n, (i, s) in enumerate(top)]


# Req. 5 + 7: cache-aside + ZINCRBY
@app.get("/eventos/{evento_id}")
def buscar_evento(evento_id: str):
    _id = oid(evento_id)
    key = f"evento:{evento_id}"
    cached = r.get(key)
    if cached:
        r.zincrby(RANKING_KEY, 1, evento_id)
        return {"origem": "cache", "evento": json.loads(cached)}
    doc = db.eventos.find_one({"_id": _id})
    if not doc:
        raise HTTPException(404, "evento nao encontrado")
    data = ser(doc)
    r.set(key, json.dumps(data), ex=CACHE_TTL)
    r.zincrby(RANKING_KEY, 1, evento_id)
    return {"origem": "banco", "evento": data}


# Req. 9: invalidacao no update e no delete
@app.put("/eventos/{evento_id}")
def atualizar_evento(evento_id: str, e: EventoUpdate):
    campos = e.model_dump(exclude_unset=True)
    if not campos:
        raise HTTPException(400, "nenhum campo para atualizar")
    res = db.eventos.update_one({"_id": oid(evento_id)}, {"$set": campos})
    if res.matched_count == 0:
        raise HTTPException(404, "evento nao encontrado")
    invalidar_cache(evento_id)
    return {"atualizado": True}


@app.delete("/eventos/{evento_id}")
def remover_evento(evento_id: str):
    res = db.eventos.delete_one({"_id": oid(evento_id)})
    if res.deleted_count == 0:
        raise HTTPException(404, "evento nao encontrado")
    invalidar_cache(evento_id)
    r.zrem(RANKING_KEY, evento_id)
    return {"removido": True}


# ---------- Req. 4: agregacoes ----------
@app.get("/relatorios/faturamento")
def faturamento():
    pipeline = [
        {"$match": {"status": "confirmado"}},
        {"$group": {"_id": "$eventoId", "faturamento": {"$sum": "$valorTotal"},
                    "pedidos": {"$sum": 1}}},
        {"$lookup": {"from": "eventos", "localField": "_id", "foreignField": "_id", "as": "evento"}},
        {"$unwind": "$evento"},
        {"$project": {"_id": 0, "eventoId": {"$toString": "$_id"}, "nome": "$evento.nome",
                      "faturamento": {"$round": ["$faturamento", 2]}, "pedidos": 1}},
        {"$sort": {"faturamento": -1}},
    ]
    return list(db.pedidos.aggregate(pipeline))


@app.get("/relatorios/ocupacao/{evento_id}")
def ocupacao(evento_id: str):
    pipeline = [
        {"$match": {"_id": oid(evento_id)}},
        {"$unwind": "$setores"},
        {"$project": {"_id": 0, "setor": "$setores.nome", "capacidade": "$setores.capacidade",
                      "vendidos": "$setores.vendidos",
                      "percentualVendido": {"$round": [
                          {"$multiply": [{"$divide": ["$setores.vendidos", "$setores.capacidade"]}, 100]}, 2]}}},
        {"$sort": {"percentualVendido": -1}},
    ]
    res = list(db.eventos.aggregate(pipeline))
    if not res:
        raise HTTPException(404, "evento nao encontrado")
    return res


# ---------- Req. 3: explain() ----------
@app.get("/admin/explain")
def explain(categoria: str = "Show"):
    cursor = db.eventos.find({"categoria": categoria, "data": {"$gte": datetime(2000, 1, 1)}}).sort("data", 1)
    plano = cursor.explain()
    return ser(plano["queryPlanner"]["winningPlan"])


# ---------- Req. 8: limite de requisicoes ----------
def checar_limite(usuario_id: str):
    key = f"limite:reservas:{usuario_id}"
    n = r.incr(key)
    if r.ttl(key) == -1:  # sem expiracao (primeira chamada ou falha anterior)
        r.expire(key, JANELA_LIMITE)
    if n > LIMITE_RESERVAS:
        raise HTTPException(429, f"limite de {LIMITE_RESERVAS} reservas por minuto excedido",
                            headers={"Retry-After": str(max(r.ttl(key), 1))})


# ---------- Req. 6 + 10: fluxo de compra ----------
def chave_reserva(usuario_id, evento_id, setor):
    return f"reserva:{usuario_id}:{evento_id}:{setor}"


@app.post("/reservas", status_code=201)
def reservar(p: ReservaIn):
    checar_limite(p.usuarioId)
    if not db.usuarios.find_one({"_id": oid(p.usuarioId)}, {"_id": 1}):
        raise HTTPException(404, "usuario nao encontrado")
    evento = db.eventos.find_one({"_id": oid(p.eventoId)})
    if not evento:
        raise HTTPException(404, "evento nao encontrado")
    setor = next((s for s in evento["setores"] if s["nome"] == p.setor), None)
    if not setor:
        raise HTTPException(404, "setor nao encontrado")
    if setor["capacidade"] - setor["vendidos"] < p.quantidade:
        raise HTTPException(409, "setor sem vagas suficientes")
    valor = json.dumps({"quantidade": p.quantidade, "preco": setor["preco"]})
    ok = r.set(chave_reserva(p.usuarioId, p.eventoId, p.setor), valor, nx=True, ex=RESERVA_TTL)
    if not ok:
        raise HTTPException(409, "ja existe uma reserva ativa para este setor")
    return {"reservado": True, "expiraEmSegundos": RESERVA_TTL,
            "chave": chave_reserva(p.usuarioId, p.eventoId, p.setor)}


@app.post("/pedidos/confirmar", status_code=201)
def confirmar(p: ConfirmarIn):
    usuario_id, evento_id = oid(p.usuarioId), oid(p.eventoId)
    # GETDEL: le e apaga a reserva atomicamente (evita confirmacao dupla)
    bruto = r.getdel(chave_reserva(p.usuarioId, p.eventoId, p.setor))
    if bruto is None:
        raise HTTPException(410, "reserva expirada ou inexistente")
    reserva = json.loads(bruto)
    qtd = reserva["quantidade"]

    # incremento atomico com a condicao de capacidade no proprio filtro
    res = db.eventos.update_one(
        {"_id": evento_id,
         "$expr": {"$anyElementTrue": {"$map": {
             "input": "$setores", "as": "s",
             "in": {"$and": [{"$eq": ["$$s.nome", p.setor]},
                             {"$lte": [{"$add": ["$$s.vendidos", qtd]}, "$$s.capacidade"]}]}}}}},
        {"$inc": {"setores.$[s].vendidos": qtd}},
        array_filters=[{"s.nome": p.setor}],
    )
    if res.modified_count == 0:
        raise HTTPException(409, "capacidade do setor esgotada")

    pedido = {"usuarioId": usuario_id, "eventoId": evento_id, "setor": p.setor,
              "quantidade": qtd, "valorTotal": round(qtd * reserva["preco"], 2),
              "status": "confirmado", "criadoEm": datetime.now(timezone.utc)}
    try:
        res = db.pedidos.insert_one(pedido)
    except Exception:
        # compensacao: desfaz o incremento se o pedido nao foi gravado
        db.eventos.update_one({"_id": evento_id}, {"$inc": {"setores.$[s].vendidos": -qtd}},
                              array_filters=[{"s.nome": p.setor}])
        raise
    invalidar_cache(p.eventoId)  # vendidos mudou: cache ficaria desatualizado
    # Bonus: Pub/Sub
    r.publish(CANAL, json.dumps(ser({**pedido, "_id": res.inserted_id})))
    return {"pedidoId": str(res.inserted_id), "valorTotal": pedido["valorTotal"], "status": "confirmado"}
