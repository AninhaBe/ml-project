import sys; sys.path.insert(0, '.')
from src.ml_api import MLClient
from src.config import load_ml_config

ml = MLClient(load_ml_config())

# Pega candidatos reais da busca da garrafa
results = ml.search_products("garrafa termica sensor temperatura", limit=5)
print(f"Candidatos: {len(results)}")
for r in results[:3]:
    cid = r.get("id")
    name = r.get("name","")[:50]
    print(f"\n=== {cid} | {name} ===")
    
    # Chama get_product_items e mostra raw
    import httpx
    resp = ml._request("GET", f"/products/{cid}/items", params={"limit": 5})
    print(f"  HTTP status: {resp.status_code}")
    data = resp.json()
    print(f"  keys: {list(data.keys())}")
    items = data.get("results", [])
    print(f"  items count: {len(items)}")
    for it in items[:3]:
        print(f"    item_id={it.get('item_id')} status={it.get('status')} sub_status={it.get('sub_status')} qty={it.get('available_quantity')} official_store={it.get('official_store_id')}")
