import httpx, re, json

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}

# Testa pagina de produto ML
r = httpx.get("https://www.mercadolivre.com.br/p/MLB63230073", headers=headers, follow_redirects=True, timeout=15)
print("status:", r.status_code)
print("url:", str(r.url)[:80])

# Procura JSON embutido com dados de vendas
patterns = [
    r'"sold_quantity"\s*:\s*(\d+)',
    r'"total_sold"\s*:\s*(\d+)',
    r'"units_sold"\s*:\s*(\d+)',
    r'\+(\d+)\s*vendidos',
    r'"visits"\s*:\s*(\d+)',
    r'"health"\s*:\s*([\d.]+)',
    r'"revenue"\s*:\s*([\d.]+)',
]
for p in patterns:
    m = re.search(p, r.text, re.IGNORECASE)
    print(f"  {p[:30]!r}: {m.group(0)[:60] if m else 'NAO'}")

# Tenta achar __PRELOADED_STATE__ ou window.__
for var in ["__PRELOADED_STATE__", "window.__", "initialState", "serverData"]:
    if var in r.text:
        idx = r.text.index(var)
        print(f"\n  ENCONTROU {var!r} em pos {idx}")
        print("  snippet:", r.text[idx:idx+200])
        break
