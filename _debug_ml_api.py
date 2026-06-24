import sys; sys.path.insert(0, '.')
from src.ml_api import MLClient
from src.config import load_ml_config

ml = MLClient(load_ml_config())

catalog_id = "MLB36941484"  # Estabilizador Gimbal Axnen

# Testa endpoint /products/{id}/items
print("=== /products/{id}/items ===")
data = ml._request("GET", f"/products/{catalog_id}/items", params={"limit": 5})
print("Status:", data.status_code)
print("Body:", data.text[:500])

print()

# Testa endpoint /search?catalog_product_id=
print("=== /search?catalog_product_id ===")
data2 = ml._request("GET", "/sites/MLB/search", params={"catalog_product_id": catalog_id, "limit": 5})
print("Status:", data2.status_code)
import json
d = data2.json()
results = d.get("results", [])
print(f"Results: {len(results)}")
for r in results[:3]:
    print(f"  {r.get('id')} | status={r.get('status')} | qty={r.get('available_quantity')} | title={r.get('title','')[:40]}")
