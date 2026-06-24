"""Pipeline end-to-end: produto fornecedor → análise → linha de Excel.

Orquestra: cache → matcher → analyzer → margin → score → exporter.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable

from . import cache
from .analyzer import Analyzer, AnaliseConcorrencia
from .config import MarginParams, load_margin_params
from .exporter import LinhaResultado, montar_linha
from .margin import calcular_margem, preco_minimo_para_margem, score_oportunidade
from .matcher import Matcher, MatchResult
from .ml_api import MLClient
from .pdf_parser import ProdutoFornecedor
from .vision_llm import VisionMatcher

logger = logging.getLogger(__name__)


@dataclass
class ResultadoProduto:
    produto: ProdutoFornecedor
    match: MatchResult
    analise: AnaliseConcorrencia | None
    linha_excel: LinhaResultado


class Pipeline:
    def __init__(
        self,
        ml: MLClient | None = None,
        vision: VisionMatcher | None = None,
        margin_params: MarginParams | None = None,
        use_cache: bool = True,
    ) -> None:
        self.ml = ml or MLClient()
        self.vision = vision  # opcional
        self.matcher = Matcher(self.ml, self.vision)
        self.analyzer = Analyzer(self.ml)
        self.params = margin_params or load_margin_params()
        self.use_cache = use_cache

    def processar(self, produto: ProdutoFornecedor) -> ResultadoProduto:
        """Processa um produto end-to-end."""
        match = self._fazer_match(produto)

        if not match.catalog_product_id:
            linha = montar_linha(
                produto=produto,
                analise=None,
                margem_classico=None,
                margem_premium=None,
                margem_minima=self.params.margem_minima,
                score=0.0,
                status_match=match.metodo,
                confianca_match=match.confianca,
            )
            return ResultadoProduto(produto=produto, match=match, analise=None, linha_excel=linha)

        if self.use_cache and match.metodo != "sem_criterio":
            cache.upsert(
                nome=produto.nome,
                catalog_product_id=match.catalog_product_id,
                confianca=match.confianca,
                metodo=match.metodo,
                codigo=produto.codigo,
                confirmado_por="agente",
            )

        analise = self.analyzer.analyze(match.catalog_product_id, catalog_payload=match.candidato_ml)

        pv_alvo = None
        if analise.lider_full:
            pv_alvo = analise.lider_full.preco
        elif analise.lider_geral:
            pv_alvo = analise.lider_geral.preco

        margem_classico = None
        margem_premium = None
        if pv_alvo and produto.preco:
            margem_classico = calcular_margem(pv_alvo, produto.preco, self.params, eh_premium=False)
            margem_premium = calcular_margem(pv_alvo, produto.preco, self.params, eh_premium=True)

        score = 0.0
        if margem_classico:
            score = score_oportunidade(
                margem_pct=margem_classico.margem_pct,
                visitas_30d=analise.visitas_total_30d,
                n_concorrentes=analise.n_concorrentes,
                tem_full=analise.n_full > 0,
            )

        linha = montar_linha(
            produto=produto,
            analise=analise,
            margem_classico=margem_classico,
            margem_premium=margem_premium,
            margem_minima=self.params.margem_minima,
            score=score,
            status_match=match.metodo,
            confianca_match=match.confianca,
        )

        return ResultadoProduto(produto=produto, match=match, analise=analise, linha_excel=linha)

    def _fazer_match(self, produto: ProdutoFornecedor) -> MatchResult:
        if self.use_cache:
            hit = cache.lookup(nome=produto.nome, codigo=produto.codigo)
            if hit:
                cat = self.ml.get_product(hit["catalog_product_id"])
                return MatchResult(
                    catalog_product_id=hit["catalog_product_id"],
                    confianca=hit["confianca"],
                    metodo=f"cache_{hit['metodo']}",
                    candidato_ml=cat,
                    motivo="match recuperado do cache",
                )
        return self.matcher.match(produto)

    def processar_lote(
        self,
        produtos: Iterable[ProdutoFornecedor],
        progress_callback: Callable[[int, int, ProdutoFornecedor, ResultadoProduto], None] | None = None,
    ) -> list[ResultadoProduto]:
        """Processa lote de produtos. progress_callback(i, total, produto, resultado) opcional."""
        produtos_lista = list(produtos)
        total = len(produtos_lista)
        resultados: list[ResultadoProduto] = []
        for i, produto in enumerate(produtos_lista, start=1):
            try:
                res = self.processar(produto)
            except Exception as e:
                logger.exception("erro processando %r: %s", produto.nome, e)
                fallback = MatchResult(
                    catalog_product_id=None,
                    confianca=0.0,
                    metodo="erro",
                    motivo=str(e),
                )
                res = ResultadoProduto(
                    produto=produto,
                    match=fallback,
                    analise=None,
                    linha_excel=montar_linha(
                        produto=produto,
                        analise=None,
                        margem_classico=None,
                        margem_premium=None,
                        margem_minima=self.params.margem_minima,
                        score=0.0,
                        status_match="erro",
                        confianca_match=0.0,
                    ),
                )
            resultados.append(res)
            if progress_callback:
                progress_callback(i, total, produto, res)
        return resultados
