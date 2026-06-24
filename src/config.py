"""Configuração centralizada. Carrega variáveis de ambiente do .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class MLConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    refresh_token: str
    api_base: str = "https://api.mercadolibre.com"
    site_id: str = "MLB"


@dataclass(frozen=True)
class ClaudeConfig:
    api_key: str
    model: str = "claude-sonnet-4-5"
    max_tokens: int = 1024


@dataclass(frozen=True)
class MarginParams:
    taxa_classico: float
    taxa_premium: float
    aliquota_imposto: float
    frete_fixo: float
    custos_extras: float
    margem_minima: float


def load_ml_config() -> MLConfig:
    return MLConfig(
        client_id=os.environ["ML_CLIENT_ID"],
        client_secret=os.environ["ML_CLIENT_SECRET"],
        redirect_uri=os.environ["ML_REDIRECT_URI"],
        refresh_token=os.environ.get("ML_REFRESH_TOKEN", ""),
    )


def load_claude_config() -> ClaudeConfig | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    return ClaudeConfig(api_key=key)


def load_claude_vision_config() -> ClaudeConfig | None:
    """Config separada para Vision extractor de catálogos.

    Usa CLAUDE_VISION_MODEL (padrão: claude-haiku-3-5) — ~10x mais barato
    que Sonnet para extração estruturada simples.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    model = os.environ.get("CLAUDE_VISION_MODEL", "claude-haiku-4-5")
    return ClaudeConfig(api_key=key, model=model)


def load_margin_params() -> MarginParams:
    return MarginParams(
        taxa_classico=float(os.environ.get("TAXA_ML_CLASSICO", 0.115)),
        taxa_premium=float(os.environ.get("TAXA_ML_PREMIUM", 0.165)),
        aliquota_imposto=float(os.environ.get("ALIQUOTA_IMPOSTO", 0.06)),
        frete_fixo=float(os.environ.get("FRETE_FIXO", 20.0)),
        custos_extras=float(os.environ.get("CUSTOS_EXTRAS", 2.0)),
        margem_minima=float(os.environ.get("MARGEM_MINIMA", 0.13)),
    )


DATA_DIR = ROOT / "data"
OUTPUTS_DIR = ROOT / "outputs"
IMGS_DIR = DATA_DIR / "imgs"
DB_PATH = DATA_DIR / "matches.db"
TOKEN_CACHE = DATA_DIR / ".access_token.json"

for d in (DATA_DIR, OUTPUTS_DIR, IMGS_DIR):
    d.mkdir(parents=True, exist_ok=True)
