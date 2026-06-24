import httpx

# API publica sem auth - search por item_id direto
# sold_quantity vem nos resultados de busca publica?
r = httpx.get(
    "https://api.mercadolibre.com/sites/MLB/search",
    params={"q": "air tag rastreador bluetooth", "limit": 5},
    timeout=10
)
print("status:", r.status_code)
results = r.json().get("results", [])
for it in results[:4]:
    print(f"\n  {it.get('id')} | {it.get('title','')[:40]}")
    print(f"    sold_quantity: {it.get('sold_quantity')}")
    print(f"    catalog_product_id: {it.get('catalog_product_id')}")
    sel = it.get("seller", {})
    rep = sel.get("seller_reputation", {})
    print(f"    seller transactions: {rep.get('transactions', {}).get('completed')}")

# Testa buscar por catalog_product_id direto
print("\n\n=== busca por catalog_product_id ===")
r2 = httpx.get(
    "https://api.mercadolibre.com/sites/MLB/search",
    params={"catalog_product_id": "MLB63230073", "limit": 3},
    timeout=10
)
print("status:", r2.status_code, r2.text[:200])
