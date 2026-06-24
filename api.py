"""FastAPI backend para o Agente de Pesquisa de Mercado."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Redireciona temp para o mesmo disco do projeto (C: pode estar cheio)
_TMP_DIR = ROOT / "_tmp"
_TMP_DIR.mkdir(exist_ok=True)
tempfile.tempdir = str(_TMP_DIR)

from src.cache import (
    pdf_hash,
    pdf_produtos_lookup,
    pdf_produtos_save,
    pdf_resultados_lookup,
    pdf_resultados_save,
)
from src.config import IMGS_DIR, load_claude_config, load_margin_params
from src.exporter import gerar_excel, nome_excel_default
from src.ml_api import MLClient
from src.pdf_parser import ProdutoFornecedor, parse_pdf_auto
from src.pipeline import Pipeline, ResultadoProduto
from src.vision_llm import VisionMatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="ML Market Agent API")

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.mercadolivre.com.br", "https://mercadolivre.com.br", "*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sessões em memória: job_id → estado
_jobs: dict[str, dict] = {}
_pipelines: dict[str, Pipeline] = {}


def _get_pipeline() -> Pipeline:
    cfg = load_claude_config()
    vision = VisionMatcher(cfg) if cfg else None
    return Pipeline(ml=MLClient(), vision=vision, margin_params=load_margin_params())


def _serializar_resultado(r: ResultadoProduto) -> dict:
    def _conv(obj):
        if hasattr(obj, "__dataclass_fields__"):
            return {k: _conv(v) for k, v in asdict(obj).items()}
        if isinstance(obj, list):
            return [_conv(i) for i in obj]
        if isinstance(obj, dict):
            return {k: _conv(v) for k, v in obj.items()}
        if isinstance(obj, Path):
            return str(obj)
        return obj
    return _conv(r)


def _resultado_para_card(r: ResultadoProduto) -> dict:
    """Versão resumida para exibição em card na UI."""
    le = r.linha_excel
    return {
        "nome": le.produto_fornec_nome,
        "codigo": le.produto_fornec_codigo or "",
        "preco_custo": le.produto_fornec_preco,
        "catalog_id": le.catalog_id or "",
        "catalog_name": le.catalog_name,
        "status_match": le.status_match,
        "confianca": round(le.confianca_match, 2),
        "n_concorrentes": le.n_concorrentes,
        "preco_min": le.preco_min,
        "preco_mediana": le.preco_mediana,
        "preco_max": le.preco_max,
        "margem_pct": round(le.margem_pct * 100, 1) if le.margem_pct else None,
        "margem_pct_premium": round(le.margem_pct_premium * 100, 1) if le.margem_pct_premium else None,
        "pv_alvo": le.pv_alvo,
        "score": round(le.score_oportunidade, 2),
        "veredicto": le.veredicto,
        "bandeiras": le.bandeiras,
        "permalink": le.permalink_lider,
    }


# Cache em memória para dados Avantpro (catalog_id → dados)
_avantpro_cache: dict = {}
_avantpro_cache_path = ROOT / "data" / "avantpro_cache.json"

# Fila de coleta: catalog_id → {url, status, ts}
# status: "pendente" | "coletando" | "feito" | "erro"
_avantpro_fila: dict = {}
# Segundos antes de considerar uma coleta "coletando" travada e reenfileirar
_AVANTPRO_COLETA_TIMEOUT = 45
# Validade do cache: re-coleta se dado for mais velho que isso (horas)
_AVANTPRO_CACHE_VALIDADE_H = 24
# Liga/desliga o pre-pass de coleta automática antes da pontuação
_AVANTPRO_COLETA_ATIVA = True
# Tempo máximo (s) esperando a extensão drenar a fila antes de pontuar
_AVANTPRO_COLETA_DEADLINE = 900
# Janela (s) para detectar se há coletor ativo; se nada for coletado, segue sem Avantpro
_AVANTPRO_GRACE = 30

def _load_avantpro_cache():
    if _avantpro_cache_path.exists():
        try:
            _avantpro_cache.update(json.loads(_avantpro_cache_path.read_text("utf-8")))
            logger.info("Avantpro cache carregado: %d entradas", len(_avantpro_cache))
        except Exception:
            pass

_load_avantpro_cache()


# ── Endpoints ──────────────────────────────────────────────────────────────


@app.get("/")
async def root():
    return FileResponse(ROOT / "static" / "index.html")


@app.post("/api/avantpro-dados")
async def receber_avantpro(request: Request):
    """Recebe dados da extensão Chrome (Avantpro Collector) e cacheia por catalog_id."""
    try:
        dados = await request.json()
    except Exception:
        raise HTTPException(400, "JSON inválido")

    catalog_id = dados.get("catalog_id") or dados.get("item_id")
    if not catalog_id:
        raise HTTPException(400, "catalog_id ou item_id obrigatório")

    _avantpro_cache[catalog_id] = dados
    # Persiste no disco
    try:
        _avantpro_cache_path.parent.mkdir(parents=True, exist_ok=True)
        _avantpro_cache_path.write_text(json.dumps(_avantpro_cache, ensure_ascii=False, indent=2), "utf-8")
    except Exception as e:
        logger.warning("Erro ao salvar avantpro cache: %s", e)

    # Marca item da fila como feito, se existir
    if catalog_id in _avantpro_fila:
        _avantpro_fila[catalog_id]["status"] = "feito"

    logger.info("Avantpro dados recebidos: %s → faturamento=%s vendas=%s visitas=%s",
                catalog_id, dados.get("faturamento"), dados.get("vendas_catalogo"), dados.get("visitas"))
    return {"ok": True, "catalog_id": catalog_id}


@app.get("/api/avantpro-dados/{catalog_id}")
async def consultar_avantpro(catalog_id: str):
    """Retorna dados Avantpro cacheados para um catalog_id."""
    dados = _avantpro_cache.get(catalog_id)
    if not dados:
        raise HTTPException(404, "Sem dados Avantpro para este catálogo")
    return dados


def _cache_recente(catalog_id: str) -> bool:
    """True se já há dado Avantpro fresco o suficiente (dentro da validade)."""
    d = _avantpro_cache.get(catalog_id)
    if not d:
        return False
    ts = d.get("timestamp")
    if not ts:
        return True  # tem dado, sem timestamp → considera válido
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        idade_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return idade_h < _AVANTPRO_CACHE_VALIDADE_H
    except Exception:
        return True


@app.post("/api/avantpro-fila")
async def enfileirar_avantpro(request: Request):
    """Enfileira URLs de catálogos para a extensão coletar.

    Body: {"itens": [{"catalog_id": "MLB123", "url": "https://..."}, ...]}
    Pula itens que já têm cache recente.
    """
    body = await request.json()
    itens = body.get("itens", [])
    enfileirados, pulados = 0, 0
    for it in itens:
        cid = it.get("catalog_id")
        url = it.get("url")
        if not cid or not url:
            continue
        if _cache_recente(cid):
            pulados += 1
            continue
        _avantpro_fila[cid] = {"url": url, "status": "pendente", "ts": time.time()}
        enfileirados += 1
    return {"enfileirados": enfileirados, "pulados_cache": pulados, "total_fila": len(_avantpro_fila)}


@app.get("/api/avantpro-fila/proxima")
async def proxima_coleta():
    """Extensão chama isto para pegar a próxima URL a coletar.

    Reenfileira itens 'coletando' travados além do timeout.
    """
    agora = time.time()
    for cid, item in _avantpro_fila.items():
        if item["status"] == "coletando" and (agora - item["ts"]) > _AVANTPRO_COLETA_TIMEOUT:
            item["status"] = "pendente"  # destravou
    for cid, item in _avantpro_fila.items():
        if item["status"] == "pendente":
            item["status"] = "coletando"
            item["ts"] = agora
            return {"catalog_id": cid, "url": item["url"]}
    return {"catalog_id": None, "url": None}  # fila vazia


@app.get("/api/avantpro-fila/status")
async def status_fila():
    """Resumo do progresso da coleta."""
    from collections import Counter
    contagem = Counter(i["status"] for i in _avantpro_fila.values())
    return {
        "total": len(_avantpro_fila),
        "pendente": contagem.get("pendente", 0),
        "coletando": contagem.get("coletando", 0),
        "feito": contagem.get("feito", 0),
        "erro": contagem.get("erro", 0),
    }


@app.post("/api/avantpro-fila/erro")
async def marcar_erro_coleta(request: Request):
    """Extensão reporta que falhou ao coletar uma URL (ex: Avantpro não carregou)."""
    body = await request.json()
    cid = body.get("catalog_id")
    if cid and cid in _avantpro_fila:
        _avantpro_fila[cid]["status"] = "erro"
    return {"ok": True}


@app.post("/api/upload-pdf")
async def upload_pdf(request: Request):
    """Recebe PDF, extrai produtos (com cache), retorna job_id."""
    import re as _re
    try:
        form = await request.form()
        file = form.get("file")
        if file is None:
            raise HTTPException(400, "Campo 'file' não encontrado no form")
    except Exception as e:
        logger.error("Erro ao parsear form: %s", e)
        raise HTTPException(400, f"Erro ao receber arquivo: {e}")

    filename = getattr(file, "filename", None) or ""
    logger.info("upload recebido: filename=%r", filename)
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, f"Arquivo deve ser PDF (recebido: {filename!r})")

    tmp = Path(tempfile.mkdtemp(dir=str(_TMP_DIR))) / filename
    tmp.write_bytes(await file.read())

    job_id = str(uuid.uuid4())[:8]
    h = pdf_hash(tmp)

    def _prod_to_dict(p):
        d = {}
        for k, v in vars(p).items():
            if isinstance(v, Path):
                d[k] = str(v.resolve())
            elif k == "extras" and isinstance(v, dict):
                d[k] = v
            else:
                d[k] = v
        return d

    _CAMPOS_PRODUTO = {"nome", "preco", "codigo", "marca_extraida", "imagem_path", "pagina", "raw_text", "extras"}

    cached_prods = pdf_produtos_lookup(tmp)
    if cached_prods:
        produtos = []
        for p in cached_prods:
            img_str = p.get("imagem_path")
            img_path = Path(img_str) if img_str and img_str != "None" else None
            if img_path and not img_path.exists():
                img_path = None
            extras = p.get("extras") or {}
            if isinstance(extras, str):
                import json as _json
                try:
                    extras = _json.loads(extras)
                except Exception:
                    extras = {}
            # Converte preco para float (pode vir como string "None" do cache antigo)
            preco_raw = p.get("preco")
            try:
                preco = float(preco_raw) if preco_raw is not None and str(preco_raw) != "None" else None
            except (TypeError, ValueError):
                preco = None
            # Filtra só campos válidos do dataclass
            kwargs = {k: v for k, v in p.items() if k in _CAMPOS_PRODUTO and k not in ("extras", "imagem_path", "preco")}
            kwargs["imagem_path"] = img_path
            kwargs["extras"] = extras
            kwargs["preco"] = preco
            try:
                produtos.append(ProdutoFornecedor(**kwargs))
            except TypeError as e:
                logger.warning("Erro ao restaurar produto do cache: %s | dados: %s", e, list(p.keys()))
                continue

        # Se nenhum produto tem imagem, reextrai do PDF (cache antigo)
        sem_img = sum(1 for p in produtos if p.imagem_path is None)
        if sem_img == len(produtos):
            logger.info("Cache sem imagens — reextraindo imagens do PDF")
            try:
                from src.pdf_parser import _associar_imagens_produtos
                from src.config import IMGS_DIR
                imgs_dir = IMGS_DIR / tmp.stem
                _associar_imagens_produtos(tmp, produtos, imgs_dir)
                # Atualiza cache com imagens
                pdf_produtos_save(tmp, [_prod_to_dict(p) for p in produtos])
            except Exception as e:
                logger.warning("Falha ao reextrair imagens: %s", e)
        _jobs[job_id] = {
            "pdf_path": str(tmp),
            "pdf_hash": h,
            "produtos": produtos,
            "resultados": [],
            "status": "pronto",
            "progresso": 0,
            "total": len(produtos),
            "current": "",
            "cache_hit": True,
        }
        # Verifica se resultados também estão cacheados
        cached_res = pdf_resultados_lookup(tmp)
        com_img_agora = sum(1 for p in produtos if p.imagem_path)
        if cached_res and com_img_agora > 0:
            # Invalida cache de resultados se foi processado sem vision (todos fuzzy_nome)
            usou_vision = any(
                r.get("linha_excel", {}).get("status_match", "") in ("vision_llm", "vision_review")
                for r in cached_res
            )
            if not usou_vision:
                logger.info("Cache de resultados sem Vision — forçando reprocessamento com imagens")
                cached_res = None
        # Sempre descarta cache de resultados — reprocessa com filtros atualizados
        # (available_quantity, novos thresholds, etc.)
        if cached_res:
            logger.info("Cache de resultados descartado — será reprocessado com filtros atuais")
        return {"job_id": job_id, "n_produtos": len(produtos), "cache_hit": True, "hash": h}

    # Extrai produtos do PDF
    try:
        produtos = parse_pdf_auto(tmp)
    except Exception as e:
        raise HTTPException(500, f"Erro ao processar PDF: {e}")

    pdf_produtos_save(tmp, [_prod_to_dict(p) for p in produtos])

    # Remove jobs antigos concluídos (mantém só os 10 mais recentes)
    concluidos = [k for k, v in _jobs.items() if v.get("status") in ("concluido", "cancelado", "erro")]
    for k in concluidos[:-10]:
        _jobs.pop(k, None)

    _jobs[job_id] = {
        "pdf_path": str(tmp),
        "pdf_hash": h,
        "produtos": produtos,
        "resultados": [],
        "status": "pronto",
        "progresso": 0,
        "total": len(produtos),
        "current": "",
        "cache_hit": False,
    }
    return {"job_id": job_id, "n_produtos": len(produtos), "cache_hit": False, "hash": h}


async def _coletar_avantpro_prepass(pipeline, produtos, job):
    """Fase A: faz match completo de cada produto, pega o catálogo vencedor e
    enfileira só ele para a extensão Chrome coletar via abas de fundo.

    O enfileiramento é imediato: a extensão começa a coletar enquanto o match
    do próximo produto ainda roda. O wait loop executa em paralelo.

    Para cedo se: fila esvaziar, deadline estourar, ou não houver coletor ativo.
    """
    loop = asyncio.get_event_loop()
    job["status"] = "coletando_avantpro"
    job["avantpro_fase"] = "match"
    job["avantpro_total"] = len(produtos)
    job["avantpro_match_done"] = 0
    job["avantpro_enfileirados"] = 0
    job["avantpro_pendente"] = 0
    job["avantpro_feito"] = 0

    match_done = asyncio.Event()

    async def _wait_coleta():
        deadline = time.time() + _AVANTPRO_COLETA_DEADLINE
        grace = time.time() + _AVANTPRO_GRACE
        while time.time() < deadline:
            if job.get("cancelado"):
                return
            pend = sum(1 for v in _avantpro_fila.values() if v["status"] in ("pendente", "coletando"))
            feito = sum(1 for v in _avantpro_fila.values() if v["status"] in ("feito", "erro"))
            coletando = sum(1 for v in _avantpro_fila.values() if v["status"] == "coletando")
            job["avantpro_pendente"] = pend
            job["avantpro_feito"] = feito
            if match_done.is_set() and pend == 0:
                break
            if time.time() > grace and feito == 0 and coletando == 0 and match_done.is_set():
                logger.warning("Avantpro: nenhum coletor ativo (Chrome/extensão?) — seguindo sem dados")
                break
            await loop.run_in_executor(None, time.sleep, 2)

    wait_task = asyncio.create_task(_wait_coleta())

    try:
        for idx, p in enumerate(produtos, start=1):
            if job.get("cancelado"):
                break
            try:
                mr = await loop.run_in_executor(None, pipeline.matcher.match, p)
                cid = mr.catalog_product_id
                if cid and not _cache_recente(cid):
                    url = f"https://www.mercadolivre.com.br/p/{cid}"
                    _avantpro_fila[cid] = {"url": url, "status": "pendente", "ts": time.time()}
                    job["avantpro_enfileirados"] += 1
                    logger.info("Avantpro pre-pass [%d/%d]: vencedor %s enfileirado para %s",
                                idx, len(produtos), cid, p.nome[:40])
            except Exception as e:
                logger.warning("Match pre-pass falhou para %s: %s", getattr(p, "nome", "?"), e)
            job["avantpro_match_done"] = idx
    finally:
        match_done.set()

    await wait_task
    job["avantpro_fase"] = "concluida"
    job["avantpro_pendente"] = 0
    logger.info("Avantpro pre-pass concluída: %d/%d coletados",
                job.get("avantpro_feito", 0), job.get("avantpro_enfileirados", 0))


@app.post("/api/analisar/{job_id}")
async def analisar(job_id: str):
    """Inicia análise ML em background para o job."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job não encontrado")
    job = _jobs[job_id]

    if job.get("resultados_cache"):
        job["status"] = "concluido"
        return {"status": "cache", "n": len(job["resultados_cache"])}

    if job["status"] == "processando":
        return {"status": "already_running"}

    # Bloqueia se já tem outro job rodando (evita conflito de recursos)
    em_andamento = [k for k, v in _jobs.items() if v.get("status") == "processando" and k != job_id]
    if em_andamento:
        return {"status": "busy", "msg": f"Outro PDF já está sendo processado ({em_andamento[0]}). Aguarde."}

    job["status"] = "processando"
    job["progresso"] = 0
    job["resultados"] = []
    job["cancelado"] = False

    async def _run():
        pipeline = _get_pipeline()
        produtos = job["produtos"]
        total = len(produtos)

        # ── Fase A: descoberta + coleta Avantpro (abre abas via extensão) ──
        if _AVANTPRO_COLETA_ATIVA:
            try:
                await _coletar_avantpro_prepass(pipeline, produtos, job)
            except Exception as e:
                logger.warning("Pre-pass Avantpro falhou (segue sem): %s", e)
            job["status"] = "processando"

        # ── Fase B: pontuação (agora com Avantpro no cache) ──
        for i, p in enumerate(produtos, start=1):
            if job.get("cancelado"):
                job["status"] = "cancelado"
                logger.info("Job %s cancelado em %d/%d", job_id, i, total)
                return
            try:
                r = await asyncio.get_event_loop().run_in_executor(None, pipeline.processar, p)
            except Exception as e:
                logger.warning("Erro produto %s: %s", p.nome, e)
                continue
            job["resultados"].append(_serializar_resultado(r))
            job["progresso"] = i
            job["current"] = p.nome
        job["status"] = "concluido"
        pdf_resultados_save(Path(job["pdf_path"]), job["resultados"])
        # Limpa arquivo tmp após concluir
        try:
            Path(job["pdf_path"]).unlink(missing_ok=True)
        except Exception:
            pass

    asyncio.create_task(_run())
    return {"status": "started", "total": job["total"]}


