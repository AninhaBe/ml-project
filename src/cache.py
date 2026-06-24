"""Cache de matches em SQLite.

Lembra associações fornecedor → catalog_product_id confirmadas.
Na próxima execução do mesmo SKU/nome, pula a etapa cara de matching.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS product_match (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fornecedor TEXT,
    codigo_fornecedor TEXT,
    nome_fornecedor TEXT NOT NULL,
    catalog_product_id TEXT NOT NULL,
    confianca REAL NOT NULL,
    metodo TEXT NOT NULL,
    confirmado_por TEXT,
    criado_em REAL NOT NULL,
    UNIQUE (fornecedor, codigo_fornecedor, nome_fornecedor)
);
CREATE INDEX IF NOT EXISTS idx_match_nome ON product_match(nome_fornecedor);
CREATE INDEX IF NOT EXISTS idx_match_codigo ON product_match(codigo_fornecedor);

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

CREATE TABLE IF NOT EXISTS pdf_produtos_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_hash TEXT NOT NULL UNIQUE,
    pdf_nome TEXT NOT NULL,
    produtos_json TEXT NOT NULL,
    n_produtos INTEGER NOT NULL,
    criado_em REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pdf_hash ON pdf_produtos_cache(pdf_hash);

CREATE TABLE IF NOT EXISTS pdf_resultados_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_hash TEXT NOT NULL UNIQUE,
    pdf_nome TEXT NOT NULL,
    resultados_json TEXT NOT NULL,
    n_resultados INTEGER NOT NULL,
    criado_em REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pdf_resultados_hash ON pdf_resultados_cache(pdf_hash);
"""


@contextmanager
def get_conn(path: Path = DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(path: Path = DB_PATH) -> None:
    with get_conn(path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def lookup(
    nome: str,
    codigo: str | None = None,
    fornecedor: str | None = None,
) -> dict | None:
    """Busca match por código (preferencial) ou nome."""
    init_db()
    with get_conn() as conn:
        if codigo:
            row = conn.execute(
                "SELECT * FROM product_match WHERE codigo_fornecedor = ? ORDER BY criado_em DESC LIMIT 1",
                (codigo,),
            ).fetchone()
            if row:
                return dict(row)
        row = conn.execute(
            "SELECT * FROM product_match WHERE nome_fornecedor = ? ORDER BY criado_em DESC LIMIT 1",
            (nome,),
        ).fetchone()
        return dict(row) if row else None


def upsert(
    nome: str,
    catalog_product_id: str,
    confianca: float,
    metodo: str,
    codigo: str | None = None,
    fornecedor: str | None = None,
    confirmado_por: str | None = None,
) -> None:
    """Insere ou atualiza um match no cache."""
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO product_match
                (fornecedor, codigo_fornecedor, nome_fornecedor, catalog_product_id,
                 confianca, metodo, confirmado_por, criado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fornecedor, codigo_fornecedor, nome_fornecedor)
            DO UPDATE SET
                catalog_product_id=excluded.catalog_product_id,
                confianca=excluded.confianca,
                metodo=excluded.metodo,
                confirmado_por=excluded.confirmado_por,
                criado_em=excluded.criado_em
            """,
            (fornecedor, codigo, nome, catalog_product_id, confianca, metodo, confirmado_por, time.time()),
        )
        conn.commit()


def pdf_hash(pdf_path: Path) -> str:
    """Calcula SHA-256 dos primeiros 2MB + tamanho do PDF (rápido e suficiente)."""
    h = hashlib.sha256()
    h.update(str(pdf_path.stat().st_size).encode())
    with open(pdf_path, "rb") as f:
        h.update(f.read(2 * 1024 * 1024))
    return h.hexdigest()[:24]


def pdf_produtos_lookup(pdf_path: Path, path: Path = DB_PATH) -> list[dict] | None:
    """Retorna produtos cacheados para este PDF, ou None se não houver."""
    init_db(path)
    h = pdf_hash(pdf_path)
    with get_conn(path) as conn:
        row = conn.execute(
            "SELECT produtos_json FROM pdf_produtos_cache WHERE pdf_hash = ? LIMIT 1",
            (h,),
        ).fetchone()
        if row:
            return json.loads(row["produtos_json"])
    return None


def pdf_produtos_save(
    pdf_path: Path,
    produtos: list[dict],
    path: Path = DB_PATH,
) -> None:
    """Salva lista de produtos extraídos para este PDF no cache."""
    init_db(path)
    h = pdf_hash(pdf_path)
    with get_conn(path) as conn:
        conn.execute(
            """
            INSERT INTO pdf_produtos_cache (pdf_hash, pdf_nome, produtos_json, n_produtos, criado_em)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(pdf_hash) DO UPDATE SET
                produtos_json=excluded.produtos_json,
                n_produtos=excluded.n_produtos,
                criado_em=excluded.criado_em
            """,
            (h, pdf_path.name, json.dumps(produtos, ensure_ascii=False), len(produtos), time.time()),
        )
        conn.commit()


def pdf_resultados_lookup(pdf_path: Path, path: Path = DB_PATH) -> list[dict] | None:
    """Retorna resultados de análise cacheados para este PDF, ou None se não houver."""
    init_db(path)
    h = pdf_hash(pdf_path)
    with get_conn(path) as conn:
        row = conn.execute(
            "SELECT resultados_json FROM pdf_resultados_cache WHERE pdf_hash = ? LIMIT 1",
            (h,),
        ).fetchone()
        if row:
            return json.loads(row["resultados_json"])
    return None


def pdf_resultados_save(
    pdf_path: Path,
    resultados: list[dict],
    path: Path = DB_PATH,
) -> None:
    """Salva resultados de análise ML para este PDF no cache."""
    init_db(path)
    h = pdf_hash(pdf_path)
    with get_conn(path) as conn:
        conn.execute(
            """
            INSERT INTO pdf_resultados_cache (pdf_hash, pdf_nome, resultados_json, n_resultados, criado_em)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(pdf_hash) DO UPDATE SET
                resultados_json=excluded.resultados_json,
                n_resultados=excluded.n_resultados,
                criado_em=excluded.criado_em
            """,
            (h, pdf_path.name, json.dumps(resultados, ensure_ascii=False), len(resultados), time.time()),
        )
        conn.commit()


def vision_stats(path: Path = DB_PATH) -> dict:
    """Retorna estatísticas de uso do Vision extractor."""
    init_db(path)
    with get_conn(path) as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total_paginas,
                SUM(n_produtos) as total_produtos,
                SUM(custo_estimado_usd) as custo_total_usd,
                SUM(input_tokens) as input_tokens_total,
                SUM(output_tokens) as output_tokens_total
            FROM vision_acionado
        """).fetchone()
        return dict(row) if row else {}


def vision_log_query(
    pdf_path: str | None = None,
    limit: int = 50,
    path: Path = DB_PATH,
) -> list[dict]:
    """Lista registros de vision_acionado, opcionalmente filtrado por PDF."""
    init_db(path)
    with get_conn(path) as conn:
        if pdf_path:
            rows = conn.execute(
                """SELECT pdf_path, pagina, motivo, n_produtos,
                          custo_estimado_usd, input_tokens, output_tokens, criado_em
                   FROM vision_acionado WHERE pdf_path = ?
                   ORDER BY pagina LIMIT ?""",
                (pdf_path, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT pdf_path, pagina, motivo, n_produtos,
                          custo_estimado_usd, input_tokens, output_tokens, criado_em
                   FROM vision_acionado
                   ORDER BY criado_em DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
