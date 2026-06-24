"""Extrator de produtos via Claude Vision para páginas de catálogo problemáticas.

Renderiza páginas via PyMuPDF, envia para Claude Vision e retorna lista de produtos.
Cache por hash(png_bytes) em SQLite para evitar re-processamento.
Paraleliza chamadas com ThreadPoolExecutor.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
from anthropic import Anthropic

from .config import DB_PATH, load_claude_vision_config

logger = logging.getLogger(__name__)

VISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS vision_acionado (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_path TEXT NOT NULL,
    pagina INTEGER NOT NULL,
    png_hash TEXT NOT NULL,
    motivo TEXT NOT NULL,
    n_produtos INTEGER,
    custo_estimado_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    criado_em REAL NOT NULL,
    UNIQUE (png_hash)
);
CREATE INDEX IF NOT EXISTS idx_vision_pdf ON vision_acionado(pdf_path, pagina);
CREATE INDEX IF NOT EXISTS idx_vision_hash ON vision_acionado(png_hash);
"""

PROMPT_SYSTEM = """Você é um extrator de dados de catálogos de produtos para distribuidores brasileiros.
Sua tarefa é identificar TODOS os produtos visíveis na imagem de página de catálogo.

Para cada produto, extraia:
- nome: nome comercial limpo do produto (sem especificações técnicas, sem código)
- codigo: código do produto (ex: DZ-52, BT-434, BM-8696) — null se não houver
- preco: preço em reais como número decimal (ex: 298.0) — null se não visível
- marca: marca do produto se identificável — null se não identificável
- bbox: bounding box do produto na imagem como [x0, y0, x1, y1] em fração da imagem (0.0 a 1.0)
         onde (0,0) é canto superior esquerdo e (1,1) é canto inferior direito
         Inclua a foto + nome + preço do produto no bbox

Regras:
- Ignore cabeçalhos de seção, rodapés, textos de navegação ("Back to Home")
- Ignore especificações técnicas (voltagem, RPM, torque) — não são o nome
- Se a página tiver 1 produto, retorne lista com 1 item
- Se a página tiver grade com N produtos, retorne N itens
- Nome deve ser descritivo e curto (máx 60 chars): ex "Parafusadeira 21V Pro" não "PARAFUSADEIRA 21V PRO DZ-52 350N.m 02 Baterias..."

Responda APENAS com JSON válido neste formato exato (sem markdown, sem explicações):
[{"nome": "...", "codigo": "...", "preco": 0.0, "marca": "...", "bbox": [0.0, 0.0, 1.0, 1.0]}]"""


@contextmanager
def _get_conn(path: Path = DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _init_vision_db(path: Path = DB_PATH) -> None:
    with _get_conn(path) as conn:
        conn.executescript(VISION_SCHEMA)
        conn.commit()


def _png_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _lookup_cache(png_hash: str, db_path: Path = DB_PATH) -> list[dict] | None:
    """Retorna resultado cacheado para este hash de imagem, ou None."""
    _init_vision_db(db_path)
    with _get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM vision_acionado WHERE png_hash = ? LIMIT 1",
            (png_hash,),
        ).fetchone()
        if row and row["n_produtos"] is not None:
            return json.loads(row["motivo"]) if row["motivo"].startswith("[") else None
    return None


