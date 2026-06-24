import sys; sys.path.insert(0, '.')
from pathlib import Path
from src.pdf_parser import parse_pdf_auto
from src.config import IMGS_DIR

pdf = list(Path('c:/Users/Pichau/Downloads').glob('*MEGAHYPE*.pdf'))[0]
print(f"PDF: {pdf.name}")
prods = parse_pdf_auto(pdf)
print(f"{len(prods)} produtos")
for p in prods[:3]:
    img = p.imagem_path
    bbox = (p.extras or {}).get('bbox')
    exists = img.exists() if img else False
    print(f"  nome: {p.nome[:40]}")
    print(f"  bbox: {bbox}")
    print(f"  img:  {img}")
    print(f"  exists: {exists}")
    print()

print("Pastas imgs:")
for d in IMGS_DIR.iterdir():
    pngs = list(d.glob("*.png"))
    print(f"  {repr(d.name)}: {len(pngs)} PNGs")
    for f in sorted(pngs)[:2]:
        print(f"    {f.name}: {f.stat().st_size//1024}KB")
