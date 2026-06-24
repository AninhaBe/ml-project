import sys, json; sys.path.insert(0, '.')
from src.cache import get_conn

with get_conn() as conn:
    rows = conn.execute('SELECT pdf_hash, pdf_nome, n_produtos FROM pdf_produtos_cache').fetchall()
    print(f"Total PDFs no cache: {len(rows)}")
    for r in rows:
        print(f"  hash={r[0]} | nome={r[1]} | n={r[2]}")

    # Pega o MEGAHYPE especificamente
    row = conn.execute(
        "SELECT produtos_json FROM pdf_produtos_cache WHERE pdf_nome LIKE '%MEGAHYPE%' LIMIT 1"
    ).fetchone()
    if row:
        prods = json.loads(row[0])
        print(f"\nMEGAHYPE: {len(prods)} produtos")
        for p in prods[:5]:
            extras = p.get('extras')
            img = p.get('imagem_path', '')
            print(f"  {p.get('nome','')[:40]}")
            print(f"    extras type: {type(extras).__name__} | valor: {extras}")
            print(f"    img: {str(img)[-60:] if img else 'None'}")
    else:
        print("\nMEGAHYPE NAO ENCONTRADO NO CACHE")
