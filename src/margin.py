"""Cálculo de margem de contribuição e score de oportunidade."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import MarginParams


@dataclass
class CalculoMargem:
    preco_venda: float
    custo_produto: float
    taxa_ml: float
    valor_taxa_ml: float
    valor_imposto: float
    frete: float
    custos_extras: float
    receita_liquida: float
    lucro: float
    margem_pct: float
    roi_pct: float
    eh_premium: bool


def calcular_margem(
    preco_venda: float,
    custo_produto: float,
    params: MarginParams,
    eh_premium: bool = False,
) -> CalculoMargem:
    """Calcula margem dado um preço de venda alvo."""
    taxa = params.taxa_premium if eh_premium else params.taxa_classico
    valor_taxa = preco_venda * taxa
    valor_imposto = preco_venda * params.aliquota_imposto
    receita_liquida = preco_venda - valor_taxa - valor_imposto - params.frete_fixo - params.custos_extras
    lucro = receita_liquida - custo_produto
    margem_pct = (lucro / preco_venda) if preco_venda > 0 else 0.0
    roi_pct = (lucro / custo_produto) if custo_produto > 0 else 0.0
    return CalculoMargem(
        preco_venda=preco_venda,
        custo_produto=custo_produto,
        taxa_ml=taxa,
        valor_taxa_ml=valor_taxa,
        valor_imposto=valor_imposto,
        frete=params.frete_fixo,
        custos_extras=params.custos_extras,
        receita_liquida=receita_liquida,
        lucro=lucro,
        margem_pct=margem_pct,
        roi_pct=roi_pct,
        eh_premium=eh_premium,
    )


def preco_minimo_para_margem(
    custo_produto: float,
    params: MarginParams,
    margem_alvo: float | None = None,
    eh_premium: bool = False,
) -> float:
    """Inverte a fórmula: dado custo e margem alvo, qual é o PV mínimo?

    Fórmula:
      Lucro = PV - PV*taxa - PV*imposto - frete - extras - custo
      Margem = Lucro / PV
      Margem * PV = PV * (1 - taxa - imposto) - frete - extras - custo
      PV * (Margem - 1 + taxa + imposto) = -(frete + extras + custo)
      PV * (1 - taxa - imposto - Margem) = frete + extras + custo
      PV = (frete + extras + custo) / (1 - taxa - imposto - Margem)
    """
    margem = margem_alvo if margem_alvo is not None else params.margem_minima
    taxa = params.taxa_premium if eh_premium else params.taxa_classico
    denom = 1 - taxa - params.aliquota_imposto - margem
    if denom <= 0:
        return math.inf
    return (params.frete_fixo + params.custos_extras + custo_produto) / denom


def score_oportunidade(
    margem_pct: float,
    visitas_30d: int,
    n_concorrentes: int,
    tem_full: bool,
) -> float:
    """Score composto (0-100). Maior = melhor.

    Combina margem, demanda (log), concorrência (raiz inversa) e disponibilidade Full.
    """
    if margem_pct <= 0 or n_concorrentes <= 0:
        return 0.0
    base = margem_pct * 100  # margem em pontos percentuais
    fator_demanda = math.log(visitas_30d + 1) / math.log(1000)  # normaliza ~1000 visitas
    fator_concorrencia = 1 / math.sqrt(n_concorrentes)
    fator_full = 1.2 if not tem_full else 1.0  # mercados sem Full são mais oportunos
    return base * fator_demanda * fator_concorrencia * fator_full
