# 📦 Guia Completo de Instalação — Agente ML Market

> 📌 **Nota:** Este documento contém código de referência e instruções de instalação.
> Não inclui credenciais reais — todos os valores sensíveis foram substituídos por placeholders
> `<entre_colchetes>` que você preenche localmente após gerar suas próprias credenciais.

> **Como usar:** Este documento contém TUDO que você precisa pra recriar o agente no seu PC pessoal.
> Siga os passos em ordem. Cada arquivo de código tem um título com o caminho exato (`src/xxx.py`) —
> crie a pasta correspondente e cole o conteúdo do bloco abaixo.

---

## 🎯 O que esse agente faz

Lê catálogos de fornecedores (PDF ou entrada manual), identifica cada produto no catálogo do Mercado Livre,
analisa concorrência (preço, Full, vendas), calcula margem de contribuição e ranqueia oportunidades
acima de um threshold de margem mínima.

**Stack:** Python 3.10+, Streamlit (UI), API do Mercado Livre (OAuth), Claude Sonnet Vision (matching visual).

**Arquitetura em camadas:**

```
Streamlit (app.py)
Upload PDF / Entrada manual / Resultados
        ↓
Pipeline (src/pipeline.py)
Orquestra: cache → match → análise → margem
        ↓        ↓        ↓         ↓         ↓
  cache    matcher  analyzer  margin   exporter
  SQLite   3 cama-  concorr.  + score  Excel
           das      + Full
        ↓        ↓
  vision_llm   ml_api
  Claude       HTTP+
  Vision       refresh
        ↓
  api.mercadolibre.com
```

---

## 📋 Pré-requisitos

Antes de começar, garanta que tem:

### 1. Python 3.10+

```bash
python3 --version
```

Se estiver abaixo de 3.10, instale:
- **Mac:** `brew install python@3.11`
- **Windows:** baixe de https://www.python.org/downloads/
- **Linux:** `sudo apt install python3.11 python3.11-venv`

### 2. Conta no DevCenter do Mercado Livre

Acesse https://developers.mercadolivre.com.br/devcenter/apps e crie uma aplicação. Você vai precisar de:

- **App ID** (client_id)
- **Secret Key** (client_secret)
- **Redirect URI** (qualquer URL pública; sugestão: `https://webhook.site/SEU-UUID`)

> Se você não tem domínio próprio, use `https://webhook.site/`: gera um UUID pra você usar como redirect URI.

### 3. API Key da Anthropic (Claude Vision)

1. Acesse https://console.anthropic.com/
2. Crie conta (US$ 5 grátis no signup)
3. **Settings → API Keys → Create Key**
4. Salve a chave (formato `<CHAVE_DA_ANTHROPIC>`)

---

## 🏗️ Passo 1 — Criar a estrutura de pastas

Abra um terminal e rode:

```bash
mkdir -p ~/ml_market_agent/{src,scripts,data,outputs,sample_pdfs}
cd ~/ml_market_agent
touch src/__init__.py
```

Estrutura final esperada:

```
ml_market_agent/
├── app.py                # Streamlit UI
├── requirements.txt
├── .env                  # credenciais (você cria)
├── .env.example
├── .gitignore
├── README.md
├── scripts/
│   └── oauth_bootstrap.py  # gera refresh_token (rodar UMA vez)
└── src/
    ├── __init__.py
    ├── config.py
    ├── ml_api.py
    ├── pdf_parser.py
    ├── vision_llm.py
    ├── matcher.py
    ├── analyzer.py
    ├── margin.py
    ├── cache.py
    ├── exporter.py
    └── pipeline.py
├── data/                 # cache + imagens (gerado em runtime)
├── outputs/              # Excels gerados
└── sample_pdfs/          # seus PDFs de teste
```

---

## 🏗️ Passo 2 — Criar arquivos de configuração

### Arquivo: `.gitignore`

```gitignore
# Credenciais
.env
*.token.json
.pkce.txt

# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/
.pytest_cache/

# Dados gerados
data/*.db
data/imgs/
data/.access_token.json
outputs/*.xlsx

# IDE
.vscode/
.idea/
.DS_Store
```

### Arquivo: `.env.example`

