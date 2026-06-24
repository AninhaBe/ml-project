// Service worker: coletor automático Avantpro.
// Faz polling da fila no backend, abre cada URL numa aba de fundo,
// espera o content_script raspar e avisar, fecha a aba e segue.

const BACKEND = "http://localhost:8000";
const POLL_MS = 4000;        // intervalo de checagem da fila quando ocioso
const COLETA_TIMEOUT_MS = 40000; // tempo máx. por aba antes de desistir

let coletando = false;       // trava: só 1 aba por vez
let abaAtual = null;         // { tabId, catalogId, timer }

async function pegarProxima() {
  try {
    const r = await fetch(`${BACKEND}/api/avantpro-fila/proxima`);
    return await r.json();
  } catch (e) {
    return { catalog_id: null, url: null };
  }
}

async function reportarErro(catalogId) {
  try {
    await fetch(`${BACKEND}/api/avantpro-fila/erro`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ catalog_id: catalogId }),
    });
  } catch (e) {}
}

function fecharAba() {
  if (!abaAtual) return;
  const { tabId, timer } = abaAtual;
  if (timer) clearTimeout(timer);
  if (tabId != null) {
    chrome.tabs.remove(tabId).catch(() => {});
  }
  abaAtual = null;
  coletando = false;
}

async function coletarUma() {
  if (coletando) return;
  const { catalog_id, url } = await pegarProxima();
  if (!catalog_id || !url) return; // fila vazia

  coletando = true;
  console.log(`[ML Agent BG] Coletando ${catalog_id}: ${url}`);

  // Abre aba de fundo (não rouba foco)
  chrome.tabs.create({ url, active: false }, (tab) => {
    if (chrome.runtime.lastError || !tab) {
      console.warn("[ML Agent BG] Falha ao abrir aba:", chrome.runtime.lastError);
      reportarErro(catalog_id);
      coletando = false;
      return;
    }
    // Timeout de segurança: se o content_script não avisar, fecha e marca erro
    const timer = setTimeout(() => {
      console.warn(`[ML Agent BG] Timeout em ${catalog_id} — fechando aba.`);
      reportarErro(catalog_id);
      fecharAba();
    }, COLETA_TIMEOUT_MS);

    abaAtual = { tabId: tab.id, catalogId: catalog_id, timer };
  });
}

// Recebe aviso do content_script de que a coleta terminou
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg?.tipo !== "coleta_concluida") return;
  // Só age se a mensagem veio da aba que ESTE worker abriu
  if (abaAtual && sender.tab && sender.tab.id === abaAtual.tabId) {
    console.log(`[ML Agent BG] Coleta ${abaAtual.catalogId} concluída (sucesso=${msg.sucesso}). Fechando aba.`);
    fecharAba();
  }
});

// Loop principal: tenta coletar periodicamente
setInterval(() => {
  coletarUma();
}, POLL_MS);

// Roda uma vez ao iniciar
coletarUma();

console.log("[ML Agent BG] Service worker iniciado — coletor Avantpro ativo.");
