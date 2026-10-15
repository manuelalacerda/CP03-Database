import os
import redis
from pymongo import MongoClient, ASCENDING

mongo = MongoClient(os.getenv("MONGO_URL", "mongodb://localhost:27017"))
db = mongo[os.getenv("MONGO_DB", "ticketflash")]
r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


def criar_indices():
    """Req. 3 - indices criados pelo codigo."""
    db.usuarios.create_index([("email", ASCENDING)], unique=True, name="uniq_email")
    db.eventos.create_index([("categoria", ASCENDING), ("data", ASCENDING)], name="categoria_data")
    db.pedidos.create_index([("usuarioId", ASCENDING)], name="pedidos_usuario")
    db.eventos.create_index([("local.cidade", ASCENDING)], name="eventos_cidade")