```env
# Mercado Livre OAuth
ML_CLIENT_ID=seu_app_id_aqui
ML_CLIENT_SECRET=<sua_chave_secreta_do_devcenter>
ML_REDIRECT_URI=https://webhook.site/SEU-UUID
# Será preenchido após rodar scripts/oauth_bootstrap.py
ML_REFRESH_TOKEN=

# Anthropic Claude
ANTHROPIC_API_KEY=<CHAVE_DA_ANTHROPIC>

# Parâmetros de cálculo de margem (ajuste conforme seu negócio)
TAXA_ML_CLASSICO=0.115
TAXA_ML_PREMIUM=0.165
ALIQUOTA_IMPOSTO=0.06
FRETE_FIXO=20.00
CUSTOS_EXTRAS=2.00
MARGEM_MINIMA=0.13
```

### Arquivo: `requirements.txt`

```text
requests>=2.31.0
python-dotenv>=1.0.0
pdfplumber>=0.10.0
PyMuPDF>=1.23.0
pandas>=2.1.0
openpyxl>=3.1.0
streamlit>=1.30.0
rapidfuzz>=3.5.0
Pillow>=10.0.0
anthropic>=0.40.0
tenacity>=8.2.0
```

---

## 🏗️ Passo 3 — Código-fonte dos módulos

### Arquivo: `src/__init__.py`

```python
# vazio (marca como pacote Python)
```

### Arquivo: `src/config.py`

```python
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
```

### Arquivo: `src/ml_api.py`

```python
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
```

### Arquivo: `src/pdf_parser.py`

```python
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


def parse_pdf_tabela(path: Path) -> list[ProdutoFornecedor]:
    """Fluxo 1: PDF com tabela estruturada (produto, código, preço).

    Heurística simples: pega cada linha não-vazia, tenta extrair preço.
    Funciona pra PDFs onde o produto é uma linha de texto.
    """
    produtos: list[ProdutoFornecedor] = []
    with pdfplumber.open(path) as pdf:
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
```

### Arquivo: `src/vision_llm.py`

```python
"""Wrapper para Claude Vision (comparação de imagens de produto)."""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Iterable

from anthropic import Anthropic

from .config import ClaudeConfig, load_claude_config

logger = logging.getLogger(__name__)


PROMPT_SYSTEM = """Você é um especialista em identificação de produtos para e-commerce.
Sua tarefa é comparar uma imagem de produto de um fornecedor com candidatos do Mercado Livre
e decidir qual (se algum) é o MESMO produto físico.

Considere SIM (match) quando coincidirem:
- Marca/logo (se visíveis)
- Número e disposição de componentes (tomadas, USB, botões, etc)
- Cor predominante e formato geral
- Códigos/textos impressos
- Especificações técnicas visíveis

IGNORE:
- Ângulo, iluminação, fundo
- Cabo enrolado vs solto
- Foto com pessoa segurando vs em fundo branco
- Pequenas diferenças de acabamento

Quando houver dúvida real (variações de modelo, cor diferente, geração diferente),
sinalize com confiança média e explique."""

PROMPT_USER_TEMPLATE = """Comparar imagem do FORNECEDOR (primeira) com {n} CANDIDATOS do Mercado Livre.

DADOS:
- Nome do produto no fornecedor: "{nome_fornec}"
- Preço do fornecedor: R$ {preco_fornec}
- Código (se houver): {codigo_fornec}

Para cada candidato (numerados de 1 a {n}), avalie se é o MESMO produto físico.

Responda APENAS um JSON neste formato exato:
{{
  "matches": [
    {{"candidato": 1, "match": true|false, "confianca": 0-100, "motivo": "explicação curta"}},
    ...
  ],
  "melhor_candidato": <número ou null>,
  "observacao": "texto livre opcional"
}}"""


class VisionMatcher:
    """Compara imagem do fornecedor com candidatos do ML usando Claude Vision."""

    def __init__(self, cfg: ClaudeConfig | None = None) -> None:
        cfg = cfg or load_claude_config()
        if cfg is None:
            raise RuntimeError(
                "ANTHROPIC_API_KEY não configurada no .env. "
                "Vision matching indisponível."
            )
        self.cfg = cfg
        self.client = Anthropic(api_key=cfg.api_key)

    @staticmethod
    def _encode_image(data: bytes, media_type: str = "image/jpeg") -> dict:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode(),
            },
        }

    @staticmethod
    def _detect_media_type(data: bytes) -> str:
        if data.startswith(b"\x89PNG"):
            return "image/png"
        if data.startswith(b"GIF"):
            return "image/gif"
        if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
            return "image/webp"
        return "image/jpeg"

    def compare(
        self,
        foto_fornecedor: bytes,
        candidatos_fotos: list[bytes],
        nome_fornec: str,
        preco_fornec: float | None = None,
        codigo_fornec: str | None = None,
    ) -> dict:
        """Compara 1 foto do fornecedor com N candidatos do ML.

        Retorna dict com:
          - matches: lista [{candidato, match, confianca (0-1), motivo}]
          - melhor_candidato: índice (1-based) ou None
          - observacao
        """
        n = len(candidatos_fotos)
        if n == 0:
            return {"matches": [], "melhor_candidato": None, "observacao": "sem candidatos"}

        content: list[dict] = []
        content.append({"type": "text", "text": "FORNECEDOR:"})
        content.append(self._encode_image(foto_fornecedor, self._detect_media_type(foto_fornecedor)))
        for i, img in enumerate(candidatos_fotos, start=1):
            content.append({"type": "text", "text": f"CANDIDATO {i}:"})
            content.append(self._encode_image(img, self._detect_media_type(img)))
        content.append(
            {
                "type": "text",
                "text": PROMPT_USER_TEMPLATE.format(
                    n=n,
                    nome_fornec=nome_fornec,
                    preco_fornec=f"{preco_fornec:.2f}" if preco_fornec else "?",
                    codigo_fornec=codigo_fornec or "?",
                ),
            }
        )

        resp = self.client.messages.create(
            model=self.cfg.model,
            max_tokens=self.cfg.max_tokens,
            system=PROMPT_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(block.text for block in resp.content if hasattr(block, "text"))
        return self._parse_response(text, n)

    @staticmethod
    def _parse_response(text: str, n_candidatos: int) -> dict:
        """Extrai JSON da resposta. Normaliza confiança pra 0-1."""
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            logger.warning("vision: no JSON in response: %s", text[:200])
            return {"matches": [], "melhor_candidato": None, "observacao": text}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            logger.warning("vision: invalid JSON: %s", e)
            return {"matches": [], "melhor_candidato": None, "observacao": text}

        # normaliza confiança: aceita 0-100 ou 0-1
        for m in data.get("matches", []):
            c = float(m.get("confianca", 0))
            m["confianca"] = c / 100 if c > 1.0 else c
        return data
```

