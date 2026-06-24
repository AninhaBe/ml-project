import sys, logging
logging.basicConfig(level=logging.DEBUG)
sys.path.insert(0, '.')
from src.ml_api import MLClient
from src.config import load_ml_config
from src.matcher import Matcher

ml = MLClient(load_ml_config())

# Simula exatamente o que _avaliar_catalogo faz para os candidatos da garrafa
results = ml.search_products("garrafa termica sensor temperatura", limit=5)
print(f"\n{'='*60}")
print(f"Candidatos encontrados: {len(results)}")

for r in results[:5]:
    cid = r.get("id")
    name = r.get("name","")[:50]
    print(f"\n--- {cid} | {name} ---")
    
    items = ml.get_product_items(cid, limit=20)
    print(f"  /items count: {len(items)}")
    
    if not items:
        print("  → DESCARTADO: sem_winners (404)")
        continue
    
    ativos = [it for it in items if not it.get("official_store_id")]
    print(f"  ativos (sem loja oficial): {len(ativos)}")
    
    if not ativos:
        print("  → DESCARTADO: so_loja_oficial")
        continue
    
    _SUB_STATUS_RUINS = {"deleted","under_review","freeze","out_of_stock","manually_paused","expired","inactive"}
    
    aprovados = []
    for it in ativos[:5]:
        iid = it.get("item_id")
        det = ml.get(f"/items/{iid}") or {}
        status = det.get("status")
        qty = det.get("available_quantity")
        sub = det.get("sub_status")
        ok = status == "active" and int(qty or 0) > 0 and sub not in _SUB_STATUS_RUINS
        print(f"    {iid}: status={status} qty={qty} sub={sub} → {'OK' if ok else 'REPROVADO'}")
        if ok:
            aprovados.append(iid)
    
    if aprovados:
        print(f"  → APROVADO com {len(aprovados)} itens ativos")
    else:
        print(f"  → DESCARTADO: sem_estoque")
