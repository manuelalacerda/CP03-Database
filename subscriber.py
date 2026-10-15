"""Bonus: assinante do canal pedidos:confirmados.
Uso: docker compose exec api python subscriber.py"""
from app.db import r

CANAL = "pedidos:confirmados"
ps = r.pubsub()
ps.subscribe(CANAL)
print(f"Aguardando mensagens em '{CANAL}'... (Ctrl+C para sair)", flush=True)
for msg in ps.listen():
    if msg["type"] == "message":
        print(f"[PEDIDO CONFIRMADO] {msg['data']}", flush=True)
