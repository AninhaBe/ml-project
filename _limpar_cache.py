import sys, shutil
sys.path.insert(0, '.')
from pathlib import Path
from src.cache import get_conn, pdf_hash, init_db
from src.config import IMGS_DIR

init_db()
pdf = list(Path(r'c:/Users/Pichau/Downloads').glob('*MEGAHYPE*.pdf'))[0]
h = pdf_hash(pdf)

with get_conn() as conn:
    tbls = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print('Tabelas:', [t[0] for t in tbls])
    r1 = conn.execute('DELETE FROM pdf_produtos_cache WHERE pdf_hash=?', (h,))
    r2 = conn.execute('DELETE FROM pdf_resultados_cache WHERE pdf_hash=?', (h,))
    r3 = conn.execute('DELETE FROM vision_acionado WHERE pdf_path LIKE ?', ('%MEGAHYPE%',))
    conn.commit()
    print(f'produtos: {r1.rowcount}, resultados: {r2.rowcount}, vision_cache: {r3.rowcount}')

imgs_dir = IMGS_DIR / pdf.stem
if imgs_dir.exists():
    shutil.rmtree(imgs_dir)
    print(f'Imgs removidas: {imgs_dir}')
print('Tudo limpo')
