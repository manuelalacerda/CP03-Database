"""Popula o MongoDB: 12 eventos, 25 usuarios, 60 pedidos.
Uso: docker compose exec api python seed.py   (a API tambem roda o seed se o banco estiver vazio)"""
import random
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.db import db, criar_indices

CATEGORIAS = ["Show", "Teatro", "Esporte", "Festival", "Stand-up"]
CIDADES = ["Sao Paulo", "Rio de Janeiro", "Belo Horizonte", "Curitiba", "Porto Alegre"]
ATRACOES = ["Banda Aurora", "DJ Neon", "Trio Sol", "Cia. Palco Vivo", "Os Comediantes",
            "Orquestra Central", "Lia Mar", "Rock Station"]
NOMES = ["Ana", "Bruno", "Carla", "Diego", "Elisa", "Fabio", "Gabi", "Hugo", "Iris", "Joao",
         "Karen", "Lucas", "Mia", "Nuno", "Olga", "Paulo", "Quita", "Rafa", "Sara", "Tiago",
         "Ursula", "Vitor", "Wanda", "Xavier", "Yara"]


def seed():
    random.seed(42)
    for c in ("eventos", "usuarios", "pedidos"):
        db[c].drop()
    criar_indices()
    agora = datetime.now(timezone.utc)

    eventos = []
    for i in range(12):
        cat = CATEGORIAS[i % len(CATEGORIAS)]
        setores = [{"nome": n, "preco": p, "capacidade": c, "vendidos": 0}
                   for n, p, c in [("Pista", 120.0, 200), ("Camarote", 350.0, 50), ("VIP", 600.0, 20)]
                   [: random.randint(2, 3)]]
        eventos.append({
            "_id": ObjectId(), "nome": f"{cat} {i + 1}: {random.choice(ATRACOES)}", "categoria": cat,
            "data": agora + timedelta(days=random.randint(5, 180)),
            "local": {"cidade": random.choice(CIDADES), "endereco": f"Av. Exemplo, {100 + i * 10}"},
            "atracoes": random.sample(ATRACOES, random.randint(1, 3)), "setores": setores})

    usuarios = [{"_id": ObjectId(), "nome": n, "email": f"{n.lower()}@email.com",
                 "cidade": random.choice(CIDADES),
                 "dataCadastro": agora - timedelta(days=random.randint(1, 400))} for n in NOMES]

    pedidos = []
    for _ in range(60):
        ev, us = random.choice(eventos), random.choice(usuarios)
        st = random.choice(ev["setores"])
        qtd = random.randint(1, 4)
        status = random.choices(["confirmado", "cancelado", "pendente"], [8, 1, 1])[0]
        if status == "confirmado":
            if st["vendidos"] + qtd > st["capacidade"]:
                status = "cancelado"
            else:
                st["vendidos"] += qtd
        pedidos.append({"usuarioId": us["_id"], "eventoId": ev["_id"], "setor": st["nome"],
                        "quantidade": qtd, "valorTotal": round(qtd * st["preco"], 2),
                        "status": status, "criadoEm": agora - timedelta(days=random.randint(0, 30))})

    db.eventos.insert_many(eventos)
    db.usuarios.insert_many(usuarios)
    db.pedidos.insert_many(pedidos)
    print(f"Seed ok: {len(eventos)} eventos, {len(usuarios)} usuarios, {len(pedidos)} pedidos")


if __name__ == "__main__":
    seed()
