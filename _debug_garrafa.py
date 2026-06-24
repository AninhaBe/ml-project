import sys; sys.path.insert(0, '.')
from src.ml_api import MLClient
from src.config import load_ml_config

ml = MLClient(load_ml_config())

print("=== BUSCA: garrafa termica sensor temperatura ===")
results = ml.search_products("garrafa termica sensor temperatura", limit=10)
print(f"Candidatos: {len(results)}\n")

for r in results:
    cid = r.get("id")
    name = r.get("name","")[:55]
    
    # Checa /products/{id}/items
    items = ml.get_product_items(cid, limit=5)
    
    if not items:
        print(f"MORTO   {cid} | {name}")
        continue
    
    # Visitas do primeiro item
    iid = items[0].get("item_id") if items else None
    vis = 0
    if iid:
        try:
            vis = ml.get_item_visits_30d(iid)
        except:
            pass
    
    print(f"VIVO    {cid} | items={len(items)} vis={vis:4d} | {name}")
    for it in items[:2]:
        print(f"         item_id={it.get('item_id')} status={it.get('status')} qty={it.get('available_quantity')} store={it.get('official_store_id')}")

print()
print("=== PRODUTO MATCHADO (indisponível que apareceu) ===")
# Checa o catálogo específico que apareceu na tela
for cid_test in ["MLB36941484", "MLB35062915", "MLB41366348"]:
    p = ml.get(f"/products/{cid_test}") or {}
    items = ml.get_product_items(cid_test, limit=5)
    print(f"\n{cid_test}: name={p.get('name','')[:40]} | /items count={len(items)}")
    for it in items[:3]:
        print(f"  item={it.get('item_id')} status={it.get('status')} qty={it.get('available_quantity')}")
