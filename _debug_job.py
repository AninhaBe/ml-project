import sys, json; sys.path.insert(0, '.')
from pathlib import Path
from src.config import IMGS_DIR
from src.cache import get_conn

with get_conn() as conn:
    row = conn.execute("SELECT pdf_hash, pdf_nome, produtos_json FROM pdf_produtos_cache LIMIT 1").fetchone()
    if not row:
        print("Cache vazio")
        sys.exit()

    prods = json.loads(row[2])
    print(f"PDF: {row[1]} | {len(prods)} produtos")
    for p in prods[:5]:
        img_str = p.get('imagem_path') or ''
        extras = p.get('extras') or {}
        bbox = extras.get('bbox') if isinstance(extras, dict) else None
        p_abs = Path(img_str) if img_str else None
        p_rel = IMGS_DIR / img_str if img_str else None
        print(f"\n  {p.get('nome','')[:40]}")
        print(f"  bbox: {bbox}")
        print(f"  img_str: {img_str}")
        print(f"  abs exists: {p_abs.exists() if p_abs else 'N/A'}")
        print(f"  rel exists: {p_rel.exists() if p_rel else 'N/A'}")
        print(f"  rel path:   {p_rel}")
