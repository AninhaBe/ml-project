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


PROMPT_SYSTEM = """Você é um especialista em identificação de produtos para distribuição e e-commerce brasileiro.

Sua tarefa: dado um produto de catálogo de fornecedor e candidatos do Mercado Livre, decidir qual é o MESMO produto — não a mesma foto, mas o mesmo PRODUTO FÍSICO.

PRINCIPIO FUNDAMENTAL:
Produtos identicos podem ter fotos completamente diferentes no fornecedor e no ML:
- Ângulo diferente (frente vs lateral vs perspectiva)
- Fundo diferente (branco vs colorido vs lifestyle)
- Acessórios mostrados ou não (cabo, case, manual)
- Qualidade/edição de foto diferente
Isso NAO invalida o match. Avalie o PRODUTO, não a foto.

O QUE DEFINE MESMO PRODUTO:
1. Categoria/tipo identico (medidor de pressão arterial de braço = medidor de pressão arterial de braço)
2. Especificações principais batem (digital, automático, display LCD, manguito de braço, etc)
3. Forma/silhueta geral compatível (não precisa ser idêntica)
4. Marca (se visível e diferente = NAO é match)
5. Nome do produto do fornecedor bate semanticamente com o nome do candidato ML

O QUE IGNORAR:
- Ângulo, iluminação, sombra, fundo
- Foto com ou sem pessoa/cenário
- Acessórios opcionais mostrados ou não
- Cor diferente SE o nome indica cores diferentes como variações (ex: azul vs preto)
- Geração ligeiramente diferente do mesmo produto genérico

USE OS DADOS TEXTUAIS COMO ÂNCORA PRINCIPAL:
O nome e código do fornecedor são a referência mais confiável.
A imagem serve para CONFIRMAR ou REJEITAR, não para ser a única fonte.

Quando houver dúvida real (produto de categoria diferente, especificação incompatível),
sinalize com confiança baixa e explique brevemente."""

PROMPT_USER_TEMPLATE = """Compare o produto do FORNECEDOR com os {n} CANDIDATOS do Mercado Livre abaixo.

DADOS DO FORNECEDOR (use como âncora principal):
- Nome: "{nome_fornec}"
- Preço de custo: R$ {preco_fornec}
- Código/SKU: {codigo_fornec}

IMPORTANTE: O mesmo produto genérico pode ter fotos muito diferentes entre fornecedor e ML.
Avalie se é o mesmo TIPO de produto com as mesmas ESPECIFICAÇÕES, não se a foto é parecida.

Para cada candidato (1 a {n}), responda:
- match: true se é o mesmo produto (mesma categoria + especificações principais)
- confianca: 0-100
- motivo: 1 linha explicando o que confirmou ou rejeitou

Responda APENAS JSON neste formato:
{{
  "matches": [
    {{"candidato": 1, "match": true|false, "confianca": 0-100, "motivo": "..."}},
    ...
  ],
  "melhor_candidato": <número ou null>,
  "observacao": "opcional"
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