### Arquivo: `src/matcher.py`

```python
"""Matching em 3 camadas: código exato → fuzzy textual → vision LLM.

Cada camada tem custo crescente. Para por confiança alta na camada mais barata possível.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from rapidfuzz import fuzz

from .ml_api import MLClient
from .pdf_parser import ProdutoFornecedor
from .vision_llm import VisionMatcher

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    catalog_product_id: str | None  # None = nenhum match aceito
    confianca: float  # 0-1
    metodo: str  # "codigo_exato" | "fuzzy_nome" | "vision_llm" | "vision_review" | "nao_encontrado"
    candidato_ml: dict | None = None  # produto completo do ML (se confirmado)
    candidatos_revisao: list[dict] | None = None  # alternativas pra review humano
    motivo: str = ""


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
    """Camada 2: fuzzy match no nome. Retorna o melhor se score > 80."""
    if not candidatos:
        return None
    melhor = None
    melhor_score = 0.0
    for c in candidatos:
        nome_ml = c.get("name", "").lower()
        # token_set ignora ordem e duplicação
        score = fuzz.token_set_ratio(produto.nome.lower(), nome_ml) / 100.0
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
        threshold_auto: float = 0.85,
        threshold_review: float = 0.60,
        max_candidatos_vision: int = 5,
    ) -> None:
        self.ml = ml
        self.vision = vision
        self.threshold_auto = threshold_auto
        self.threshold_review = threshold_review
        self.max_candidatos_vision = max_candidatos_vision

    def buscar_candidatos(self, produto: ProdutoFornecedor, limit_per_query: int = 10) -> list[dict]:
        """Executa todas as queries e agrega resultados, deduplicando por id."""
        vistos = set()
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
        logger.info("found %d unique candidates for '%s'", len(agregados), produto.nome[:50])
        return agregados

    def match(self, produto: ProdutoFornecedor) -> MatchResult:
        candidatos = self.buscar_candidatos(produto)
        if not candidatos:
            return MatchResult(
                catalog_product_id=None,
                confianca=0.0,
                metodo="nao_encontrado",
                motivo="busca retornou 0 resultados (provavelmente só anúncio tradicional)",
            )

        # ---- CAMADA 1: código exato ----
        hit = _match_por_codigo(produto, candidatos)
        if hit:
            cand, conf = hit
            return MatchResult(
                catalog_product_id=cand["id"],
                confianca=conf,
                metodo="codigo_exato",
                candidato_ml=cand,
                motivo=f"código {produto.codigo} bate com atributo do ML",
            )

        # ---- CAMADA 2: fuzzy texto ----
        hit = _match_por_texto(produto, candidatos)
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

        # ---- CAMADA 3: vision LLM (se disponível e tiver foto) ----
        if self.vision and produto.imagem_path and produto.imagem_path.exists():
            return self._match_visual(produto, candidatos)

        # Sem vision e sem matches confiáveis → review humano
        return MatchResult(
            catalog_product_id=None,
            confianca=hit[1] if hit else 0.0,
            metodo="vision_review",
            candidatos_revisao=candidatos[: self.max_candidatos_vision],
            motivo="sem confiança suficiente; vision não disponível ou sem foto",
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

        return MatchResult(
            catalog_product_id=cand["id"] if metodo == "vision_llm" else None,
            confianca=conf,
            metodo=metodo,
            candidato_ml=cand,
            candidatos_revisao=candidatos_usaveis if metodo == "vision_review" else None,
            motivo=melhor.get("motivo", ""),
        )
```

