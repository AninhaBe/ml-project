"""Testa _avaliar_catalogo diretamente para os produtos do catálogo."""
import sys, logging
sys.path.insert(0, '.')
logging.basicConfig(level=logging.WARNING)

from src.ml_api import MLClient
from src.config import load_ml_config
from src.matcher import Matcher

ml = MLClient(load_ml_config())
matcher = Matcher(ml=ml, vision=None)

produtos_teste = [
    ("Garrafa Térmica 800ml com Sensor de Temperatura", "garrafa termica sensor temperatura"),
    ("Estabilizador Gimbal 3 Eixos", "estabilizador gimbal 3 eixos celular"),
    ("Smart Air Tag Rastreador Bluetooth", "rastreador bluetooth air tag"),
]

for nome, query in produtos_teste:
    print(f"\n{'='*60}")
    print(f"PRODUTO: {nome}")
    print(f"Query: {query}")

    results = ml.search_products(query, limit=8)
    print(f"Candidatos encontrados: {len(results)}")

    aprovados = 0
    for r in results:
        cid = r.get("id")
        cname = r.get("name","")[:45]
        metricas = matcher._avaliar_catalogo(cid)
        descartado = metricas.get("descartado")
        vis = metricas.get("visitas_30d", 0)
        score = metricas.get("score", 0)
        if descartado:
            print(f"  REPROV  {cid} | {descartado[:40]} | {cname}")
        else:
            aprovados += 1
            print(f"  APROVADO {cid} | vis={vis} score={score:.0f} | {cname}")

    print(f"  → {aprovados}/{len(results)} aprovados")