@app.post("/api/cancelar/{job_id}")
async def cancelar(job_id: str):
    """Cancela o processamento em andamento."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job não encontrado")
    _jobs[job_id]["cancelado"] = True
    return {"status": "cancelando"}


@app.get("/api/progresso/{job_id}")
async def progresso_sse(job_id: str):
    """SSE stream de progresso — atualiza a cada 500ms enquanto processa."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job não encontrado")

    async def _stream() -> AsyncGenerator[dict, None]:
        last_prog = -1
        last_n = -1
        last_avantpro_fase = None
        last_avantpro_match_done = -1
        last_avantpro_feito = -1
        while True:
            job = _jobs.get(job_id, {})
            prog = job.get("progresso", 0)
            total = job.get("total", 1)
            status = job.get("status", "")
            current = job.get("current", "")
            n_res = len(job.get("resultados", []) or job.get("resultados_cache", []))

            avantpro_changed = (
                job.get("avantpro_fase") != last_avantpro_fase
                or job.get("avantpro_match_done") != last_avantpro_match_done
                or job.get("avantpro_feito") != last_avantpro_feito
            )
            if prog != last_prog or n_res != last_n or avantpro_changed:
                last_prog = prog
                last_n = n_res
                last_avantpro_fase = job.get("avantpro_fase")
                last_avantpro_match_done = job.get("avantpro_match_done")
                last_avantpro_feito = job.get("avantpro_feito")
                yield {
                    "data": json.dumps({
                        "progresso": prog,
                        "total": total,
                        "current": current,
                        "status": status,
                        "n_resultados": n_res,
                        "avantpro_fase": job.get("avantpro_fase", ""),
                        "avantpro_match_done": job.get("avantpro_match_done", 0),
                        "avantpro_total": job.get("avantpro_total", 0),
                        "avantpro_enfileirados": job.get("avantpro_enfileirados", 0),
                        "avantpro_pendente": job.get("avantpro_pendente", 0),
                        "avantpro_feito": job.get("avantpro_feito", 0),
                    })
                }

            if status in ("concluido", "cancelado"):
                break

            await asyncio.sleep(0.3)

    return EventSourceResponse(_stream())