### Arquivo: `src/analyzer.py`

```python
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
        return bandeiras
```

### Arquivo: `src/margin.py`

```python
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
```

### Arquivo: `src/cache.py`

```python
"""Cache de matches em SQLite.

Lembra associações fornecedor → catalog_product_id confirmadas.
Na próxima execução do mesmo SKU/nome, pula a etapa cara de matching.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS product_match (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fornecedor TEXT,
    codigo_fornecedor TEXT,
    nome_fornecedor TEXT NOT NULL,
    catalog_product_id TEXT NOT NULL,
    confianca REAL NOT NULL,
    metodo TEXT NOT NULL,
    confirmado_por TEXT,
    criado_em REAL NOT NULL,
    UNIQUE (fornecedor, codigo_fornecedor, nome_fornecedor)
);
CREATE INDEX IF NOT EXISTS idx_match_nome ON product_match(nome_fornecedor);
CREATE INDEX IF NOT EXISTS idx_match_codigo ON product_match(codigo_fornecedor);
"""


@contextmanager
def get_conn(path: Path = DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(path: Path = DB_PATH) -> None:
    with get_conn(path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def lookup(
    nome: str,
    codigo: str | None = None,
    fornecedor: str | None = None,
) -> dict | None:
    """Busca match por código (preferencial) ou nome."""
    init_db()
    with get_conn() as conn:
        if codigo:
            row = conn.execute(
                "SELECT * FROM product_match WHERE codigo_fornecedor = ? ORDER BY criado_em DESC LIMIT 1",
                (codigo,),
            ).fetchone()
            if row:
                return dict(row)
        row = conn.execute(
            "SELECT * FROM product_match WHERE nome_fornecedor = ? ORDER BY criado_em DESC LIMIT 1",
            (nome,),
        ).fetchone()
        return dict(row) if row else None


def upsert(
    nome: str,
    catalog_product_id: str,
    confianca: float,
    metodo: str,
    codigo: str | None = None,
    fornecedor: str | None = None,
    confirmado_por: str | None = None,
) -> None:
    """Insere ou atualiza um match no cache."""
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO product_match
                (fornecedor, codigo_fornecedor, nome_fornecedor, catalog_product_id,
                 confianca, metodo, confirmado_por, criado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fornecedor, codigo_fornecedor, nome_fornecedor)
            DO UPDATE SET
                catalog_product_id=excluded.catalog_product_id,
                confianca=excluded.confianca,
                metodo=excluded.metodo,
                confirmado_por=excluded.confirmado_por,
                criado_em=excluded.criado_em
            """,
            (fornecedor, codigo, nome, catalog_product_id, confianca, metodo, confirmado_por, time.time()),
        )
        conn.commit()
```

### Arquivo: `src/exporter.py`

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .analyzer import AnaliseConcorrencia
from .margin import CalculoMargem
from .pdf_parser import ProdutoFornecedor

logger = logging.getLogger(__name__)


@dataclass
class LinhaResultado:
    produto_fornec_nome: str
    produto_fornec_codigo: str
    produto_fornec_preco: float | None
    catalog_id: str | None
    catalog_name: str
    status_match: str  # codigo_exato | fuzzy_nome | vision_llm | vision_review | nao_encontrado
    confianca_match: float
    n_concorrentes: int
    n_full: int
    preco_min: float
    preco_mediana: float
    preco_max: float
    lider_full_preco: float | None
    lider_full_seller: str
    visitas_30d: int
    margem_pct: float | None
    margem_pct_premium: float | None
    pv_alvo: float | None
    lucro_alvo: float | None
    score_oportunidade: float
    bandeiras: str
    veredicto: str  # APROVADO | AVALIAR | REJEITAR | DESCARTADO | REVIEW
    permalink_lider: str


