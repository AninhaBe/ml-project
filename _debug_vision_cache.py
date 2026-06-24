import sys, json
sys.path.insert(0, '.')
from src.cache import get_conn

with get_conn() as conn:
    cols = conn.execute("PRAGMA table_info(vision_acionado)").fetchall()
    print("Colunas:", [c[1] for c in cols])
    rows = conn.execute("SELECT * FROM vision_acionado LIMIT 3").fetchall()
    for r in rows:
        d = dict(r)
        # mostra apenas primeiros 400 chars do campo maior
        for k, v in d.items():
            if isinstance(v, str) and len(v) > 200:
                d[k] = v[:400] + '...'
        print(d)
        print('---')