@app.get("/api/resultados/{job_id}")
async def get_resultados(job_id: str, offset: int = 0, limit: int = 50):
    """Retorna resultados parciais ou completos do job."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job não encontrado")
    job = _jobs[job_id]

    # usa cache de resultados se disponível
    fonte = job.get("resultados_cache") or job.get("resultados", [])
    fatia = fonte[offset: offset + limit]

    cards = []
    for idx_abs, r_dict in enumerate(fatia):
        le = r_dict.get("linha_excel", {})
        marg = le.get("margem_pct")
        marg_p = le.get("margem_pct_premium")
        # imagem local do PDF — resolve path relativo contra IMGS_DIR
        img_path_str = (r_dict.get("produto") or {}).get("imagem_path") or ""
        img_local = None
        if img_path_str:
            _p = Path(img_path_str)
            if not _p.exists():
                _p = IMGS_DIR / img_path_str
            if _p.exists():
                import urllib.parse as _up2
                img_local = "/api/imagem-arquivo?path=" + _up2.quote(str(_p), safe="")
        # thumbnail do ML — tenta foto principal do catálogo, fallback para pictures do candidato
        import urllib.parse as _up
        match_dict = r_dict.get("match") or {}
        cand = match_dict.get("candidato_ml") or {}
        thumb_ml = None
        # Foto do catálogo (main_picture ou pictures[0])
        main_pic = cand.get("main_picture") or {}
        url = main_pic.get("url") or main_pic.get("secure_url") or ""
        if not url:
            pics = cand.get("pictures") or []
            url = (pics[0].get("url") or pics[0].get("secure_url") or "") if pics else ""
        if url:
            thumb_ml = "/api/thumbnail-ml?url=" + _up.quote(url, safe="")
        cards.append({
            "nome": le.get("produto_fornec_nome", ""),
            "codigo": le.get("produto_fornec_codigo") or "",
            "preco_custo": le.get("produto_fornec_preco"),
            "catalog_id": le.get("catalog_id") or "",
            "catalog_name": le.get("catalog_name", ""),
            "status_match": le.get("status_match", ""),
            "confianca": round(float(le.get("confianca_match", 0)), 2),
            "n_concorrentes": le.get("n_concorrentes", 0),
            "preco_min": le.get("preco_min", 0),
            "preco_mediana": le.get("preco_mediana", 0),
            "preco_max": le.get("preco_max", 0),
            "margem_pct": round(marg * 100, 1) if marg else None,
            "margem_pct_premium": round(marg_p * 100, 1) if marg_p else None,
            "pv_alvo": le.get("pv_alvo"),
            "score": round(float(le.get("score_oportunidade", 0)), 2),
            "veredicto": le.get("veredicto", ""),
            "bandeiras": le.get("bandeiras", ""),
            "permalink": le.get("permalink_lider", ""),
            "img_local": img_local,
            "thumb_ml": thumb_ml,
        })

    return {
        "total": len(fonte),
        "offset": offset,
        "limit": limit,
        "resultados": cards,
        "status": job.get("status"),
    }


@app.get("/api/imagem-arquivo")
async def imagem_arquivo(path: str, t: str = ""):
    """Serve uma imagem pelo path absoluto (dentro de IMGS_DIR)."""
    # Remove qualquer sufixo de query string que possa ter chegado embutido no path
    path = path.split("?")[0]
    img_path = Path(path)
    logger.info("imagem-arquivo: path=%r exists=%s", path, img_path.exists())
    # Segurança: só serve arquivos dentro de IMGS_DIR
    try:
        img_path.resolve().relative_to(IMGS_DIR.resolve())
    except ValueError:
        raise HTTPException(403, "Acesso negado")
    if not img_path.exists():
        raise HTTPException(404, f"Imagem não encontrada: {path}")
    return FileResponse(str(img_path), media_type="image/png")


@app.get("/api/thumbnail-ml")
async def thumbnail_ml(url: str):
    """Proxy para thumbnails do ML (evita CORS no browser)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            r.raise_for_status()
        from fastapi.responses import Response
        return Response(content=r.content, media_type=r.headers.get("content-type", "image/jpeg"))
    except Exception as e:
        raise HTTPException(502, f"Erro ao buscar thumbnail: {e}")