VERDICT_COLORS = {
    "APROVADO": "C6EFCE",   # verde
    "AVALIAR": "FFEB9C",    # amarelo
    "REJEITAR": "FFC7CE",   # vermelho
    "REVIEW": "BDD7EE",     # azul
    "DESCARTADO": "D9D9D9", # cinza
}


def gerar_excel(linhas: list[LinhaResultado], out_path) -> object:
    """Gera Excel formatado. Retorna o path final do arquivo."""
    df = pd.DataFrame([_linha_to_dict(l) for l in linhas])
    df = df.sort_values(by="score_oportunidade", ascending=False, ignore_index=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.suffix:
        out_path = out_path.with_suffix(".xlsx")
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Oportunidades", index=False)
        _formatar(writer.book["Oportunidades"])
    return out_path


def _linha_to_dict(l: LinhaResultado) -> dict:
    return {
        "Produto Fornecedor": l.produto_fornec_nome,
        "Código Fornec.": l.produto_fornec_codigo or "",
        "Custo (R$)": l.produto_fornec_preco,
        "Catálogo ML": l.catalog_id or "",
        "Nome no ML": l.catalog_name,
        "Status Match": l.status_match,
        "Confiança": round(l.confianca_match, 2),
        "# Concorrentes": l.n_concorrentes,
        "# Full": l.n_full,
        "Preço Min": l.preco_min,
        "Preço Mediana": l.preco_mediana,
        "Preço Max": l.preco_max,
        "Líder Full Preço": l.lider_full_preco,
        "Líder Full Seller": l.lider_full_seller,
        "Visitas 30d": l.visitas_30d,
        "Margem % (clássico)": _pct(l.margem_pct),
        "Margem % (premium)": _pct(l.margem_pct_premium),
        "PV Alvo": l.pv_alvo,
        "Lucro Alvo": l.lucro_alvo,
        "Score": round(l.score_oportunidade, 1),
        "Bandeiras": l.bandeiras,
        "Veredicto": l.veredicto,
        "Permalink": l.permalink_lider,
    }


def _pct(v: float | None) -> str:
    if v is None:
        return ""
    return f"{v * 100:.1f}%"


def _formatar(ws) -> None:
    # Header em negrito + fundo cinza
    header_fill = PatternFill("solid", fgColor="305496")
    header_font = Font(bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Auto-width simples
    for col_idx, column_cells in enumerate(ws.columns, start=1):
        max_len = max((len(str(c.value)) for c in column_cells if c.value is not None), default=10)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 40)

    # Colorir linha por Veredicto
    headers = [c.value for c in ws[1]]
    try:
        idx_veredicto = headers.index("Veredicto") + 1
    except ValueError:
        return

    for row in ws.iter_rows(min_row=2):
        veredicto = row[idx_veredicto - 1].value
        cor = VERDICT_COLORS.get(str(veredicto))
        if cor:
            fill = PatternFill("solid", fgColor=cor)
            for cell in row:
                cell.fill = fill


def montar_linha(
    produto: ProdutoFornecedor,
    analise: AnaliseConcorrencia | None,
    margem_classico: CalculoMargem | None,
    margem_premium: CalculoMargem | None,
    margem_minima: float,
    score: float,
    status_match: str,
    confianca_match: float,
    veredicto_motivo: str = "",
) -> LinhaResultado:
    lider_full = analise.lider_full if analise else None
    lider = lider_full or (analise.lider_geral if analise else None)
    permalink = ""
    if lider:
        permalink = f"https://produto.mercadolivre.com.br/{lider.item_id}"
    # Veredicto
    if status_match == "nao_encontrado":
        veredicto = "DESCARTADO"
    elif status_match == "vision_review":
        veredicto = "REVIEW"
    elif analise and analise.catalogo_fantasma:
        veredicto = "DESCARTADO"
    elif margem_classico is None or analise is None:
        veredicto = "REVIEW"
    elif margem_classico.margem_pct < margem_minima:
        veredicto = "REJEITAR"
    elif analise and "marca_vende_direto" in analise.bandeiras:
        veredicto = "AVALIAR"
    elif analise and "demanda_baixa" in analise.bandeiras:
        veredicto = "AVALIAR"
    else:
        veredicto = "APROVADO"

    return LinhaResultado(
        produto_fornec_nome=produto.nome,
        produto_fornec_codigo=produto.codigo or "",
        produto_fornec_preco=produto.preco,
        catalog_id=analise.catalog_product_id if analise else None,
        catalog_name=analise.catalog_name if analise else "",
        status_match=status_match,
        confianca_match=confianca_match,
        n_concorrentes=analise.n_concorrentes if analise else 0,
        n_full=analise.n_full if analise else 0,
        preco_min=analise.preco_min if analise else 0.0,
        preco_mediana=analise.preco_mediana if analise else 0.0,
        preco_max=analise.preco_max if analise else 0.0,
        lider_full_preco=lider_full.preco if lider_full else None,
        lider_full_seller=lider_full.seller_nickname if lider_full else "",
        visitas_30d=analise.visitas_total_30d if analise else 0,
        margem_pct=margem_classico.margem_pct if margem_classico else None,
        margem_pct_premium=margem_premium.margem_pct if margem_premium else None,
        pv_alvo=margem_classico.preco_venda if margem_classico else None,
        lucro_alvo=margem_classico.lucro if margem_classico else None,
        score_oportunidade=score,
        bandeiras=", ".join(analise.bandeiras) if analise else "",
        veredicto=veredicto,
        permalink_lider=permalink,
    )


def nome_excel_default():
    from .config import OUTPUTS_DIR
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return OUTPUTS_DIR / f"analise_{ts}.xlsx"
```

### Arquivo: `src/pipeline.py`

```python
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

        if self.use_cache:
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
```

### Arquivo: `app.py` (Streamlit UI)

```python
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
import sys
from io import BytesIO
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.config import IMGS_DIR, load_claude_config, load_margin_params
from src.exporter import gerar_excel, nome_excel_default
from src.ml_api import MLClient
from src.pdf_parser import (
    ProdutoFornecedor,
    carregar_produto_manual,
    extrair_imagens_pdf,
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

# Tabs
tab_manual, tab_pdf, tab_resultados = st.tabs(["📝 Manual (1 produto)", "📄 PDF do fornecedor", "📊 Resultados"])

if "resultados" not in st.session_state:
    st.session_state.resultados = []

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
    st.caption("Suporta PDFs com tabela (produto + código + preço). Para encartes com fotos, use a aba Manual por enquanto.")

    pdf_file = st.file_uploader("Arquivo PDF", type=["pdf"])
    if pdf_file and st.button("📤 Processar PDF", type="primary"):
        pdf_path = ROOT / "sample_pdfs" / pdf_file.name
        pdf_path.write_bytes(pdf_file.getvalue())

        with st.spinner("Extraindo produtos do PDF..."):
            produtos = parse_pdf_tabela(pdf_path)
            st.info(f"Extraídos {len(produtos)} produtos do PDF.")

        if produtos:
            progress = st.progress(0.0, text="Analisando produtos...")
            pipeline = get_pipeline()
            resultados_pdf: list[ResultadoProduto] = []

            def cb(i: int, total: int, p: ProdutoFornecedor, r: ResultadoProduto) -> None:
                progress.progress(i / total, text=f"[{i}/{total}] {p.nome[:60]}")

            resultados_pdf = pipeline.processar_lote(produtos, progress_callback=cb)
            st.session_state.resultados.extend(resultados_pdf)
            progress.empty()
            st.success(f"Análise concluída. {len(resultados_pdf)} produtos processados.")

with tab_resultados:
    st.subheader(f"📊 Resultados acumulados ({len(st.session_state.resultados)})")

    if not st.session_state.resultados:
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

        filtrados = [r for r in st.session_state.resultados if r.linha_excel.veredicto in filtros]
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
```

### Arquivo: `scripts/oauth_bootstrap.py`

```python
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
```

---

## 🏗️ Passo 4 — Instalar dependências e rodar

```bash
# Criar e ativar venv
python3 -m venv .venv
source .venv/bin/activate  # Mac/Linux
# .venv\Scripts\activate   # Windows

# Instalar dependências
pip install -r requirements.txt

# Copiar e preencher credenciais
cp .env.example .env
# Edite .env com seus valores reais

# Gerar refresh_token (apenas uma vez)
python scripts/oauth_bootstrap.py

# Rodar a aplicação
streamlit run app.py
```#   m l - p r o j e c t  
 