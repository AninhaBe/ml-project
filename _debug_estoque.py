import sys; sys.path.insert(0, '.')
from src.ml_api import MLClient
from src.config import load_ml_config

ml = MLClient(load_ml_config())

# Busca gimbal para ver os items
results = ml.search_products("estabilizador gimbal 3 eixos", limit=5)
print(f"Resultados: {len(results)}")
for r in results[:3]:
    cid = r.get('id')
    name = r.get('name', '')[:50]
    print(f"\n  Catalogo: {cid} | {name}")
    items = ml.get_product_items(cid, limit=10)
    print(f"  Items: {len(items)}")
    for it in items[:5]:
        status = it.get('status')
        qty = it.get('available_quantity')
        seller = it.get('seller_id')
        iid = it.get('item_id')
        print(f"    item={iid} status={status} qty={qty} seller={seller}")
