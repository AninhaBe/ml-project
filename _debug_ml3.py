import sys, requests; sys.path.insert(0, '.')
from src.ml_api import MLClient
from src.config import load_ml_config

ml = MLClient(load_ml_config())
catalog_id = "MLB36941484"

# 1) GET /products/{id} — ver o produto em si
print("=== GET /products/{id} ===")
p = ml.get(f"/products/{catalog_id}")
print("keys:", list((p or {}).keys()))
print("status:", (p or {}).get("status"))
print("name:", (p or {}).get("name","")[:60])

# 2) search normal pelo nome + filtra status
print()
print("=== search por nome, ver available_quantity ===")
res = ml._request("GET", "/sites/MLB/search", params={"q": "estabilizador gimbal axnen hq5", "limit": 5})
print("status:", res.status_code)
d = res.json()
for r in d.get("results", [])[:5]:
    print(f"  id={r.get('id')} status={r.get('status')} qty={r.get('available_quantity')} cat={r.get('catalog_product_id')} title={r.get('title','')[:40]}")

# 3) GET item direto para ver available_quantity
print()
print("=== GET /items/{id} direto ===")
# Pega um item_id do search acima
for r in d.get("results", [])[:2]:
    iid = r.get('id')
    item = ml.get(f"/items/{iid}")
    print(f"  {iid}: status={item.get('status') if item else 'N/A'} qty={item.get('available_quantity') if item else 'N/A'}")
