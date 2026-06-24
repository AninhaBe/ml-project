"""Análise de concorrência de um produto de catálogo confirmado.

Coleta: lista de anúncios, líder Full, faixa de preços, reputação dos sellers,
visitas (proxy de demanda) e bandeiras de risco.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from statistics import median
from typing import Iterable

from .ml_api import MLClient

logger = logging.getLogger(__name__)


@dataclass
class AnuncioConcorrente:
    item_id: str
    seller_id: int
    seller_nickname: str = ""
    seller_level: str | None = None
    seller_transacoes: int | None = None
    preco: float = 0.0
    logistic_type: str = ""
    is_full: bool = False
    free_shipping: bool = False
    listing_type_id: str = ""
    official_store_id: int | None = None
    condition: str = ""
    visitas_30d: int | None = None


@dataclass
class AnaliseConcorrencia:
    catalog_product_id: str
    catalog_name: str = ""
    catalog_brand: str | None = None
    n_concorrentes: int = 0
    preco_min: float = 0.0
    preco_max: float = 0.0
    preco_mediana: float = 0.0
    distrib_logistica: dict = field(default_factory=dict)
    n_full: int = 0
    lider_full: AnuncioConcorrente | None = None
    lider_geral: AnuncioConcorrente | None = None
    visitas_total_30d: int = 0
    anuncios: list[AnuncioConcorrente] = field(default_factory=list)
    bandeiras: list[str] = field(default_factory=list)
    catalogo_fantasma: bool = False


class Analyzer:
    """Coleta dados de concorrência de um produto de catálogo."""

    def __init__(
        self,
        ml: MLClient,
        max_visitas_queries: int = 5,
        enriquecer_sellers: bool = True,
    ) -> None:
        self.ml = ml
        self.max_visitas_queries = max_visitas_queries
        self.enriquecer_sellers = enriquecer_sellers

    def analyze(self, catalog_product_id: str, catalog_payload: dict | None = None) -> AnaliseConcorrencia:
        # 1. detalhes do catálogo (nome, marca)
        catalogo = catalog_payload or self.ml.get_product(catalog_product_id) or {}
        catalog_name = catalogo.get("name", "")
        brand = next(
            (a["value_name"] for a in catalogo.get("attributes", []) if a.get("id") == "BRAND"),
            None,
        )

        # 2. lista de anúncios competindo
        items = self.ml.get_product_items(catalog_product_id, limit=50)
        analise = AnaliseConcorrencia(
            catalog_product_id=catalog_product_id,
            catalog_name=catalog_name,
            catalog_brand=brand,
        )

        if not items:
            analise.catalogo_fantasma = True
            analise.bandeiras.append("catalogo_sem_anuncios")
            return analise

        # 3. estrutura dos anúncios
        anuncios = [self._item_to_anuncio(it) for it in items]
        precos = [a.preco for a in anuncios if a.preco > 0]
        analise.n_concorrentes = len(anuncios)
        analise.preco_min = min(precos) if precos else 0.0
        analise.preco_max = max(precos) if precos else 0.0
        analise.preco_mediana = median(precos) if precos else 0.0

        distrib: dict[str, int] = {}
        for a in anuncios:
            distrib[a.logistic_type] = distrib.get(a.logistic_type, 0) + 1
        analise.distrib_logistica = distrib

        fulls = [a for a in anuncios if a.is_full]
        analise.n_full = len(fulls)

        # 4. líderes (menor preço)
        anuncios_ordenados = sorted(anuncios, key=lambda a: a.preco)
        analise.lider_geral = anuncios_ordenados[0] if anuncios_ordenados else None
        fulls_ordenados = sorted(fulls, key=lambda a: a.preco)
        analise.lider_full = fulls_ordenados[0] if fulls_ordenados else None

        # 5. enriquecer sellers (líder geral e líder Full)
        sellers_para_enriquecer = set()
        if analise.lider_geral:
            sellers_para_enriquecer.add(analise.lider_geral.seller_id)
        if analise.lider_full:
            sellers_para_enriquecer.add(analise.lider_full.seller_id)
        if self.enriquecer_sellers:
            for sid in sellers_para_enriquecer:
                self._enriquecer_seller_em_anuncios(anuncios, sid)

        # 6. visitas (proxy de demanda) — limita queries
        ids_visitas = [a.item_id for a in anuncios_ordenados[: self.max_visitas_queries]]
        total_visitas = 0
        for item_id in ids_visitas:
            try:
                v = self.ml.get_item_visits_30d(item_id)
                for a in anuncios:
                    if a.item_id == item_id:
                        a.visitas_30d = v
                        break
                total_visitas += v
            except Exception as e:
                logger.warning("visits failed for %s: %s", item_id, e)
        analise.visitas_total_30d = total_visitas

        analise.anuncios = anuncios

        # 7. bandeiras
        analise.bandeiras = self._detectar_bandeiras(analise)
        return analise

    @staticmethod
    def _item_to_anuncio(it: dict) -> AnuncioConcorrente:
        shipping = it.get("shipping") or {}
        logistic = shipping.get("logistic_type", "")
        return AnuncioConcorrente(
            item_id=it.get("item_id", ""),
            seller_id=int(it.get("seller_id", 0)),
            preco=float(it.get("price") or 0),
            logistic_type=logistic,
            is_full=logistic == "fulfillment",
            free_shipping=bool(shipping.get("free_shipping")),
            listing_type_id=it.get("listing_type_id", ""),
            official_store_id=it.get("official_store_id"),
            condition=it.get("condition", ""),
        )

    def _enriquecer_seller_em_anuncios(self, anuncios: list[AnuncioConcorrente], seller_id: int) -> None:
        try:
            user = self.ml.get_user(seller_id)
        except Exception as e:
            logger.warning("get_user(%s) failed: %s", seller_id, e)
            return
        if not user:
            return
        rep = user.get("seller_reputation") or {}
        for a in anuncios:
            if a.seller_id == seller_id:
                a.seller_nickname = user.get("nickname", "")
                a.seller_level = rep.get("level_id")
                a.seller_transacoes = (rep.get("transactions") or {}).get("total")

    @staticmethod
    def _detectar_bandeiras(an: AnaliseConcorrencia) -> list[str]:
        bandeiras: list[str] = []
        # Marca vende direto (bandeira vermelha forte)
        if an.catalog_brand and an.lider_full:
            nick = (an.lider_full.seller_nickname or "").upper()
            marca = an.catalog_brand.upper().replace(" ", "")
            if marca and (marca in nick.replace("_", "").replace(" ", "")):
                bandeiras.append("marca_vende_direto")
        # Mercado saturado
        if an.n_concorrentes >= 10:
            bandeiras.append("mercado_saturado")
        # Demanda baixa
        if an.visitas_total_30d < 50 and an.n_concorrentes > 0:
            bandeiras.append("demanda_baixa")
        # Demanda alta (positivo)
        if an.visitas_total_30d > 1000:
            bandeiras.append("demanda_alta")
        # Sem Full = oportunidade de entrar com Full
        if an.n_concorrentes >= 3 and an.n_full == 0:
            bandeiras.append("oportunidade_full")
        # Apenas Full disponível (mercado dominado por Full)
        if an.n_full > 0 and an.n_full == an.n_concorrentes:
            bandeiras.append("mercado_full_only")
        # Seller único = possível marca registrada ou exclusividade
        if an.n_concorrentes == 1:
            bandeiras.append("seller_unico")
        return bandeiras