def _save_cache(
    pdf_path: str,
    pagina: int,
    png_hash: str,
    motivo_trigger: str,
    produtos_json: list[dict],
    input_tokens: int,
    output_tokens: int,
    db_path: Path = DB_PATH,
) -> None:
    _init_vision_db(db_path)
    # custo estimado: haiku ~$0.80/1M input, ~$4/1M output (sonnet ~10x mais caro)
    custo = (input_tokens * 0.80 + output_tokens * 4) / 1_000_000
    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO vision_acionado
                (pdf_path, pagina, png_hash, motivo, n_produtos,
                 custo_estimado_usd, input_tokens, output_tokens, criado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(png_hash) DO UPDATE SET
                n_produtos=excluded.n_produtos,
                custo_estimado_usd=excluded.custo_estimado_usd,
                input_tokens=excluded.input_tokens,
                output_tokens=excluded.output_tokens,
                criado_em=excluded.criado_em
            """,
            (
                pdf_path, pagina, png_hash,
                json.dumps(produtos_json, ensure_ascii=False),
                len(produtos_json), custo,
                input_tokens, output_tokens,
                time.time(),
            ),
        )
        conn.commit()
    logger.info(
        "vision cache: pág %d — %d produtos, ~$%.4f USD (%d in / %d out tokens)",
        pagina, len(produtos_json), custo, input_tokens, output_tokens,
    )


def _render_page_png(doc: fitz.Document, page_idx: int, dpi: int = 150) -> bytes:
    """Renderiza uma página do PDF como PNG em memória."""
    page = doc[page_idx]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return pix.tobytes("png")


def _parse_vision_response(text: str) -> list[dict]:
    """Extrai e valida o JSON da resposta do Claude."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        logger.warning("vision_extractor: sem JSON na resposta: %s", text[:200])
        return []
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        logger.warning("vision_extractor: JSON inválido: %s", e)
        return []

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("nome") or "").strip()
        if not nome or len(nome) < 3:
            continue
        preco_raw = item.get("preco")
        try:
            preco = float(preco_raw) if preco_raw is not None else None
        except (TypeError, ValueError):
            preco = None
        # Valida bbox: lista de 4 floats em [0,1]
        bbox_raw = item.get("bbox")
        bbox = None
        if isinstance(bbox_raw, list) and len(bbox_raw) == 4:
            try:
                bbox = [max(0.0, min(1.0, float(v))) for v in bbox_raw]
                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    bbox = None
            except (TypeError, ValueError):
                bbox = None
        result.append({
            "nome": nome,
            "codigo": str(item.get("codigo") or "").strip() or None,
            "preco": preco,
            "marca": str(item.get("marca") or "").strip() or None,
            "bbox": bbox,
        })
    return result


@dataclass
class VisionExtractor:
    """Extrai produtos de páginas via Claude Vision com cache e paralelismo."""

    max_workers: int = 5
    dpi: int = 150
    db_path: Path = DB_PATH

    def __post_init__(self) -> None:
        cfg = load_claude_vision_config()
        if cfg is None:
            raise RuntimeError(
                "ANTHROPIC_API_KEY não configurada. "
                "Vision extractor indisponível."
            )
        self._client = Anthropic(api_key=cfg.api_key)
        self._model = cfg.model
        _init_vision_db(self.db_path)
        logger.info("vision_extractor: modelo=%s", self._model)

    def extract_page(
        self,
        pdf_path: Path,
        page_idx: int,
        png_bytes: bytes,
        motivo_trigger: str,
    ) -> list[dict]:
        """Processa uma página. Usa cache se disponível."""
        h = _png_hash(png_bytes)

        cached = _lookup_cache(h, self.db_path)
        if cached is not None:
            logger.info("vision cache hit: pág %d (%s)", page_idx, h)
            return cached

        img_b64 = base64.standard_b64encode(png_bytes).decode()
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=PROMPT_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": img_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extraia todos os produtos desta página de catálogo.",
                        },
                    ],
                }],
            )
        except Exception as e:
            logger.error("vision_extractor: API error pág %d: %s", page_idx, e)
            return []

        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        produtos = _parse_vision_response(text)

        in_tok = resp.usage.input_tokens if resp.usage else 0
        out_tok = resp.usage.output_tokens if resp.usage else 0
        _save_cache(
            str(pdf_path), page_idx, h, motivo_trigger,
            produtos, in_tok, out_tok, self.db_path,
        )
        return produtos

    def extract_pages(
        self,
        pdf_path: Path,
        paginas: list[tuple[int, str]],
    ) -> dict[int, list[dict]]:
        """Processa múltiplas páginas em paralelo.

        paginas: lista de (page_idx, motivo_trigger)
        Retorna dict {page_idx: [produtos]}
        """
        results: dict[int, list[dict]] = {}
        if not paginas:
            return results

        with fitz.open(pdf_path) as doc:
            pngs: dict[int, bytes] = {
                idx: _render_page_png(doc, idx, self.dpi)
                for idx, _ in paginas
            }

        def _worker(args: tuple[int, str]) -> tuple[int, list[dict]]:
            idx, motivo = args
            return idx, self.extract_page(pdf_path, idx, pngs[idx], motivo)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(_worker, arg): arg[0] for arg in paginas}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    page_idx, produtos = future.result()
                    results[page_idx] = produtos
                except Exception as e:
                    logger.error("vision worker falhou pág %d: %s", idx, e)
                    results[idx] = []

        return results