@app.get("/api/download-excel/{job_id}")
async def download_excel(job_id: str):
    """Gera e retorna Excel com todos os resultados."""
    if job_id not in _jobs:
        raise HTTPException(404, "Job não encontrado")
    job = _jobs[job_id]
    fonte = job.get("resultados_cache") or job.get("resultados", [])
    if not fonte:
        raise HTTPException(400, "Sem resultados para exportar")

    from src.exporter import LinhaResultado
    from src.pipeline import ResultadoProduto
    from src.pdf_parser import ProdutoFornecedor
    from src.matcher import MatchResult

    resultados = []
    for d in fonte:
        le_d = d.get("linha_excel", {})
        try:
            linha = LinhaResultado(**le_d)
        except Exception:
            continue
        p = d.get("produto", {})
        produto = ProdutoFornecedor(nome=p.get("nome", ""), preco=p.get("preco"), codigo=p.get("codigo"))
        match = MatchResult(
            catalog_product_id=le_d.get("catalog_id"),
            confianca=le_d.get("confianca_match", 0),
            metodo=le_d.get("status_match", ""),
        )
        resultados.append(ResultadoProduto(produto=produto, match=match, analise=None, linha_excel=linha))

    pdf_nome = Path(job["pdf_path"]).stem
    excel_path = ROOT / "data" / nome_excel_default(pdf_nome)
    gerar_excel(resultados, excel_path)
    return FileResponse(excel_path, filename=excel_path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/analisar-produto")
async def analisar_produto(body: dict):
    """Analisa 1 produto manual."""
    from src.pdf_parser import carregar_produto_manual
    nome = body.get("nome", "").strip()
    preco = float(body.get("preco", 0))
    codigo = body.get("codigo") or None
    if not nome or preco <= 0:
        raise HTTPException(400, "nome e preco são obrigatórios")

    pipeline = _get_pipeline()
    produto = carregar_produto_manual(nome=nome, preco=preco, codigo=codigo, out_dir=IMGS_DIR)
    r = await asyncio.get_event_loop().run_in_executor(None, pipeline.processar, produto)
    return _resultado_para_card(r)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8501, reload=True)
