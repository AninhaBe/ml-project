"""Bootstrap do OAuth do Mercado Livre.

Uso:
    python scripts/oauth_bootstrap.py

Fluxo:
1. Gera code_verifier + code_challenge (PKCE obrigatório no ML).
2. Abre URL de autorização no navegador.
3. Você autoriza e copia o ?code=... da URL final.
4. Cola aqui no terminal.
5. Script troca code por access_token + refresh_token e salva em data/.access_token.json.
6. Imprime o refresh_token pra você colar no .env (ML_REFRESH_TOKEN).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

CLIENT_ID = os.environ["ML_CLIENT_ID"]
CLIENT_SECRET = os.environ["ML_CLIENT_SECRET"]
REDIRECT_URI = os.environ["ML_REDIRECT_URI"]

TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
TOKEN_CACHE = ROOT / "data" / ".access_token.json"
TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)


def gerar_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def montar_url_autorizacao(challenge: str) -> str:
    params = (
        f"response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={quote(REDIRECT_URI, safe='')}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
    )
    return f"{AUTH_URL}?{params}"


def trocar_code_por_token(code: str, verifier: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        headers={"accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    print("=" * 70)
    print("Bootstrap OAuth do Mercado Livre (PKCE)")
    print("=" * 70)

    verifier, challenge = gerar_pkce()
    url = montar_url_autorizacao(challenge)

    print("\n1. Vou abrir esta URL no seu navegador. Faça login no ML e autorize a app.")
    print(f"\n  {url}\n")
    print("2. Após autorizar, você será redirecionado para uma URL que termina com")
    print("   ?code=TG-xxxxxxxx-yyyyyyyy")
    print("   Mesmo que a página dê erro (ex: webhook.site), o que importa é a URL.")
    print("\n3. COPIE o valor do parâmetro 'code' (só o que vem depois de '?code=')\n")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    code = input("Cole o code aqui e pressione ENTER: ").strip()
    if code.startswith("?code="):
        code = code[len("?code="):]
    if "&" in code:
        code = code.split("&", 1)[0]
    if not code:
        print("ERRO: code vazio")
        return 1

    print("\nTrocando code por access_token...")
    try:
        data = trocar_code_por_token(code, verifier)
    except requests.HTTPError as e:
        print(f"ERRO HTTP: {e.response.status_code}")
        print(e.response.text)
        return 1

    access_token = data["access_token"]
    refresh_token = data["refresh_token"]
    expires_in = data.get("expires_in", 21600)

    TOKEN_CACHE.write_text(
        json.dumps(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": time.time() + expires_in - 60,
            },
            indent=2,
        )
    )

    print("\n" + "=" * 70)
    print("SUCESSO!")
    print("=" * 70)
    print(f"  user_id      : {data.get('user_id')}")
    print(f"  expires_in   : {expires_in} segundos")
    print(f"  scope        : {data.get('scope', '')[:80]}...")
    print(f"\n  access_token : {access_token[:30]}... (cacheado em {TOKEN_CACHE})")
    print(f"\n  refresh_token: {refresh_token}")
    print("\nADICIONE ESTA LINHA NO SEU .env:")
    print(f"\n  ML_REFRESH_TOKEN={refresh_token}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
