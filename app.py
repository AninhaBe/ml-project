"""Streamlit UI do agente de pesquisa de mercado no Mercado Livre.

Funcionalidades:
- Entrada manual: 1 produto (nome + preço + foto)
- Upload de PDF de fornecedor (em desenvolvimento — usa parser básico)
- Análise em tempo real com barra de progresso
- Fila de REVIEW para matches incertos
- Download do Excel formatado
"""
from __future__ import annotations

import logging
import re as _re
import sys
from pathlib import Path

import fitz as _fitz
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.cache import pdf_produtos_lookup, pdf_produtos_save
from src.config import IMGS_DIR, load_claude_config, load_margin_params
from src.exporter import gerar_excel, nome_excel_default
from src.ml_api import MLClient
from src.pdf_parser import (
    ProdutoFornecedor,
    _detectar_pagina_suspeita,
    _produtos_de_vision,
    carregar_produto_manual,
    extrair_imagens_pdf,
    parse_pdf_auto,
    parse_pdf_catalogo_visual,
    parse_pdf_tabela,
)
from src.pipeline import Pipeline, ResultadoProduto
from src.vision_llm import VisionMatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

st.set_page_config(page_title="Agente ML Market", page_icon="🛒", layout="wide")


@st.cache_resource
def get_pipeline() -> Pipeline:
    ml = MLClient()
    cfg = load_claude_config()
    vision = VisionMatcher(cfg) if cfg else None
    return Pipeline(ml=ml, vision=vision)


def render_resultado(res: ResultadoProduto) -> None:
    """Renderiza um resultado individual em um container."""
    linha = res.linha_excel
    cores = {
        "APROVADO": "🟢",
        "AVALIAR": "🟡",
        "REJEITAR": "🔴",
        "REVIEW": "🔵",
        "DESCARTADO": "⚪",
    }
    icone = cores.get(linha.veredicto, "⚪")
    with st.container(border=True):
        col1, col2 = st.columns([2, 5])
        with col1:
            if res.produto.imagem_path and res.produto.imagem_path.exists():
                st.image(str(res.produto.imagem_path), width=150)
            st.caption(f"**Fornecedor:** {res.produto.nome[:60]}")
            if res.produto.preco:
                st.caption(f"**Custo:** R$ {res.produto.preco:.2f}")
            if res.produto.codigo:
                st.caption(f"**Código:** `{res.produto.codigo}` ")
        with col2:
            st.markdown(f"### {icone} {linha.veredicto} — {linha.catalog_name or 'sem match'}")
            if res.match.catalog_product_id:
                st.markdown(
                    f"**Match:** `{res.match.catalog_product_id}` "
                    f"({res.match.metodo}, conf={res.match.confianca:.0%})"
                )
            if res.analise:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Concorrentes", res.analise.n_concorrentes)
                m2.metric("Nº Full", res.analise.n_full)
                m3.metric(
                    "Líder Full",
                    f"R$ {res.analise.lider_full.preco:.2f}" if res.analise.lider_full else "—",
                )
                m4.metric("Visitas 30d", res.analise.visitas_total_30d)

            if linha.margem_pct is not None:
                margem_pct = linha.margem_pct * 100
                cor = "green" if margem_pct >= 13 else "red"
                st.markdown(
                    f"**Margem (clássico):** :{cor}[{margem_pct:.1f}%] | "
                    f"**Score:** {linha.score_oportunidade:.1f}"
                )

            if res.analise and res.analise.bandeiras:
                st.warning("**Bandeiras:** " + ", ".join(res.analise.bandeiras))
            else:
                st.info(f"Motivo: {res.match.motivo}")


# ============================== UI ==============================
st.title("🛒 Agente de Pesquisa de Mercado — Mercado Livre")
st.caption("Identifica produtos no catálogo do ML, analisa concorrência, calcula margem e ranqueia oportunidades.")

with st.sidebar:
    st.subheader("⚙️ Parâmetros de margem")
    params = load_margin_params()
    st.text(f"Taxa ML clássico: {params.taxa_classico:.1%}")
    st.text(f"Taxa ML premium: {params.taxa_premium:.1%}")
    st.text(f"Alíquota imposto: {params.aliquota_imposto:.1%}")
    st.text(f"Frete fixo: R$ {params.frete_fixo:.2f}")
    st.text(f"Custos extras: R$ {params.custos_extras:.2f}")
    st.text(f"Margem mínima: {params.margem_minima:.1%}")
    st.caption("Ajustar em `.env` ")

    st.divider()
    st.subheader("🔑 Status")
    try:
        get_pipeline()
        st.success("ML API conectada")
    except Exception as e:
        st.error(f"ML API: {e}")
    if load_claude_config():
        st.success("Claude Vision ativo")
    else:
        st.warning("Claude Vision DESATIVADO (configurar ANTHROPIC_API_KEY no .env)")

