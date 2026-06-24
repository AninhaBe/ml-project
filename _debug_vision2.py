import sys, json
sys.path.insert(0, '.')
from src.cache import get_conn

with get_conn() as conn:
    rows = conn.execute(
        "SELECT pagina, motivo FROM vision_acionado WHERE pdf_path LIKE '%MEGAHYPE%' LIMIT 3"
    ).fetchall()
    print(f"Total MEGAHYPE no cache: {conn.execute('SELECT COUNT(*) FROM vision_acionado WHERE pdf_path LIKE ?', ('%MEGAHYPE%',)).fetchone()[0]}")
    for r in rows:
        print(f"\nPagina {r[0]}:")
        try:
            items = json.loads(r[1])
            for it in items[:2]:
                print("  bbox:", it.get('bbox'), "| nome:", it.get('nome', '')[:40])
        except:
            print("  raw:", str(r[1])[:200])
