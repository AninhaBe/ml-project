import sys; sys.path.insert(0, '.')
from pathlib import Path
from src.config import IMGS_DIR
from src.cache import get_conn

# Ver o que está no cache de produtos
with get_conn() as conn:
    row = conn.execute("SELECT produtos_json FROM pdf_produtos_cache LIMIT 1").fetchone()
    if row:
        import json
        prods = json.loads(row[0])
        print(f"Total produtos no cache: {len(prods)}")
        for p in prods[:5]:
            img = p.get('imagem_path')
            extras = p.get('extras') or {}
            bbox = extras.get('bbox') if isinstance(extras, dict) else None
            print(f"  nome: {p.get('nome','')[:35]}")
            print(f"  bbox: {bbox}")
            print(f"  img:  {img}")
            if img:
                print(f"  exists: {Path(img).exists()}")
            print()
    else:
        print("Cache vazio")

# Ver imgs geradas
print("\nDirs em IMGS_DIR:")
for d in IMGS_DIR.iterdir():
    pngs = list(d.glob('*.png'))
    print(f"  {d.name}: {len(pngs)} PNGs")
    for p in sorted(pngs)[:3]:
        sz = p.stat().st_size // 1024
        print(f"    {p.name}: {sz} KB")
