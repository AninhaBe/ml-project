// ML Agent - Avantpro Collector
// Lê os dados que a Avantpro injeta no DOM e envia para o backend local

const BACKEND_URL = "http://localhost:8000/api/avantpro-dados";
const WAIT_MS = 4000; // espera Avantpro carregar

function parseBRL(text) {
  if (!text) return null;
  const clean = text.replace(/[^\d,]/g, "").replace(",", ".");
  const val = parseFloat(clean);
  return isNaN(val) ? null : val;
}

function parseNum(text) {
  if (!text) return null;
  const clean = text.replace(/[^\d]/g, "");
  const val = parseInt(clean);
  return isNaN(val) ? null : val;
}

function extractCatalogId() {
  // Pega o catalog_id da URL: /p/MLB123456 ou /products/MLB123456/...
  const patterns = [/\/p\/(MLB\d+)/, /\/products\/(MLB\d+)/];
  for (const p of patterns) {
    const m = window.location.pathname.match(p);
    if (m) return m[1];
  }
  // Tenta canonical
  const canonical = document.querySelector('link[rel="canonical"]');
  if (canonical) {
    for (const p of patterns) {
      const m = canonical.href.match(p);
      if (m) return m[1];
    }
  }
  // Tenta pegar do JSON-LD na página
  const jsonLd = document.querySelector('script[type="application/ld+json"]');
  if (jsonLd) {
    const m = jsonLd.textContent.match(/"(MLB\d+)"/);
    if (m) return m[1];
  }
  return null;
}

function extractItemId() {
  // Pega item_id da URL: /MLB123456-titulo ou do JSON-LD
  const m = window.location.pathname.match(/\/(MLB\d+)-/);
  if (m) return m[1];
  return null;
}

function findAvantproData() {
  const data = {};

  // Container principal da Avantpro na página de catálogo/produto
  const container = document.querySelector(
    '[class*="avantpro-in-product-content"], [class*="avantpro-product"]'
  );

  if (!container) return data;

  const allLeaves = [...container.querySelectorAll("*")].filter(el => el.children.length === 0);

  // Acha o card (célula) que contém um label e retorna o texto completo do card
  function findCellByLabel(labelText) {
    for (const el of allLeaves) {
      const t = el.textContent.trim();
      if (t === labelText || t.startsWith(labelText)) {
        // Sobe até encontrar um card razoável (até 4 níveis)
        let card = el.parentElement;
        for (let i = 0; i < 3 && card; i++) {
          // Se o card tem mais de um texto, é o container certo
          const leaves = [...card.querySelectorAll("*")].filter(e => e.children.length === 0);
          if (leaves.length >= 2) return { card, labelEl: el };
          card = card.parentElement;
        }
        return { card: el.parentElement, labelEl: el };
      }
    }
    return null;
  }

  // Extrai o primeiro número do card que NÃO faz parte do label
  function valueForLabel(labelText) {
    const hit = findCellByLabel(labelText);
    if (!hit) return null;
    const { card, labelEl } = hit;
    const leaves = [...card.querySelectorAll("*")].filter(e => e.children.length === 0);
    for (const leaf of leaves) {
      if (leaf === labelEl) continue;
      const txt = leaf.textContent.trim();
      if (/\d/.test(txt)) return txt;
    }
    // Fallback: número no texto do card sem o label
    const semLabel = card.textContent.replace(labelText, "");
    const m = semLabel.match(/[\d.,]+/);
    return m ? m[0] : null;
  }

  // Vendas do catálogo — label "Vendidos" (ex: "3 Vendidos", "100 Vendidos")
  let vendidos = valueForLabel("Vendidos");
  if (!vendidos) {
    // Pode estar como leaf "X Vendidos" inteiro
    const vLeaf = allLeaves.find(el => /\d+\s*Vendidos/i.test(el.textContent));
    if (vLeaf) { const m = vLeaf.textContent.match(/(\d+)\s*Vendidos/i); if (m) vendidos = m[1]; }
  }
  if (vendidos) data.vendas_catalogo = parseNum(vendidos);

  const estoque = valueForLabel("Estoque");
  if (estoque) data.estoque = parseNum(estoque);

  const estimadas = valueForLabel("Vendas estimadas");
  if (estimadas) data.vendas_estimadas = parseNum(estimadas);

  const ritmo = valueForLabel("Ritmo atual");
  if (ritmo) data.ritmo_mensal = parseNum(ritmo);

  const visitas = valueForLabel("Visitas");
  if (visitas) data.visitas = parseNum(visitas);

  const vendasMensais = valueForLabel("Vendas mensais");
  if (vendasMensais) data.vendas_mensais = parseNum(vendasMensais);

  const conversao = valueForLabel("Conversão");
  if (conversao) {
    const pct = conversao.match(/([\d,]+)/);
    if (pct) data.conversao_pct = parseFloat(pct[1].replace(",", "."));
  }

  const participacao = valueForLabel("Participação");
  if (participacao) {
    const pct = participacao.match(/([\d,]+)/);
    if (pct) data.participacao_pct = parseFloat(pct[1].replace(",", "."));
  }

  // Faturamento — botão "Faturando R$ X" no rodapé do painel
  const faturandoBtn = container.querySelector('button, [class*="faturan"]');
  if (faturandoBtn) {
    const brl = faturandoBtn.textContent.match(/R\$\s*[\d.,]+/);
    if (brl) data.faturamento = parseBRL(brl[0]);
  }
  // Fallback: qualquer R$ no container que pareça faturamento
  if (!data.faturamento) {
    const allText = container.textContent;
    const brlMatches = [...allText.matchAll(/Faturan[^R]*R\$\s*([\d.,]+)/gi)];
    if (brlMatches.length > 0) data.faturamento = parseBRL("R$ " + brlMatches[0][1]);
  }

  // Vendas por dia (ex: "Menos de 1", "3")
  const vendasDia = valueForLabel("Vendas por dia");
  if (vendasDia) {
    if (/menos de 1/i.test(vendasDia)) data.vendas_por_dia = 0.5;
    else { const n = parseNum(vendasDia); if (n != null) data.vendas_por_dia = n; }
  }

  // Datas de criação + idade em dias (ex: "Anúncio criado em 27/05/2026 Há 27 dias")
  function extrairData(labelText, prefixo) {
    const hit = findCellByLabel(labelText);
    if (!hit) return;
    const txt = hit.card.textContent;
    const dataM = txt.match(/(\d{2}\/\d{2}\/\d{4})/);
    if (dataM) data[prefixo + "_data"] = dataM[1];
    const diasM = txt.match(/H[áa]\s*([\d.]+)\s*dias/i);
    if (diasM) data[prefixo + "_dias"] = parseNum(diasM[1]);
  }
  extrairData("Anúncio criado em", "anuncio_criado");
  extrairData("Catálogo criado em", "catalogo_criado");

  // Vendidos badge da página principal (fora do container Avantpro)
  const vendidosEl = document.querySelector('[class*="pdp-subtitle"] span, [class*="sold-quantity"]');
  if (vendidosEl) {
    const m = vendidosEl.textContent.match(/\+?([\d.]+)\s*vendidos/i);
    if (m) data.vendidos_badge = parseNum(m[1]);
  }

  return data;
}

