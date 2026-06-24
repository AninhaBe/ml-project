import sys; sys.path.insert(0, '.')
from pathlib import Path
from src.config import IMGS_DIR

for d in IMGS_DIR.iterdir():
    print(f"Dir: {d.name}")
    full = d / "page_002_full.png"
    prod0 = d / "page_002_prod00.png"
    if full.exists():
        from PIL import Image
        img_full = Image.open(full)
        img_prod = Image.open(prod0) if prod0.exists() else None
        print(f"  full:    {img_full.size} ({full.stat().st_size//1024}KB)")
        if img_prod:
            print(f"  prod00:  {img_prod.size} ({prod0.stat().st_size//1024}KB)")
            print(f"  ratio W: {img_prod.size[0]/img_full.size[0]:.2f}  ratio H: {img_prod.size[1]/img_full.size[1]:.2f}")
