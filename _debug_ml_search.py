import sys, requests; sys.path.insert(0, '.')
from src.ml_api import MLClient
from src.config import load_ml_config

ml = MLClient(load_ml_config())
catalog_id = "MLB36941484"

# Tenta search público sem auth (catalogo_product_id)
print("=== search sem auth ===")
r = requests.get(
    "https://api.mercadolibre.com/sites/MLB/search",
    params={"catalog_product_id": catalog_id, "limit": 5},
    timeout=15
)
print("Status:", r.status_code)
d = r.json()
results = d.get("results", [])
print(f"Results: {len(results)}")
for item in results[:5]:
    print(f"  {item.get('id')} | status={item.get('status')} | qty={item.get('available_quantity')} | title={item.get('title','')[:45]}")

print()

# Tenta com auth
print("=== search COM auth ===")
r2 = ml._request("GET", "/sites/MLB/search", params={"catalog_product_id": catalog_id, "limit": 5, "status": "active"})
print("Status:", r2.status_code)
print("Body[:300]:", r2.text[:300])