function sendToBackend(catalogId, itemId, avantproData) {
  const payload = {
    catalog_id: catalogId,
    item_id: itemId,
    url: window.location.href,
    timestamp: new Date().toISOString(),
    ...avantproData,
  };

  // Só envia se tem pelo menos um dado útil
  const temDados = Object.keys(avantproData).length > 0;
  if (!temDados) {
    console.log("[ML Agent] Nenhum dado Avantpro encontrado nesta página.");
    return;
  }

  console.log("[ML Agent] Enviando dados:", payload);

  fetch(BACKEND_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then((r) => r.json())
    .then((d) => console.log("[ML Agent] Salvo:", d))
    .catch((e) => console.warn("[ML Agent] Erro ao enviar:", e));
}

function debugAvantproHTML() {
  // Loga o HTML real da Avantpro para identificar os seletores corretos
  const allEls = [...document.querySelectorAll("*")];

  // Procura container com textos típicos da Avantpro
  const keywords = ["Faturamento", "Vendas estimadas", "Ritmo atual", "Vendas do catálogo", "Participação"];
  for (const kw of keywords) {
    const found = allEls.find(el => el.children.length === 0 && el.textContent.trim() === kw);
    if (found) {
      // Sobe 3 níveis para pegar o container
      let container = found;
      for (let i = 0; i < 4; i++) container = container.parentElement || container;
      console.log(`[ML Agent DEBUG] Encontrou "${kw}" — HTML do container:`);
      console.log(container.outerHTML.slice(0, 800));
      break;
    }
  }

  // Log de todos os textos dos elementos folha que podem ser da Avantpro
  const avantTexts = allEls
    .filter(el => el.children.length === 0)
    .filter(el => keywords.some(kw => el.textContent.includes(kw)))
    .map(el => ({ text: el.textContent.trim(), tag: el.tagName, class: el.className }));

  if (avantTexts.length > 0) {
    console.log("[ML Agent DEBUG] Textos Avantpro encontrados:", JSON.stringify(avantTexts, null, 2));
  } else {
    console.log("[ML Agent DEBUG] Nenhum texto Avantpro encontrado ainda.");
  }
}

function run() {
  const catalogId = extractCatalogId();
  const itemId = extractItemId();

  if (!catalogId && !itemId) {
    return; // Não é página de produto/catálogo
  }

  // Polling: tenta a cada 2s por até 30s, até a Avantpro carregar os dados
  let tentativas = 0;
  const MAX_TENTATIVAS = 15;
  const intervalo = setInterval(() => {
    tentativas++;
    const data = findAvantproData();
    const temDados = Object.keys(data).length > 0;
    // Completo = campos-chave para previsibilidade carregaram
    const completo =
      data.faturamento != null &&
      data.vendas_mensais != null &&
      data.anuncio_criado_dias != null;

    if (completo || (temDados && tentativas >= MAX_TENTATIVAS)) {
      console.log(`[ML Agent] Dados na tentativa ${tentativas}:`, data);
      sendToBackend(catalogId, itemId, data);
      clearInterval(intervalo);
      notificarBackground(catalogId || itemId, true);
    } else if (tentativas >= MAX_TENTATIVAS) {
      console.log("[ML Agent] Desistiu após", tentativas, "tentativas — Avantpro não carregou.");
      debugAvantproHTML();
      clearInterval(intervalo);
      notificarBackground(catalogId || itemId, false);
    }
  }, 2000);
}

// Avisa o service worker (background) que a coleta desta aba terminou,
// para que ele possa fechá-la e seguir para a próxima da fila.
function notificarBackground(catalogId, sucesso) {
  try {
    if (chrome?.runtime?.sendMessage) {
      chrome.runtime.sendMessage({ tipo: "coleta_concluida", catalogId, sucesso });
    }
  } catch (e) {
    // ignora — pode estar rodando fora do contexto da extensão
  }
}

// Roda na carga inicial
run();

// Roda também em navegação SPA (ML usa React)
let lastUrl = window.location.href;
const observer = new MutationObserver(() => {
  if (window.location.href !== lastUrl) {
    lastUrl = window.location.href;
    setTimeout(run, 1000);
  }
});
observer.observe(document.body, { childList: true, subtree: true });
