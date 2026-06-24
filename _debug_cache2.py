import sys, json; sys.path.insert(0, '.')
from pathlib import Path
from src.cache import get_conn, pdf_hash

with get_conn() as conn:
    rows = conn.execute("SELECT pdf_hash, pdf_nome, n_produtos FROM pdf_produtos_cache").fetchall()
    print("Entradas no cache:")
    for r in rows:
        print(f"  hash={r[0]} | nome={r[1]} | n={r[2]}")

# Hash do MEGAHYPE
pdfs = list(Path(r'c:/Users/Pichau/Downloads').glob('*MEGAHYPE*.pdf'))
if pdfs:
    h = pdf_hash(pdfs[0])
    print(f"\nHash MEGAHYPE agora: {h}")
    print(f"Arquivo: {pdfs[0].name}")
