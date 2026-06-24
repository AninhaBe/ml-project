"""Cliente HTTP para API do Mercado Livre.

Faz refresh automático do access_token usando refresh_token (válido 6 meses).
Aplica retry com backoff exponencial em erros transitórios.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import TOKEN_CACHE, MLConfig, load_ml_config

logger = logging.getLogger(__name__)

OAUTH_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"


@dataclass
class Token:
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds

    def is_expired(self, skew_seconds: int = 60) -> bool:
        return time.time() + skew_seconds >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Token":
        return cls(
            access_token=d["access_token"],
            refresh_token=d["refresh_token"],
            expires_at=d["expires_at"],
        )


class MLClient:
    """Cliente do Mercado Livre com refresh automático e retry.

    Uso:
        client = MLClient()
        product = client.get(f"/products/{catalog_id}")
        items = client.get(f"/products/{catalog_id}/items", params={"limit": 50})
    """

    def __init__(self, cfg: MLConfig | None = None, token_path: Path = TOKEN_CACHE) -> None:
        self.cfg = cfg or load_ml_config()
        self.token_path = token_path
        self._token: Token | None = None
        self._session = requests.Session()
        self._load_token()

    # ----------- token lifecycle -----------
    def _load_token(self) -> None:
        if self.token_path.exists():
            try:
                self._token = Token.from_dict(json.loads(self.token_path.read_text()))
                logger.info("token: loaded from cache (expires_at=%s)", self._token.expires_at)
            except Exception:
                logger.warning("token: cache corrupted, will refresh")
                self._token = None

    def _save_token(self) -> None:
        assert self._token is not None
        self.token_path.write_text(json.dumps(self._token.to_dict(), indent=2))

    def _refresh(self) -> None:
        rt = self._token.refresh_token if self._token else self.cfg.refresh_token
        if not rt:
            raise RuntimeError(
                "Sem refresh_token. Configure ML_REFRESH_TOKEN no .env "
                "(rode scripts/oauth_bootstrap.py se ainda não tem)."
            )
        logger.info("token: refreshing")
        resp = requests.post(
            OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
                "refresh_token": rt,
            },
            headers={"accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = Token(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", rt),
            expires_at=time.time() + data.get("expires_in", 21600) - 60,
        )
        self._save_token()
        logger.info("token: refreshed (expires in %s s)", data.get("expires_in"))

    def _ensure_token(self) -> str:
        if self._token is None or self._token.is_expired():
            self._refresh()
        assert self._token is not None
        return self._token.access_token

    # ----------- HTTP -----------
    @retry(
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        token = self._ensure_token()
        url = path if path.startswith("http") else f"{self.cfg.api_base}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")
        resp = self._session.request(method, url, headers=headers, timeout=30, **kwargs)
        # 401 → refresh e retry uma vez
        if resp.status_code == 401:
            logger.warning("HTTP 401, forcing refresh and retrying once")
            self._refresh()
            headers["Authorization"] = f"Bearer {self._token.access_token}"  # type: ignore[union-attr]
            resp = self._session.request(method, url, headers=headers, timeout=30, **kwargs)
        return resp

    def get(self, path: str, params: dict | None = None, raise_404: bool = False) -> dict | None:
        resp = self._request("GET", path, params=params)
        if resp.status_code == 404:
            if raise_404:
                resp.raise_for_status()
            return None
        if resp.status_code == 403:
            logger.debug("403 on %s", path)
            return None
        resp.raise_for_status()
        return resp.json()

    # ----------- endpoints especializados -----------
    def search_products(self, query: str, limit: int = 10) -> list[dict]:
        """GET /products/search?status=active&site_id=MLB&q=...&limit=..."""
        data = self.get(
            "/products/search",
            params={"status": "active", "site_id": self.cfg.site_id, "q": query, "limit": limit},
        )
        return (data or {}).get("results", [])

    def get_catalog_sold_quantity(self, catalog_id: str) -> int:
        """Retorna total de vendas do catálogo via /products/{id}/items."""
        items = self.get_product_items(catalog_id, limit=50)
        return sum(int(it.get("sold_quantity") or 0) for it in items)

    def catalog_tem_anuncios_ativos(self, catalog_id: str) -> bool:
        """Verifica se o catálogo tem pelo menos 1 anúncio ativo com estoque."""
        items = self.get_product_items(catalog_id, limit=10)
        return any(
            it.get("status") == "active" and int(it.get("available_quantity") or 0) > 0
            for it in items
        )

    def get_product(self, catalog_id: str) -> dict | None:
        return self.get(f"/products/{catalog_id}")

    def get_product_items(self, catalog_id: str, limit: int = 50) -> list[dict]:
        """GET /products/{id}/items?limit=N. Retorna [] se 404 (catálogo fantasma)."""
        data = self.get(f"/products/{catalog_id}/items", params={"limit": limit})
        return (data or {}).get("results", [])

    def get_user(self, seller_id: int) -> dict | None:
        return self.get(f"/users/{seller_id}")

    def get_item_visits_30d(self, item_id: str) -> int:
        """Total de visitas dos últimos 30 dias."""
        data = self.get(f"/items/{item_id}/visits/time_window", params={"last": 30, "unit": "day"})
        return int((data or {}).get("total_visits", 0))

    def download_image(self, url: str) -> bytes:
        """Baixa uma imagem (não passa token; URLs do mlstatic são públicas)."""
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
