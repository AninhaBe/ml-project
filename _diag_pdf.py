import sys
sys.path.insert(0, 'g:/ml_market_agent')
from pathlib import Path
import fitz
import re

path = Path(r'c:/Users/Pichau/OneDrive/Documentos/mercado livre/code/CATALOGO B-TEK ORIGINAL 2026.pdf')

CODIGO_RE = re.compile(r'\b(BT-\d{3,5}|DZ-\d{2,5})\b', re.IGNORECASE)
PRECO_RE = re.compile(r'\b(\d{1,3}(?:\.\d{3})*,\d{2})\b')

# Analisa páginas 2, 3, 11 (grade e individual)
paginas_alvo = [1, 2, 3, 10]  # 0-indexed

with fitz.open(path) as doc:
    for page_idx in paginas_alvo:
        page = doc[page_idx]
        print(f'\n{"="*60}')
        print(f'PÁGINA {page_idx+1}')
        print(f'{"="*60}')
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
        for block in blocks:
            if block.get("type") != 0:
                continue
            text_parts = []
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    t = span.get("text", "").strip()
                    if t:
                        text_parts.append(t)
            text = " ".join(text_parts).strip()
            if not text:
                continue
            bbox = block.get("bbox", (0,0,0,0))
            x, y = round(bbox[0]), round(bbox[1])
            has_code = bool(CODIGO_RE.search(text))
            has_price = bool(PRECO_RE.search(text))
            tag = ""
            if has_code: tag += "[COD]"
            if has_price: tag += "[PRECO]"
            print(f'  y={y:4d} x={x:4d} {tag:12s} | {text[:80]!r}')
