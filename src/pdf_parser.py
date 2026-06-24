"""Parser de PDFs de fornecedores.

Extrai produtos com nome, preço, código (se houver) e imagem associada.
Suporta dois fluxos:
  1. PDF "encarte": foto + texto próximos (usa PyMuPDF pra extrair imagens + texto por posição)
  2. PDF "tabela": tabela estruturada com colunas (usa pdfplumber)

Também aceita "entrada manual": foto + nome + preço (sem PDF).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

logger = logging.getLogger(__name__)


@dataclass
class ProdutoFornecedor:
    nome: str
    preco: float | None = None
    codigo: str | None = None  # código do fornecedor (SKU, EAN, modelo)
    marca_extraida: str | None = None  # se o nome contém marca
    imagem_path: Path | None = None  # caminho local da imagem extraída
    pagina: int = 0
    raw_text: str = ""  # texto bruto onde apareceu (debug)
    extras: dict = field(default_factory=dict)


# regex de preço BR: R$ 119,00 / R$ 1.299,90 / 119,00
PRECO_RE = re.compile(r"R?\$?\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})|[0-9]+,[0-9]{2})")
# códigos típicos: BM-IO5307, ABC1234, EAN 13 dígitos
CODIGO_RE = re.compile(r"\b([A-Z]{2,5}-?[A-Z0-9]{3,10}|\d{12,13})\b")


def parse_preco(texto: str) -> float | None:
    m = PRECO_RE.search(texto)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def extrair_codigo(texto: str) -> str | None:
    """Acha código tipo modelo/SKU/EAN no texto."""
    m = CODIGO_RE.search(texto)
    return m.group(1) if m else None


def parse_pdf_tabela(
    path: Path,
    _pdf: "pdfplumber.PDF | None" = None,
) -> list[ProdutoFornecedor]:
    """Fluxo 1: PDF com tabela estruturada (produto, código, preço).

    Heurística simples: pega cada linha não-vazia, tenta extrair preço.
    Funciona pra PDFs onde o produto é uma linha de texto.
    _pdf: objeto pdfplumber já aberto (opcional, para reutilizar entre parsers).
    """
    produtos: list[ProdutoFornecedor] = []
    _close = _pdf is None
    pdf = _pdf or pdfplumber.open(path)
    try:
        for page_idx, page in enumerate(pdf.pages):
            # Primeiro tenta tabelas explícitas
            tabelas = page.extract_tables() or []
            for tabela in tabelas:
                if not tabela or len(tabela) < 2:
                    continue
                header = [(h or "").lower() for h in tabela[0]]
                for row in tabela[1:]:
                    p = _linha_tabela_para_produto(header, row, page_idx)
                    if p:
                        produtos.append(p)
            # Depois tenta linhas de texto solto
            if not tabelas:
                texto = page.extract_text() or ""
                for linha in texto.splitlines():
                    preco = parse_preco(linha)
                    if preco and 1 <= preco <= 100000:
                        nome = PRECO_RE.sub("", linha).strip(" -|\t")
                        if len(nome) >= 5:
                            produtos.append(
                                ProdutoFornecedor(
                                    nome=nome,
                                    preco=preco,
                                    codigo=extrair_codigo(linha),
                                    pagina=page_idx,
                                    raw_text=linha,
                                )
                            )
    finally:
        if _close:
            pdf.close()
    return produtos


def _linha_tabela_para_produto(header: list[str], row: list, page_idx: int) -> ProdutoFornecedor | None:
    if not row:
        return None
    row = [(c or "").strip() for c in row]
    # acha colunas heurísticas
    idx_nome = _achar_coluna(header, ["produto", "descricao", "descrição", "item", "nome"])
    idx_preco = _achar_coluna(header, ["preco", "preço", "valor", "r$"])
    idx_codigo = _achar_coluna(header, ["codigo", "código", "sku", "ean", "ref"])

    nome = row[idx_nome] if idx_nome is not None and idx_nome < len(row) else row[0]
    preco_str = row[idx_preco] if idx_preco is not None and idx_preco < len(row) else " ".join(row)
    codigo = row[idx_codigo] if idx_codigo is not None and idx_codigo < len(row) else None

    preco = parse_preco(preco_str)
    if not nome or len(nome) < 3 or not preco:
        return None
    return ProdutoFornecedor(
        nome=nome,
        preco=preco,
        codigo=codigo or extrair_codigo(nome),
        pagina=page_idx,
        raw_text=" | ".join(row),
    )


def _achar_coluna(header: list[str], chaves: list[str]) -> int | None:
    for i, h in enumerate(header):
        for k in chaves:
            if k in h:
                return i
    return None


# ============= Parser de catálogo visual (encartes) =============

# Preço "artístico" duplicado: "3355" = 35, "RR$$" = R$, ",,0000" = ,00
PRECO_DUP_RE = re.compile(r"((?:\d\d){1,4})\s*\n?\s*RR\$\$\s*,,0000")
# Códigos B-TEK / DZ típicos (incluindo duplicados como DDZZ--5522)
CODIGO_CATALOGO_RE = re.compile(
    r"\b(BT-\d{3,5}|DZ-\d{2,4}|DDZZ?--\d{2,6})\b", re.IGNORECASE
)
# Preço normal simples (valores entre 1 e 100000)
PRECO_SIMPLES_RE = re.compile(r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\b")
# Frases de marketing/seção que nunca são nomes de produto
MARKETING_BLACKLIST = {
    "enquanto durar o estoque",
    "promoções tempo limitado",
    "promocoes tempo limitado",
    "novidades hype",
    "promoções limitado",
    "promocoes limitado",
    "tempo limitado",
    "novidades",
    "em promoção",
    "em promocao",
    "oferta",
    "oferta especial",
    "destaque",
    "lançamento",
    "lancamento",
    "back to home",
    "voltar ao início",
    "sumário",
    "sumario",
    "catálogo",
    "catalogo",
}

# Padrões de linhas que são meta-informação, nunca produto
LIXO_RE = re.compile(
    r"^("
    r"(unid\.?cx[: ]+){2,}"           # UNID.CX: UNID.CX:
    r"|pcs\/cx\s*:\s*\d+.*pcs\/cx"   # PCS/CX: 50 R$ ... PCS/CX:
    r"|@[\w]+"                          # @handle redes sociais
    r"|www\."
    r"|acesse"
    r"|funcionamos"
    r")",
    re.IGNORECASE,
)


def _is_lixo_marketing(texto: str) -> bool:
    """Retorna True se o texto é frase de marketing/seção, não produto."""
    t = texto.strip().lower()
    # Blacklist exata
    if t in MARKETING_BLACKLIST:
        return True
    # Prefixo de blacklist (ex: "promoções tempo limitado - confira")
    for frase in MARKETING_BLACKLIST:
        if t.startswith(frase):
            return True
    # Padrões regex de lixo
    if LIXO_RE.match(texto.strip()):
        return True
    return False


# Palavras que indicam categoria/seção (não são nomes de produto)
SECAO_KEYWORDS = {
    "ferramentas", "elétricas", "manuais", "baterias", "recarregáveis",
    "soquetes", "magnético", "abraçadeira", "rosqueável", "pistolas",
    "pintura", "acabamento", "tesouras", "estiletes", "corte", "serras",
    "martelos", "alicates", "desencapadores", "kits", "chaves", "catraca",
    "medidores", "testes", "trenas", "fitas", "adesivas", "rádios",
    "comunicadores", "automotiva", "bombas", "infladores", "industrial",
    "maçaricos", "instrumentos", "ópticos", "organizadores", "diversos",
    "fixadores", "enforca-gato", "organizador", "fios", "multi-cabeças",
    "2026", "back to home", "linha", "sumário", "catálogo", "introdução",
    "promoção", "relâmpago", "suporte",
}


def _dedup_price(s: str) -> float:
    """Decodifica preço duplicado: '3355' -> 35.0, '229988' -> 298.0."""
    chars = list(s)
    result = []
    for i in range(0, len(chars), 2):
        result.append(chars[i])
    return float("".join(result))


def _clean_code(s: str) -> str:
    """Limpa código duplicado: 'DDZZ--5522' -> 'DZ-52', 'DDZ--78' -> 'DZ-78'."""
    s = s.upper().strip()
    s = re.sub(r"^DD?ZZ?--", "DZ-", s)
    # Remove dígitos duplicados no código: DZ-5522 -> DZ-52
    parts = s.split("-", 1)
    if len(parts) == 2 and len(parts[1]) >= 4 and len(parts[1]) % 2 == 0:
        digits = parts[1]
        # Verifica se os dígitos estão realmente duplicados (cada par igual)
        is_dup = all(digits[i] == digits[i + 1] for i in range(0, len(digits), 2))
        if is_dup:
            deduped = "".join(digits[i] for i in range(0, len(digits), 2))
            s = f"{parts[0]}-{deduped}"
    return s


def _is_section_header(text: str) -> bool:
    """Verifica se o texto é um cabeçalho de seção (não um produto)."""
    if _is_lixo_marketing(text):
        return True
    words = set(text.lower().split())
    return len(words) > 0 and words.issubset(SECAO_KEYWORDS | {"de", "e", "para"})


def _extract_product_blocks_fitz(path: Path) -> list[dict]:
    """Usa PyMuPDF para extrair blocos de texto com posição (x, y).

    Agrupa blocos próximos verticalmente em 'colunas' de produto.
    """
    blocks_by_page = []
    with fitz.open(path) as doc:
        for page_idx, page in enumerate(doc):
            text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            page_blocks = []
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:  # só texto
                    continue
                text_parts = []
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        t = span.get("text", "").strip()
                        if t:
                            text_parts.append(t)
                text = " ".join(text_parts)
                if text:
                    bbox = block.get("bbox", (0, 0, 0, 0))
                    page_blocks.append({
                        "text": text,
                        "x": bbox[0],
                        "y": bbox[1],
                        "x1": bbox[2],
                        "y1": bbox[3],
                        "page": page_idx,
                    })
            blocks_by_page.append(page_blocks)
    return blocks_by_page


def parse_pdf_catalogo_visual(
    path: Path,
    _pdf: "pdfplumber.PDF | None" = None,
) -> list[ProdutoFornecedor]:
    """Parser para catálogos visuais/encartes (como B-TEK).

    Suporta dois sub-formatos:
      A) Página com 1 produto: código no topo, nome abaixo, preço artístico duplicado
      B) Página com múltiplos produtos em grade: códigos + preços normais

    _pdf: objeto pdfplumber já aberto (opcional, para reutilizar entre parsers).
    """
    produtos: list[ProdutoFornecedor] = []
    _close = _pdf is None
    pdf = _pdf or pdfplumber.open(path)
    try:
        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if not text.strip() or len(text.strip()) < 10:
                continue

            # ---- Formato A: preço artístico duplicado (1 produto por página) ----
            matches_dup = PRECO_DUP_RE.findall(text)
            if matches_dup:
                preco = _dedup_price(matches_dup[0])
                if preco < 1 or preco > 100000:
                    continue

                codigos = CODIGO_CATALOGO_RE.findall(text)
                codigo = _clean_code(codigos[0]) if codigos else None

                # Nome: pega linhas significativas (não seção, não código, não preço)
                linhas = [l.strip() for l in text.splitlines() if l.strip()]
                nome_parts = []
                for l in linhas:
                    l_clean = l.strip()
                    if not l_clean or len(l_clean) < 3:
                        continue
                    if _is_section_header(l_clean):
                        continue
                    if CODIGO_CATALOGO_RE.match(l_clean):
                        continue
                    if PRECO_DUP_RE.search(l_clean):
                        continue
                    if re.match(r"^\d+$", l_clean):  # só números
                        continue
                    if re.match(r"^(RR\$\$|PPeeças|PPCCTTSS|CCXX|Back to Home)", l_clean):
                        continue
                    if re.match(r"^\d+PCS", l_clean):
                        continue
                    if re.match(r"^\d+ [Pp]e[çc]as?$", l_clean):
                        continue
                    nome_parts.append(l_clean)
                    if len(nome_parts) >= 2:
                        break

                nome = " ".join(nome_parts).strip() if nome_parts else ""
                if codigo and not nome:
                    nome = codigo

                if nome and preco:
                    produtos.append(ProdutoFornecedor(
                        nome=nome,
                        preco=preco,
                        codigo=codigo,
                        marca_extraida="B-TEK" if codigo and codigo.startswith("BT-") else None,
                        pagina=page_idx,
                        raw_text=text[:200],
                    ))
                continue

            # ---- Formato B: múltiplos produtos com preço normal ----
            codigos_raw = CODIGO_CATALOGO_RE.findall(text)
            codigos = [_clean_code(c) for c in codigos_raw]
            precos_raw = PRECO_SIMPLES_RE.findall(text)
            precos = []
            for p in precos_raw:
                val = float(p.replace(".", "").replace(",", "."))
                if 1 <= val <= 100000:
                    precos.append(val)

            if not precos:
                continue

            # Extrai nomes de produtos das linhas
            linhas = [l.strip() for l in text.splitlines() if l.strip()]
            nomes_candidatos = []
            for l in linhas:
                l_clean = l.strip()
                if not l_clean or len(l_clean) < 4:
                    continue
                if _is_section_header(l_clean):
                    continue
                if PRECO_SIMPLES_RE.match(l_clean):
                    continue
                if re.match(r"^\d+$", l_clean):
                    continue
                if re.match(r"^(Back to Home|Voltagem|Torque|Rotação|Mandril|Impactos|Frequência|Energia|Profundidade|Diâmetro)", l_clean, re.IGNORECASE):
                    continue
                if re.match(r"^\d+\s*(baterias?|PCS|CX)", l_clean, re.IGNORECASE):
                    continue
                nomes_candidatos.append(l_clean)

            # Se temos códigos, emparelha código → preço
            if codigos and precos:
                for idx, codigo in enumerate(codigos):
                    preco = precos[idx] if idx < len(precos) else precos[-1]
                    # Tenta achar nome próximo
                    nome = codigo  # fallback
                    for nc in nomes_candidatos:
                        if codigo.lower() in nc.lower():
                            continue  # pula se é o próprio código
                        nome = nc
                        break

                    produtos.append(ProdutoFornecedor(
                        nome=f"{nome} {codigo}".strip(),
                        preco=preco,
                        codigo=codigo,
                        marca_extraida="B-TEK" if codigo.startswith("BT-") else None,
                        pagina=page_idx,
                        raw_text=text[:200],
                    ))
            else:
                # Sem códigos: cria produtos genéricos por preço
                categoria = ""
                for l in linhas[:3]:
                    if not _is_section_header(l) and len(l) > 3:
                        categoria = l
                        break

                for idx, preco in enumerate(precos):
                    nome = nomes_candidatos[idx] if idx < len(nomes_candidatos) else categoria
                    if not nome:
                        nome = f"Produto pág.{page_idx + 1} item {idx + 1}"

                    produtos.append(ProdutoFornecedor(
                        nome=nome,
                        preco=preco,
                        pagina=page_idx,
                        raw_text=text[:200],
                    ))

    finally:
        if _close:
            pdf.close()
    logger.info("catalogo visual: extraídos %d produtos de %s", len(produtos), path.name)
    return produtos


_CODIGO_FINAL_RE = re.compile(r"[A-Z]{2,4}-[A-Z0-9]+$")


def _detectar_pagina_suspeita(
    produtos_pagina: list[ProdutoFornecedor],
    texto_pagina: str,
) -> str | None:
    """Verifica 5 sinais de página problemática. Retorna motivo ou None.

    Sinais:
      1. Nome concatenado: len > 50 e código no final do nome
      2. n_precos > 1.5x n_produtos extraídos
      3. n_codigos_texto > 1.5x n_produtos extraídos
      4. 0 produtos mas texto + preço presentes
      5. Todos os nomes < 15 chars (fragmentos)
    """
    n = len(produtos_pagina)

    # Sinal 4: sem produtos mas há texto e preço
    tem_preco = bool(PRECO_SIMPLES_RE.search(texto_pagina))
    if n == 0 and len(texto_pagina.strip()) > 30 and tem_preco:
        return "sem_produtos_com_texto_preco"

    if n == 0:
        return None

    # Sinal 1: nome concatenado (> 50 chars com código no final)
    concatenados = sum(
        1 for p in produtos_pagina
        if len(p.nome) > 50 and _CODIGO_FINAL_RE.search(p.nome)
    )
    if concatenados > 0:
        return f"nome_concatenado({concatenados}/{n})"

    # Sinal 2: n_precos >> n_produtos
    n_precos = len(PRECO_SIMPLES_RE.findall(texto_pagina))
    if n_precos > n * 1.5:
        return f"excesso_precos({n_precos}precos/{n}produtos)"

    # Sinal 3: n_codigos >> n_produtos
    n_codigos = len(CODIGO_CATALOGO_RE.findall(texto_pagina))
    if n_codigos > n * 1.5:
        return f"excesso_codigos({n_codigos}cod/{n}produtos)"

    # Sinal 5: todos os nomes são fragmentos
    if n > 0 and all(len(p.nome) < 15 for p in produtos_pagina):
        return f"nomes_fragmentados(max={max(len(p.nome) for p in produtos_pagina)})"

    return None


def _produtos_de_vision(
    page_idx: int,
    items: list[dict],
) -> list[ProdutoFornecedor]:
    """Converte resultado do VisionExtractor para ProdutoFornecedor."""
    resultado = []
    for item in items:
        nome = item.get("nome") or ""
        preco_raw = item.get("preco")
        try:
            preco = float(preco_raw) if preco_raw is not None else None
        except (TypeError, ValueError):
            preco = None
        bbox = item.get("bbox")  # [x0,y0,x1,y1] em fração 0-1, ou None
        resultado.append(ProdutoFornecedor(
            nome=nome,
            preco=preco,
            codigo=item.get("codigo"),
            marca_extraida=item.get("marca"),
            pagina=page_idx,
            raw_text=f"vision_page{page_idx}",
            extras={"bbox": bbox} if bbox else {},
        ))
    return resultado


def parse_pdf_auto(
    path: Path,
    use_vision_fallback: bool = True,
) -> list[ProdutoFornecedor]:
    """Vision LLM primeiro em todas as páginas; heurística como fallback.

    Fluxo:
      1. Tenta VisionExtractor em todas as páginas (paralelo, com cache)
      2. Para páginas sem resultado do Vision (ou Vision indisponível),
         usa heurística (tabela + visual) como fallback
      3. Associa imagens do PDF a cada produto extraído
    """
    n_pages: int
    with fitz.open(str(path)) as doc:
        n_pages = len(doc)

    por_pagina: dict[int, list[ProdutoFornecedor]] = {}

    # ── Tenta Vision em todas as páginas ──────────────────────────────────
    vision_ok = False
    if use_vision_fallback:
        try:
            from .vision_extractor import VisionExtractor
            extractor = VisionExtractor()
            todas = [(pg, "all_pages") for pg in range(n_pages)]
            vision_results = extractor.extract_pages(path, todas)
            for pg, items in vision_results.items():
                if items:
                    por_pagina[pg] = _produtos_de_vision(pg, items)
            vision_ok = True
            logger.info(
                "parse_pdf_auto: Vision processou %d páginas, %d com produtos",
                n_pages, len(por_pagina),
            )
        except RuntimeError as e:
            logger.warning("Vision indisponível, usando heurística: %s", e)
        except Exception as e:
            logger.error("Vision falhou, usando heurística: %s", e)

    # ── Fallback heurístico para páginas sem resultado ──────────────────
    paginas_sem_vision = [
        pg for pg in range(n_pages)
        if not por_pagina.get(pg)
    ]
    if paginas_sem_vision:
        logger.info(
            "parse_pdf_auto: heurística em %d páginas sem Vision",
            len(paginas_sem_vision),
        )
        with pdfplumber.open(path) as _pdf:
            tabela_total = parse_pdf_tabela(path, _pdf=_pdf)
            visual_total = parse_pdf_catalogo_visual(path, _pdf=_pdf)

        por_pagina_tab: dict[int, list[ProdutoFornecedor]] = {}
        for p in tabela_total:
            if p.pagina in paginas_sem_vision:
                por_pagina_tab.setdefault(p.pagina, []).append(p)

        por_pagina_vis: dict[int, list[ProdutoFornecedor]] = {}
        for p in visual_total:
            if p.pagina in paginas_sem_vision:
                por_pagina_vis.setdefault(p.pagina, []).append(p)

        for pg in paginas_sem_vision:
            t = por_pagina_tab.get(pg, [])
            v = por_pagina_vis.get(pg, [])
            merged = v if len(v) > len(t) else t
            if merged:
                por_pagina[pg] = merged

    # Flatten ordenado por página
    resultado: list[ProdutoFornecedor] = []
    for pg in sorted(por_pagina):
        resultado.extend(por_pagina[pg])

    # Associa imagem recortada a cada produto usando bbox do Vision
    try:
        from .config import IMGS_DIR
        import unicodedata, re as _re
        safe_stem = unicodedata.normalize("NFD", path.stem)
        safe_stem = "".join(c for c in safe_stem if unicodedata.category(c) != "Mn")
        safe_stem = _re.sub(r'[^\w\s\-]', '', safe_stem).strip()
        imgs_dir = IMGS_DIR / safe_stem
        imgs_dir.mkdir(parents=True, exist_ok=True)
        paginas_sem_img = {p.pagina for p in resultado if p.imagem_path is None}
        if paginas_sem_img:
            with fitz.open(str(path)) as doc:
                for pg_idx in paginas_sem_img:
                    if pg_idx >= len(doc):
                        continue
                    page = doc[pg_idx]
                    pw = page.rect.width
                    ph = page.rect.height
                    # Renderiza página em alta res para recortes
                    mat = fitz.Matrix(2.0, 2.0)  # 144 dpi
                    pix_full = page.get_pixmap(matrix=mat, alpha=False)
                    page_png = imgs_dir / f"page_{pg_idx:03d}_full.png"
                    pix_full.save(str(page_png))
                    prods_da_pagina = [p for p in resultado if p.pagina == pg_idx and p.imagem_path is None]
                    for prod_i, p in enumerate(prods_da_pagina):
                        bbox = (p.extras or {}).get("bbox") if p.extras else None
                        if bbox:
                            # Recorta usando bbox (coordenadas em fração)
                            x0 = bbox[0] * pw
                            y0 = bbox[1] * ph
                            x1 = bbox[2] * pw
                            y1 = bbox[3] * ph
                            clip = fitz.Rect(x0, y0, x1, y1)
                            pix_crop = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
                            crop_png = imgs_dir / f"page_{pg_idx:03d}_prod{prod_i:02d}.png"
                            pix_crop.save(str(crop_png))
                            p.imagem_path = crop_png
                        else:
                            # Sem bbox — usa página inteira como fallback
                            p.imagem_path = page_png
        com_img = sum(1 for p in resultado if p.imagem_path)
        logger.info("parse_pdf_auto: %d/%d produtos com imagem", com_img, len(resultado))
    except Exception as e:
        logger.warning("parse_pdf_auto: erro associando imagens: %s", e)

    logger.info("parse_pdf_auto: %d produtos totais de %s", len(resultado), path.name)
    return resultado


def _associar_imagens_produtos(
    path: Path,
    produtos: list[ProdutoFornecedor],
    out_dir: Path,
    min_px: int = 50,
) -> None:
    """Associa a imagem mais próxima (acima) de cada produto na página.

    Para cada produto sem imagem_path:
      1. Pega todas as imagens da página com bounding box
      2. Filtra imagens maiores que min_px em cada dimensão (descarta ícones/logos)
      3. Escolhe a imagem cujo centro-X mais se alinha com o produto
         e que está posicionada acima ou no mesmo nível do texto
      4. Salva em out_dir e seta produto.imagem_path
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Agrupa produtos por página
    por_pagina: dict[int, list[ProdutoFornecedor]] = {}
    for p in produtos:
        if p.imagem_path is None:
            por_pagina.setdefault(p.pagina, []).append(p)

    if not por_pagina:
        return

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        logger.warning("_associar_imagens: erro abrindo PDF: %s", e)
        return

    with doc:
        for page_idx, prods in por_pagina.items():
            if page_idx >= len(doc):
                continue
            page = doc[page_idx]

            # Extrai imagens com posição
            imgs_info: list[dict] = []
            for img_idx, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                # Pega bounding box da imagem na página
                rects = page.get_image_rects(xref)
                if not rects:
                    continue
                rect = rects[0]
                w = rect.width
                h = rect.height
                if w < min_px or h < min_px:
                    continue  # descarta ícones pequenos
                imgs_info.append({
                    "xref": xref,
                    "idx": img_idx,
                    "x0": rect.x0, "y0": rect.y0,
                    "x1": rect.x1, "y1": rect.y1,
                    "cx": (rect.x0 + rect.x1) / 2,
                    "cy": (rect.y0 + rect.y1) / 2,
                    "area": w * h,
                })

            if not imgs_info:
                continue

            # Para cada produto, acha a imagem mais próxima acima dele
            # Como não temos Y do produto (parser não guarda), usamos ordem:
            # ordenamos imagens por Y e produtos por ordem de aparecimento,
            # e associamos 1:1 por posição relativa
            imgs_sorted = sorted(imgs_info, key=lambda i: (i["cy"], i["cx"]))

            # Se há 1 imagem grande por produto (caso típico de catálogo visual)
            # distribui sequencialmente
            if len(imgs_sorted) >= len(prods):
                # Pega as N maiores imagens (N = qtd produtos)
                imgs_by_area = sorted(imgs_info, key=lambda i: -i["area"])
                imgs_para_usar = sorted(imgs_by_area[:len(prods)], key=lambda i: (i["cy"], i["cx"]))
            else:
                imgs_para_usar = imgs_sorted

            for prod_idx, prod in enumerate(prods):
                if prod_idx >= len(imgs_para_usar):
                    # Reutiliza última imagem disponível
                    img_info = imgs_para_usar[-1]
                else:
                    img_info = imgs_para_usar[prod_idx]

                out_path = out_dir / f"p{page_idx:03d}_prod{prod_idx:02d}.png"
                if out_path.exists():
                    prod.imagem_path = out_path
                    continue
                try:
                    pix = fitz.Pixmap(doc, img_info["xref"])
                    if pix.n - pix.alpha > 3:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    pix.save(str(out_path))
                    prod.imagem_path = out_path
                    pix = None
                except Exception as e:
                    logger.warning("_associar_imagens: erro salvando img pág %d: %s", page_idx, e)

    logger.info(
        "_associar_imagens: %d produtos com imagem associada",
        sum(1 for p in produtos if p.imagem_path),
    )


def extrair_imagens_pdf(path: Path, out_dir: Path) -> list[Path]:
    """Extrai todas as imagens do PDF para arquivos individuais. Útil pra encartes.

    Retorna lista de paths das imagens extraídas, ordenadas por página.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with fitz.open(path) as doc:
        for page_idx, page in enumerate(doc):
            for img_idx, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha > 3:  # CMYK → RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                out = out_dir / f"page{page_idx:03d}_img{img_idx:03d}.png"
                pix.save(out)
                paths.append(out)
                pix = None
    logger.info("extracted %d images from %s", len(paths), path.name)
    return paths


def carregar_produto_manual(
    nome: str,
    preco: float,
    imagem_bytes: bytes | None = None,
    codigo: str | None = None,
    out_dir: Path | None = None,
) -> ProdutoFornecedor:
    """Entrada manual: usuário cola nome + preço + imagem (sem PDF)."""
    img_path = None
    if imagem_bytes and out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        img_path = out_dir / "manual_input.png"
        img_path.write_bytes(imagem_bytes)
    return ProdutoFornecedor(
        nome=nome,
        preco=preco,
        codigo=codigo or extrair_codigo(nome),
        imagem_path=img_path,
        raw_text=nome,
    )
