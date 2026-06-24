"""Matching em 3 camadas: código exato → fuzzy textual → vision LLM.

Cada camada tem custo crescente. Para por confiança alta na camada mais barata possível.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rapidfuzz import fuzz

from .ml_api import MLClient
from .pdf_parser import ProdutoFornecedor
from .vision_llm import VisionMatcher

logger = logging.getLogger(__name__)

_SUB_STATUS_INVALIDOS = {
    "deleted", "under_review", "freeze", "out_of_stock",
    "manually_paused", "expired", "inactive",
}

# Cache de dados Avantpro coletados pela extensão Chrome (data/avantpro_cache.json)
_AVANTPRO_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "avantpro_cache.json"


@dataclass
class MatchResult:
    catalog_product_id: str | None  # None = nenhum match aceito
    confianca: float  # 0-1
    metodo: str  # "codigo_exato" | "fuzzy_nome" | "vision_llm" | "vision_review" | "nao_encontrado" | "sem_criterio"
    candidato_ml: dict | None = None  # produto completo do ML (se confirmado)
    candidatos_revisao: list[dict] | None = None  # alternativas pra review humano
    motivo: str = ""
    catalogo_disponivel: bool = True  # False quando sem_criterio (indisponível/demanda baixa)


# ----------- estratégias de busca -----------
def gerar_queries(produto: ProdutoFornecedor) -> list[str]:
    """Gera múltiplas queries de busca pra cobrir catálogos com nomes diferentes."""
    queries: list[str] = []
    nome = produto.nome.lower().strip()
    if not nome:
        return queries

    # 1. Nome inteiro
    queries.append(nome)

    # 2. Com código se houver
    if produto.codigo:
        queries.append(produto.codigo)
        queries.append(f"{produto.codigo} {nome[:30]}")
        # variações: BM-8696 → BM8696 / bm8696a
        sem_hifen = produto.codigo.replace("-", "")
        if sem_hifen != produto.codigo:
            queries.append(sem_hifen)

    # 3. Tokens significativos (remove preposições/artigos)
    stopwords = {"com", "de", "do", "da", "para", "e", "ou", "em", "no", "na", "cor"}
    tokens = [t for t in re.findall(r"\w+", nome) if t not in stopwords and len(t) > 2]
    if len(tokens) >= 3:
        queries.append(" ".join(tokens[:5]))

    # dedup preservando ordem
    seen, out = set(), []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def normalizar_codigo(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"[^A-Z0-9]", "", s.upper())


# ----------- camadas -----------
def _match_por_codigo(produto: ProdutoFornecedor, candidatos: list[dict]) -> tuple[dict, float] | None:
    """Camada 1: compara código do fornecedor com atributos MODEL / EAN do ML."""
    if not produto.codigo:
        return None
    cod_fornec = normalizar_codigo(produto.codigo)
    for c in candidatos:
        for attr in c.get("attributes", []):
            if attr.get("id") in ("MODEL", "MODEL_DETALHADO", "EAN", "ALPHANUMERIC_MODEL"):
                v = normalizar_codigo(attr.get("value_name"))
                if v and (v == cod_fornec or v in cod_fornec or cod_fornec in v):
                    return c, 0.99
    return None


def _match_por_texto(produto: ProdutoFornecedor, candidatos: list[dict]) -> tuple[dict, float] | None:
    """Camada 2: fuzzy match no nome. Usa token_sort (penaliza tokens extras) + token_set."""
    if not candidatos:
        return None
    melhor = None
    melhor_score = 0.0
    nome_p = produto.nome.lower()
    for c in candidatos:
        nome_ml = c.get("name", "").lower()
        # token_sort_ratio penaliza quando ML tem muito mais tokens (ex: kit com N itens)
        score_sort = fuzz.token_sort_ratio(nome_p, nome_ml) / 100.0
        score_set = fuzz.token_set_ratio(nome_p, nome_ml) / 100.0
        # média ponderada: set tem peso menor para evitar falsos positivos de substrings
        score = score_sort * 0.6 + score_set * 0.4
        if score > melhor_score:
            melhor_score = score
            melhor = c
    if melhor and melhor_score >= 0.80:
        return melhor, melhor_score
    return None


# ----------- coordenador -----------
class Matcher:
    """Coordena as 3 camadas de matching."""

    def __init__(
        self,
        ml: MLClient,
        vision: VisionMatcher | None = None,
        threshold_auto: float = 0.92,
        threshold_review: float = 0.60,
        max_candidatos_vision: int = 5,
    ) -> None:
        self.ml = ml
        self.vision = vision
        self.threshold_auto = threshold_auto
        self.threshold_review = threshold_review
        self.max_candidatos_vision = max_candidatos_vision
        self._avantpro: dict = {}
        self._avantpro_mtime: float = 0.0

    def _avantpro_dados(self, catalog_id: str) -> dict | None:
        """Lê dados Avantpro do cache em disco (recarrega se o arquivo mudou)."""
        try:
            mtime = _AVANTPRO_CACHE_PATH.stat().st_mtime
            if mtime != self._avantpro_mtime:
                self._avantpro = json.loads(_AVANTPRO_CACHE_PATH.read_text("utf-8"))
                self._avantpro_mtime = mtime
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.warning("Falha ao ler cache Avantpro: %s", e)
            return None
        return self._avantpro.get(catalog_id)

    def _avaliar_catalogo(self, catalog_id: str) -> dict:
        """Coleta métricas do catálogo para scoring e filtragem.

        Critério de catálogo ativo: /products/{id}/items retorna >=1 listing.
        NÃO usar buy_box_winner como filtro — vem null em catálogos saudáveis.
        """
        # 1) Tem listings? Endpoint /items é a verdade.
        try:
            items = self.ml.get_product_items(catalog_id, limit=20)
        except Exception as e:
            logger.warning("get_product_items(%s) failed: %s", catalog_id, e)
            return {"descartado": f"erro_api: {e}", "score": 0}

        if not items:
            # 404 "No winners found" = catálogo sem listings competindo
            return {"descartado": "sem_winners (catálogo sem anúncios elegíveis)", "score": 0}

        # 2) Filtrar listings inválidos — campos podem vir None com este token,
        #    nesse caso assume válido (só 404 acima é sinal confiável de morto)
        ativos = []
        for it in items:
            status = it.get("status")
            qty = it.get("available_quantity")
            sub = it.get("sub_status")
            # Se campos vêm None (token sem permissão), passa; se vêm preenchidos, filtra
            status_ok = status is None or status == "active"
            qty_ok = qty is None or int(qty) > 0
            sub_ok = sub is None or (set(sub) if isinstance(sub, list) else {sub}) - _SUB_STATUS_INVALIDOS
            if status_ok and qty_ok and sub_ok:
                ativos.append(it)

        if not ativos:
            return {"descartado": f"todos {len(items)} listings inválidos", "score": 0}

        # 3) Filtrar loja oficial — também pode vir None, só filtra se preenchido
        ativos_validos = [it for it in ativos if not it.get("official_store_id")]
        if not ativos_validos:
            return {"descartado": f"todos {len(ativos)} ativos são de loja oficial", "score": 0}

        # 4) Visitas 30d (top 5 listings por economia)
        item_ids = [it["item_id"] for it in ativos_validos[:5] if it.get("item_id")]
        visitas_total = 0
        for iid in item_ids:
            try:
                visitas_total += self.ml.get_item_visits_30d(iid)
            except Exception:
                pass

        # 5) Sellers distintos para score e threshold
        sellers = {it.get("seller_id") for it in ativos_validos if it.get("seller_id")}
        n_sellers = len(sellers) if sellers else len(ativos_validos)
        seller_penalty = 0.4 if n_sellers == 1 else 1.0

        # 6) Dados Avantpro — quando existem, são VENDAS REAIS e têm prioridade
        #    sobre a heurística de visitas (que é só um proxy ruidoso).
        av = self._avantpro_dados(catalog_id)
        if av:
            metrics = self._avaliar_com_avantpro(
                catalog_id, av, n_sellers, seller_penalty, visitas_total, ativos_validos
            )
            return metrics

        # 6b) Sem Avantpro: cai na heurística de visitas (proxy de vendas)
        #     Calibrado nos testes: produto bom tem 26-895 visitas, lixo tem 0-6
        min_visitas = 30 if n_sellers == 1 else 15
        if visitas_total < min_visitas:
            return {
                "descartado": f"demanda_baixa (visitas={visitas_total} < {min_visitas}, sellers={n_sellers})",
                "score": 0,
                "visitas_30d": visitas_total,
            }

        # 7) Score composto (sem Avantpro)
        score = visitas_total * seller_penalty + (n_sellers * 10)

        result = {
            "descartado": None,
            "items": ativos_validos,
            "n_sellers": n_sellers,
            "visitas_30d": visitas_total,
            "score": score,
        }
        if n_sellers == 1:
            result["risco_marca"] = True
            logger.debug("catalogo %s: 1 seller — possível marca registrada", catalog_id)

        return result

    # Limiares de demanda real (vendas/mês) baseados nos dados Avantpro
    _AVANTPRO_VENDAS_MIN = 5      # < isso/mês = demanda fraca
    _AVANTPRO_FAT_MENSAL_MIN = 300.0  # faturamento/mês mínimo para ser interessante

    def _avaliar_com_avantpro(
        self, catalog_id, av, n_sellers, seller_penalty, visitas_total, ativos_validos
    ) -> dict:
        """Decide o veredicto usando VENDAS REAIS da Avantpro (prioridade sobre visitas).

        Considera previsibilidade: faturamento alto acumulado em anúncio antigo
        vale menos que faturamento recente. Normaliza pela idade quando possível.
        """
        vendas_mensais = av.get("vendas_mensais")
        vendas_por_dia = av.get("vendas_por_dia")
        faturamento = av.get("faturamento")  # run-rate mensal ("Faturando R$ X")
        anuncio_dias = av.get("anuncio_criado_dias")
        catalogo_dias = av.get("catalogo_criado_dias")

        base = {
            "items": ativos_validos,
            "n_sellers": n_sellers,
            "visitas_30d": visitas_total,
            "fonte_demanda": "avantpro",
            "avantpro": {
                "vendas_mensais": vendas_mensais,
                "vendas_por_dia": vendas_por_dia,
                "faturamento": faturamento,
                "anuncio_criado_dias": anuncio_dias,
                "catalogo_criado_dias": catalogo_dias,
            },
        }
        if n_sellers == 1:
            base["risco_marca"] = True

        # Demanda efetiva em vendas/mês: usa vendas_mensais; se ausente, deriva de vendas_por_dia
        if vendas_mensais is None and vendas_por_dia is not None:
            vendas_mensais = vendas_por_dia * 30
        if vendas_mensais is None:
            # Avantpro sem campo de vendas — não dá pra decidir, usa faturamento como sinal
            if faturamento is not None and faturamento < self._AVANTPRO_FAT_MENSAL_MIN:
                return {**base, "descartado": f"avantpro_faturamento_baixo (R${faturamento:.0f}/mês)", "score": 0}
            score = (faturamento or 0) / 50.0 * seller_penalty + n_sellers * 10
            return {**base, "descartado": None, "score": score}

        # Veredicto por vendas/mês reais
        if vendas_mensais <= 0:
            idade_txt = f", anúncio {anuncio_dias}d" if anuncio_dias else ""
            return {**base, "descartado": f"avantpro_sem_vendas (0/mês{idade_txt})", "score": 0}

        if vendas_mensais < self._AVANTPRO_VENDAS_MIN:
            # Demanda fraca — não descarta de cara, marca para AVALIAR com score baixo
            score = vendas_mensais * 5 * seller_penalty + n_sellers * 5
            return {**base, "descartado": None, "demanda_fraca": True, "score": score}

        # Demanda saudável — score forte baseado em vendas reais + faturamento
        score = vendas_mensais * 10 * seller_penalty + (faturamento or 0) / 50.0 + n_sellers * 10
        return {**base, "descartado": None, "score": score}

    def descobrir_catalogos(
        self,
        produto: ProdutoFornecedor,
        limit_per_query: int = 10,
    ) -> list[str]:
        """Só descobre catalog_ids candidatos (sem avaliar). Usado para enfileirar
        coleta Avantpro antes da pontuação. Barato: apenas search_products."""
        vistos: set[str] = set()
        for q in gerar_queries(produto):
            try:
                results = self.ml.search_products(q, limit=limit_per_query)
            except Exception as e:
                logger.warning("descoberta falhou para %r: %s", q, e)
                continue
            for r in results:
                pid = r.get("id")
                if pid:
                    vistos.add(pid)
        return list(vistos)

    def buscar_candidatos(
        self,
        produto: ProdutoFornecedor,
        limit_per_query: int = 10,
    ) -> list[dict]:
        """Busca e ranqueia candidatos do ML.

        Retorna TODOS os candidatos encontrados, enriquecidos com métricas.
        Candidatos que não passam nos critérios ficam com _aprovado=False e
        _motivo_reprovacao explicando o motivo — mas ainda são retornados
        para exibição e análise de mercado.
        """
        vistos: set[str] = set()
        agregados: list[dict] = []
        for q in gerar_queries(produto):
            try:
                results = self.ml.search_products(q, limit=limit_per_query)
            except Exception as e:
                logger.warning("search failed for %r: %s", q, e)
                continue
            for r in results:
                pid = r.get("id")
                if pid and pid not in vistos:
                    vistos.add(pid)
                    agregados.append(r)

        # Avalia cada catálogo — todos são mantidos, aprovados ou não
        todos: list[dict] = []
        n_aprovados = 0
        for c in agregados:
            metricas = self._avaliar_catalogo(c["id"])
            c["_visitas_30d"] = metricas.get("visitas_30d", 0)
            c["_n_sellers"] = metricas.get("n_sellers", 0)
            c["_score_catalogo"] = metricas.get("score", 0)
            c["_risco_marca"] = metricas.get("risco_marca", False)
            motivo_reprov = metricas.get("descartado")
            c["_aprovado"] = motivo_reprov is None
            c["_motivo_reprovacao"] = motivo_reprov or ""
            if c["_aprovado"]:
                n_aprovados += 1
            todos.append(c)

        # Ordena: aprovados primeiro (por score), depois reprovados (por visitas)
        todos.sort(key=lambda x: (int(x["_aprovado"]), x["_score_catalogo"]), reverse=True)

        logger.info(
            "candidatos para '%s': %d raw, %d aprovados, %d reprovados",
            produto.nome[:40], len(todos), n_aprovados, len(todos) - n_aprovados,
        )
        return todos

    def match(self, produto: ProdutoFornecedor) -> MatchResult:
        todos = self.buscar_candidatos(produto)
        if not todos:
            return MatchResult(
                catalog_product_id=None,
                confianca=0.0,
                metodo="nao_encontrado",
                motivo="busca retornou 0 resultados (provavelmente só anúncio tradicional)",
            )

        # Separa aprovados (passaram nos critérios) dos reprovados
        aprovados = [c for c in todos if c.get("_aprovado")]
        # Melhor candidato reprovado (para exibição mesmo sem aprovados)
        melhor_reprovado = next((c for c in todos if not c.get("_aprovado")), None)

        # ---- CAMADA 1: código exato (só em aprovados) ----
        hit = _match_por_codigo(produto, aprovados) if aprovados else None
        if hit:
            cand, conf = hit
            return MatchResult(
                catalog_product_id=cand["id"],
                confianca=conf,
                metodo="codigo_exato",
                candidato_ml=cand,
                motivo=f"código {produto.codigo} bate com atributo do ML",
            )

        # ---- CAMADA 2: fuzzy texto (só em aprovados) ----
        hit = _match_por_texto(produto, aprovados) if aprovados else None
        if hit:
            cand, conf = hit
            if conf >= self.threshold_auto:
                return MatchResult(
                    catalog_product_id=cand["id"],
                    confianca=conf,
                    metodo="fuzzy_nome",
                    candidato_ml=cand,
                    motivo=f"fuzzy score {conf:.2f}",
                )

        # ---- CAMADA 3: vision LLM ----
        tem_foto = produto.imagem_path and produto.imagem_path.exists()
        if self.vision and tem_foto:
            if aprovados:
                return self._match_visual(produto, aprovados)
            # Sem aprovados: Vision só para identificar o produto, retorna como sem_criterio
            # Nunca passa catálogos mortos (sem_winners) pro Vision
            todos_vivos = [c for c in todos if "sem_winners" not in c.get("_motivo_reprovacao", "")]
            result_visual = self._match_visual(produto, todos_vivos if todos_vivos else todos)
            if result_visual.candidato_ml is not None:
                motivo_reprov = result_visual.candidato_ml.get('_motivo_reprovacao', 'sem_anuncios')
                return MatchResult(
                    catalog_product_id=result_visual.catalog_product_id,
                    confianca=result_visual.confianca,
                    metodo="sem_criterio",
                    candidato_ml=result_visual.candidato_ml,
                    motivo=f"catálogo encontrado mas não recomendado: {motivo_reprov}",
                    catalogo_disponivel=False,
                )
            return result_visual

        # Sem Vision e sem aprovados — retorna melhor reprovado para exibição/análise
        if not aprovados and melhor_reprovado:
            return MatchResult(
                catalog_product_id=melhor_reprovado["id"],
                confianca=0.0,
                metodo="sem_criterio",
                candidato_ml=melhor_reprovado,
                motivo=f"catálogo encontrado mas não recomendado: {melhor_reprovado.get('_motivo_reprovacao', '')}",
                catalogo_disponivel=False,
            )

        # Sem Vision: aceita fuzzy aprovado com confiança reduzida
        if hit:
            cand, conf = hit
            return MatchResult(
                catalog_product_id=cand["id"],
                confianca=conf * 0.8,
                metodo="fuzzy_nome",
                candidato_ml=cand,
                motivo=f"fuzzy score {conf:.2f} (sem Vision disponível)",
            )

        return MatchResult(
            catalog_product_id=None,
            confianca=0.0,
            metodo="nao_encontrado",
            candidatos_revisao=todos[: self.max_candidatos_vision],
            motivo="sem match suficiente",
        )

    def _match_visual(self, produto: ProdutoFornecedor, candidatos: list[dict]) -> MatchResult:
        assert self.vision and produto.imagem_path
        top = candidatos[: self.max_candidatos_vision]
        # baixa primeira foto de cada candidato
        candidatos_fotos: list[bytes] = []
        candidatos_usaveis: list[dict] = []
        for c in top:
            pics = c.get("pictures") or []
            if not pics:
                continue
            url = pics[0].get("url") or pics[0].get("secure_url")
            if not url:
                continue
            try:
                candidatos_fotos.append(self.ml.download_image(url))
                candidatos_usaveis.append(c)
            except Exception as e:
                logger.warning("failed to download %s: %s", url, e)

        if not candidatos_fotos:
            return MatchResult(
                catalog_product_id=None,
                confianca=0.0,
                metodo="nao_encontrado",
                motivo="candidatos sem fotos baixáveis",
            )

        try:
            foto_fornec = produto.imagem_path.read_bytes()
            resp = self.vision.compare(
                foto_fornecedor=foto_fornec,
                candidatos_fotos=candidatos_fotos,
                nome_fornec=produto.nome,
                preco_fornec=produto.preco,
                codigo_fornec=produto.codigo,
            )
        except Exception as e:
            logger.error("vision compare failed: %s", e)
            return MatchResult(
                catalog_product_id=None,
                confianca=0.0,
                metodo="nao_encontrado",
                candidatos_revisao=candidatos_usaveis,
                motivo=f"vision error: {e}",
            )

        # escolhe melhor match com confiança aceitável
        matches = resp.get("matches", [])
        confirmados = [m for m in matches if m.get("match")]
        if not confirmados:
            return MatchResult(
                catalog_product_id=None,
                confianca=0.0,
                metodo="nao_encontrado",
                candidatos_revisao=candidatos_usaveis,
                motivo=f"vision rejeitou todos os {len(candidatos_usaveis)} candidatos",
            )
        melhor = max(confirmados, key=lambda m: m["confianca"])
        idx = int(melhor["candidato"]) - 1
        if not (0 <= idx < len(candidatos_usaveis)):
            return MatchResult(
                catalog_product_id=None,
                confianca=0.0,
                metodo="nao_encontrado",
                candidatos_revisao=candidatos_usaveis,
                motivo="vision retornou índice inválido",
            )
        cand = candidatos_usaveis[idx]
        conf = float(melhor["confianca"])

        if conf >= self.threshold_auto:
            metodo = "vision_llm"
        elif conf >= self.threshold_review:
            metodo = "vision_review"
        else:
            return MatchResult(
                catalog_product_id=None,
                confianca=conf,
                metodo="nao_encontrado",
                candidatos_revisao=candidatos_usaveis,
                motivo=f"vision: confiança {conf:.2f} < {self.threshold_review}",
            )

        # Se o candidato escolhido não passou nos critérios, marca como sem_criterio
        if not cand.get("_aprovado", True):
            motivo_reprov = cand.get('_motivo_reprovacao', 'criterios_nao_atendidos')
            return MatchResult(
                catalog_product_id=cand["id"],
                confianca=conf,
                metodo="sem_criterio",
                candidato_ml=cand,
                motivo=f"catálogo encontrado mas não recomendado: {motivo_reprov}",
                catalogo_disponivel=False,
            )

        return MatchResult(
            catalog_product_id=cand["id"] if metodo == "vision_llm" else None,
            confianca=conf,
            metodo=metodo,
            candidato_ml=cand,
            candidatos_revisao=candidatos_usaveis if metodo == "vision_review" else None,
            motivo=melhor.get("motivo", ""),
        )
