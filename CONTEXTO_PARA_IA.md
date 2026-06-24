# Contexto do Projeto: ml_market_agent

## O que é o sistema

Agente de análise de mercado para revendedores. O usuário envia um catálogo de fornecedor em PDF, o sistema:
1. Extrai produtos do PDF via Vision LLM (Claude)
2. Busca cada produto no Mercado Livre (catálogos MLB)
3. Analisa viabilidade: margem, visitas, concorrência
4. Exibe resultado no frontend com veredictos: **APROVADO / AVALIAR / REJEITAR / DESCARTADO / REVIEW**

## Stack

- **Backend**: Python + FastAPI (`api.py`)
- **Frontend**: HTML/JS puro (`static/index.html`)
- **ML API**: OAuth2 com token de seller (`src/ml_api.py`)
- **Matcher**: 3 camadas — código exato → fuzzy texto → Vision LLM (`src/matcher.py`)
- **Parser PDF**: PyMuPDF + Claude Vision (`src/pdf_parser.py`)
- **Cache**: SQLite (`src/cache.py`) — tabelas: `pdf_produtos_cache`, `pdf_resultados_cache`, `product_match`, `vision_acionado`
- **Diretório de imagens**: `G:\ml_market_agent\data\imgs\`
- **Servidor**: uvicorn na porta 8000

## Fluxo resumido

```
PDF upload → parse_pdf_auto() → ProdutoFornecedor[] 
  → Pipeline.processar() → Matcher.match() → MatchResult
  → Analyzer → Exporter.montar_linha() → linha_excel dict
  → /api/resultados/{job_id} → frontend cards
```

## Estado atual (onde estamos)

### O que já funciona
- Extração de produtos do PDF com bbox correto (recorte de imagem funcional)
- Match por Vision LLM identifica produtos corretamente
- Cálculo de margem e score de oportunidade
- Veredictos APROVADO/REJEITAR/DESCARTADO/REVIEW/AVALIAR no frontend
- Imagens PDF e ML exibidas lado a lado nos cards
- Cache de produtos com paths absolutos sem caracteres Unicode problemáticos

### Problema persistente: produtos indisponíveis sendo matchados

O matcher encontra catálogos no ML que estão **sem estoque / indisponíveis**, mas ainda os retorna como match.

**Causa raiz identificada**: O endpoint `/products/{catalog_id}/items` da API ML retorna `404` ("No winners found") para catálogos sem anúncios ativos. O `get_product_items()` retorna `[]`, o `_avaliar_catalogo()` retorna `descartado="sem_anuncios"`, e o candidato fica com `_aprovado=False`.

**Porém**, o Vision LLM ainda recebe esses candidatos reprovados (via `cands_vision = todos` quando não há aprovados), identifica o produto corretamente e retorna o candidato — mas sem checar `_aprovado`. Isso foi parcialmente corrigido (match visual agora força `sem_criterio` para reprovados), mas o `_avaliar_catalogo` nunca consegue verificar estoque real porque:

1. `/products/{id}/items` → sempre 404 para catálogos sem winner
2. `/sites/MLB/search?catalog_product_id=` → **403 Forbidden** (token sem scope)
3. `buy_box_winner` no `/products/{id}` fica vazio para produtos indisponíveis

**O que funciona para detectar indisponibilidade**: `buy_box_winner == {}` em `/products/{id}` indica que não há nenhum anúncio ativo. Este campo está disponível e é confiável.

## Pendências críticas

### 1. Filtro de disponibilidade via `buy_box_winner`
Em `src/matcher.py`, método `_avaliar_catalogo`, adicionar verificação:
```python
produto_data = self.ml.get_product(catalog_id)  # GET /products/{id}
bbw = (produto_data or {}).get("buy_box_winner") or {}
if not bbw:
    return {"descartado": "sem_winner (produto indisponível)", "score": 0}
```
E em `src/ml_api.py`, o método `catalog_tem_anuncios_ativos` deve usar essa lógica em vez de `get_product_items`.

### 2. Imagem PDF ainda aparece como bloco inteiro às vezes
O recorte via bbox está correto no disco (`page_002_prod00.png` tem ~160KB vs `page_002_full.png` com ~1.2MB). O endpoint `/api/imagem-arquivo?path=...` funciona. O problema era que o timestamp de cache-bust `?t=...` estava sendo embutido no path em vez de ser parâmetro separado — já corrigido com `&t=`.

### 3. Match de produto errado
"Garrafa Térmica 800ml com Sensor" (preta, simples) está sendo matchada com "Garrafa Térmica Display Mulheres Com Temperatura Sensor Led" (colorida, feminina). O Vision LLM está sendo enganado pela semelhança superficial do nome. Possível melhoria: aumentar penalidade visual no prompt quando as fotos claramente não batem.

## Arquivos principais

```
g:\ml_market_agent\
├── api.py                    # FastAPI: upload, jobs, resultados, endpoints de imagem
├── static\index.html         # Frontend SPA
├── src\
│   ├── matcher.py            # Lógica de matching (3 camadas + Vision)
│   ├── ml_api.py             # Cliente API Mercado Livre
│   ├── pdf_parser.py         # Extração de produtos do PDF
│   ├── pipeline.py           # Orquestra match + análise
│   ├── analyzer.py           # Detecta bandeiras (risco, oportunidade)
│   ├── exporter.py           # Monta linha_excel com veredicto final
│   ├── cache.py              # SQLite cache
│   ├── config.py             # Configurações (margem, API keys)
│   └── vision_extractor.py  # Chama Claude para extrair produtos do PDF
```

## Veredictos e lógica

| status_match     | Veredicto  | Significado |
|-----------------|-----------|-------------|
| `vision_llm`    | APROVADO/REJEITAR | Match confirmado por Vision, avalia margem |
| `fuzzy_nome`    | APROVADO/REJEITAR | Match textual, avalia margem |
| `vision_review` | REVIEW | Vision com confiança média, precisa revisão humana |
| `sem_criterio`  | AVALIAR | Produto identificado mas sem anúncios ativos/demanda baixa |
| `nao_encontrado`| DESCARTADO | Nenhum catálogo encontrado |

## Restrições da API ML (token atual)

- `GET /products/{id}` ✅ funciona — retorna produto com `buy_box_winner`, `pictures`, `name`
- `GET /products/{id}/items` ❌ retorna 404 para catálogos sem winner
- `GET /sites/MLB/search?catalog_product_id=` ❌ 403 Forbidden
- `GET /sites/MLB/search?q=` ❌ 403 Forbidden  
- `GET /products/search?q=` ✅ funciona — usado para buscar candidatos
- `GET /items/{id}` ✅ funciona — retorna item com `available_quantity`, `status`