if "resultados" not in st.session_state:
    st.session_state.resultados = []

# Tabs
tab_manual, tab_pdf, tab_resultados = st.tabs(["📝 Manual (1 produto)", "📄 PDF do fornecedor", "📊 Resultados"])

with tab_manual:
    st.subheader("Análise rápida — 1 produto")
    col1, col2 = st.columns(2)
    with col1:
        nome = st.text_input("Nome do produto (como aparece no fornecedor)", placeholder="Ex: Irrigador bucal")
        preco = st.number_input("Preço de custo (R$)", min_value=0.0, value=0.0, step=1.0)
        codigo = st.text_input("Código/SKU (opcional)", placeholder="Ex: BM-8696")
        foto = st.file_uploader("Foto do produto (do fornecedor)", type=["jpg", "jpeg", "png", "webp"])
    with col2:
        if foto:
            st.image(foto, caption="Preview", use_container_width=True)

    if st.button("🚀 Analisar", type="primary", disabled=not (nome and preco > 0)):
        with st.spinner("Buscando no catálogo, analisando concorrência e calculando margem..."):
            img_bytes = foto.getvalue() if foto else None
            produto = carregar_produto_manual(
                nome=nome,
                preco=preco,
                imagem_bytes=img_bytes,
                codigo=codigo or None,
                out_dir=IMGS_DIR,
            )
            try:
                pipeline = get_pipeline()
                resultado = pipeline.processar(produto)
                st.session_state.resultados.append(resultado)
                render_resultado(resultado)
            except Exception as e:
                st.error(f"Erro: {e}")
                logging.exception("erro no processamento manual")

with tab_pdf:
    st.subheader("Upload de PDF do fornecedor")
    st.caption("Suporta PDFs com tabela e catálogos visuais (como B-TEK). Extrai produtos e imagens para análise com Vision AI.")

    pdf_file = st.file_uploader("Arquivo PDF", type=["pdf"])
    if pdf_file and st.button("📤 Processar PDF", type="primary"):
        pdf_path = ROOT / "sample_pdfs" / pdf_file.name
        pdf_path.write_bytes(pdf_file.getvalue())

        # --- Verifica cache antes de qualquer processamento ---
        from src.cache import pdf_hash as _pdf_hash
        cached_produtos_raw = pdf_produtos_lookup(pdf_path)
        if cached_produtos_raw is not None:
            produtos = [
                ProdutoFornecedor(**{k: v for k, v in p.items() if k != "imagem_path"})
                for p in cached_produtos_raw
            ]
            st.info(f"⚡ Cache: {len(produtos)} produtos carregados instantaneamente (PDF já processado antes).")
            n_suspeitas = 0
        else:
            produtos = None

        bar = st.progress(0, text="📄 Lendo PDF...")
        status = st.empty()
        if cached_produtos_raw is not None:
            bar.empty()

        # Fase 1 — conta páginas (só se não estava em cache)
        if cached_produtos_raw is None:
            with _fitz.open(pdf_path) as _doc:
                n_paginas = len(_doc)
            bar.progress(5, text=f"📄 {n_paginas} páginas detectadas — rodando heurística...")

            # Fase 2 — heurística
            tabela = parse_pdf_tabela(pdf_path)
            bar.progress(30, text="🔍 Heurística concluída — verificando páginas problemáticas...")

            visual = parse_pdf_catalogo_visual(pdf_path)
            bar.progress(55, text="🔍 Parser visual concluído...")

            por_pagina_tab: dict[int, list] = {}
            for p in tabela:
                por_pagina_tab.setdefault(p.pagina, []).append(p)
            por_pagina_vis: dict[int, list] = {}
            for p in visual:
                por_pagina_vis.setdefault(p.pagina, []).append(p)
            todas_paginas = set(por_pagina_tab) | set(por_pagina_vis)
            por_pagina: dict[int, list] = {}
            for pg in todas_paginas:
                t = por_pagina_tab.get(pg, [])
                v = por_pagina_vis.get(pg, [])
                por_pagina[pg] = v if len(v) > len(t) else t

            textos: dict[int, str] = {}
            with _fitz.open(pdf_path) as _doc:
                for pg in todas_paginas:
                    if pg < len(_doc):
                        textos[pg] = _doc[pg].get_text() or ""

            paginas_suspeitas = []
            for pg in sorted(todas_paginas):
                motivo = _detectar_pagina_suspeita(por_pagina.get(pg, []), textos.get(pg, ""))
                if motivo:
                    paginas_suspeitas.append((pg, motivo))

            n_suspeitas = len(paginas_suspeitas)

            # Fase 3 — Vision nas suspeitas
            if n_suspeitas > 0 and load_claude_config():
                bar.progress(60, text=f"🤖 Vision AI em {n_suspeitas} páginas problemáticas (paralelo)...")
                from src.vision_extractor import VisionExtractor
                try:
                    extractor = VisionExtractor()
                    vision_results = extractor.extract_pages(pdf_path, paginas_suspeitas)
                    for pg, items in vision_results.items():
                        if items:
                            por_pagina[pg] = _produtos_de_vision(pg, items)
                except Exception as e:
                    logging.warning("Vision fallback falhou: %s", e)
                    status.warning(f"⚠️ Vision indisponível, mantendo heurística: {e}")
            elif n_suspeitas > 0:
                status.warning(f"⚠️ {n_suspeitas} páginas problemáticas detectadas mas Vision não configurado.")

            bar.progress(75, text="🖼️ Extraindo imagens do PDF...")

            # Flatten
            produtos = []
            for pg in sorted(por_pagina):
                produtos.extend(por_pagina[pg])

            # Fase 4 — imagens
            imgs_dir = IMGS_DIR / pdf_path.stem
            try:
                imagens = extrair_imagens_pdf(pdf_path, imgs_dir)
                imgs_por_pagina: dict[int, list[Path]] = {}
                for img_path in imagens:
                    m = _re.search(r"page(\d+)", img_path.name)
                    if m:
                        imgs_por_pagina.setdefault(int(m.group(1)), []).append(img_path)
                associados = 0
                for p in produtos:
                    imgs = imgs_por_pagina.get(p.pagina, [])
                    if imgs and not p.imagem_path:
                        p.imagem_path = imgs[0]
                        associados += 1
                bar.progress(95, text="✅ Extração concluída!")
                status.info(
                    f"**{len(produtos)} produtos** extraídos de {n_paginas} páginas "
                    f"({n_suspeitas} via Vision AI, {associados} com foto)."
                )
            except Exception as e:
                logging.warning("Erro extraindo imagens: %s", e)
                bar.progress(95, text="✅ Extração concluída (sem imagens).")
                status.info(f"**{len(produtos)} produtos** extraídos ({n_suspeitas} páginas via Vision AI).")

            # Salva no cache para próximas vezes
            pdf_produtos_save(pdf_path, [
                {k: (str(v) if isinstance(v, Path) else v)
                 for k, v in vars(p).items() if k != "extras"}
                for p in produtos
            ])

            bar.empty()

        if produtos:
            progress = st.progress(0.0, text="Analisando produtos...")
            pipeline = get_pipeline()
            resultados_pdf: list[ResultadoProduto] = []

            def cb(i: int, total: int, p: ProdutoFornecedor, r: ResultadoProduto) -> None:
                resultados_pdf.append(r)
                progress.progress(i / total, text=f"[{i}/{total}] {p.nome[:60]}")

            pipeline.processar_lote(produtos, progress_callback=cb)
            st.session_state.resultados.extend(resultados_pdf)
            progress.empty()
            st.rerun()


with tab_resultados:
    _exibir = st.session_state.resultados
    st.subheader(f"📊 Resultados acumulados ({len(_exibir)})")
    if not _exibir:
        st.info("Ainda não há resultados. Faça uma análise na aba Manual ou PDF.")
    else:
        veredictos = [r.linha_excel.veredicto for r in st.session_state.resultados]
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("🟢 Aprovado", veredictos.count("APROVADO"))
        c2.metric("🟡 Avaliar", veredictos.count("AVALIAR"))
        c3.metric("🔴 Rejeitar", veredictos.count("REJEITAR"))
        c4.metric("🔵 Review", veredictos.count("REVIEW"))
        c5.metric("⚪ Descartado", veredictos.count("DESCARTADO"))

        st.divider()
        filtros = st.multiselect(
            "Filtrar por veredicto",
            options=["APROVADO", "AVALIAR", "REJEITAR", "REVIEW", "DESCARTADO"],
            default=["APROVADO", "AVALIAR", "REVIEW"],
        )

        filtrados = [r for r in _exibir if r.linha_excel.veredicto in filtros]
        filtrados.sort(key=lambda r: r.linha_excel.score_oportunidade, reverse=True)

        for r in filtrados:
            render_resultado(r)

        st.divider()
        if st.button("📥 Baixar Excel"):
            linhas = [r.linha_excel for r in st.session_state.resultados]
            out = gerar_excel(linhas, nome_excel_default())
            with open(out, "rb") as f:
                st.download_button(
                    "Baixar arquivo gerado",
                    data=f.read(),
                    file_name=out.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

        if st.button("🗑️ Limpar resultados"):
            st.session_state.resultados = []
            st.rerun()
