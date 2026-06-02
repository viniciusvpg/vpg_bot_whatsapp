const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode');
const WebSocket = require('ws');

const ws = new WebSocket('ws://127.0.0.1:8000/ws');
const menu = [{"id": "1", "text": "📦 Produtos", "children": [{"id": "1.1", "text": "Ver catálogo", "children": [], "final_response": "Acesse nosso catálogo em: catalogo.com"}, {"id": "1.2", "text": "Fazer pedido", "children": [], "final_response": "Para fazer um pedido, entre em contato pelo e-mail: pedidos@negocio.com"}], "final_response": null}, {"id": "2", "text": "💬 Suporte", "children": [{"id": "2.1", "text": "Problema técnico", "children": [], "final_response": "Nossa equipe técnica entrará em contato em até 24h."}, {"id": "2.2", "text": "Dúvidas gerais", "children": [], "final_response": "Respondemos todas as dúvidas em até 2 horas!"}], "final_response": null}, {"id": "3", "text": "📍 Localização", "children": [], "final_response": "Rua João Back - Vila Nova, Ituporanga - SC, 88400-000."}];
const userState = {}; 

const client = new Client({
  authStrategy: new LocalAuth({
        clientId: "client-one",
        dataPath: "./.wwebjs_auth"
    }),
  authTimeoutMs: 0,
  webVersionCache: {
    type: 'remote',
    remotePath: 'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html/2.2412.54.html',
  },
  puppeteer: { 
    headless: true,
    protocolTimeout: 0,
    args: [
      '--no-sandbox', 
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-canvas',
      '--no-first-run',
      '--no-zygote',
      '--single-process'
    ]
  }
});

client.on('qr', async (qr) => {
  try {
    const qrDataUrl = await qrcode.toDataURL(qr);
    const msg = JSON.stringify({ type: 'qr', data: qrDataUrl });
    if (ws.readyState === WebSocket.OPEN) ws.send(msg);
  } catch (err) {
    console.error('Erro no QR:', err);
  }
});

client.on('ready', () => {
  console.log('Bot conectado!');
  const msg = JSON.stringify({ type: 'bot_status', status: 'connected' });
  if (ws.readyState === WebSocket.OPEN) ws.send(msg);
});

function buildMenuText(items) {
  return items.map((item, idx) => `*${idx + 1}.* ${item.text}`).join('\n');
}

client.on('message', async (msg) => {
  try {
    const from = msg.from;
    const body = msg.body ? msg.body.trim() : "";

    if (msg.fromMe) return;
    if (from === 'status@broadcast' || from.includes('@newsletter')) return;

    let chat;
    try { chat = await msg.getChat(); } catch (e) {}

    const send = async (text) => {
      if (chat) return await chat.sendMessage(text);
      return await client.sendMessage(from, text);
    };

    // RESET: Se o usuário digitar 'menu' ou '0'
    if (body.toLowerCase() === 'menu' || body === '0') {
      userState[from] = { path: [], active: true };
      const menuText = buildMenuText(menu);
      await send(`Olá! Bem-vindo. Como posso ajudar?\n\n${menuText}\n\n_Digite o número da opção desejada._`);
      return;
    }

    // INÍCIO: Se não houver estado ativo, inicia o bot
    if (!userState[from] || !userState[from].active) {
      userState[from] = { path: [], active: true };
      const menuText = buildMenuText(menu);
      await send(`Olá! Bem-vindo. Como posso ajudar?\n\n${menuText}\n\n_Como posso ajudar? Digite uma opção:_`);
      return;
    }

    // NAVEGAÇÃO: Só entra aqui se o estado estiver ativo
    const choice = parseInt(body) - 1;
    if (isNaN(choice) || choice < 0) {
      await send('❌ Opção inválida. Digite o número ou *menu* para reiniciar.');
      return;
    }

    let currentPath = userState[from].path;
    let targetLevel = [...currentPath, choice];
    let tempMenu = menu;
    let item = null;

    for (const idx of targetLevel) {
      if (!tempMenu[idx]) {
        await send('❌ Opção inválida.');
        return;
      }
      item = tempMenu[idx];
      tempMenu = item.children || [];
    }

    if (item.final_response || !item.children || item.children.length === 0) {
      await send(item.final_response || 'Obrigado pelo contato!');
      await send('_Digite *menu* para voltar ao início._');
      userState[from].active = false; // Desativa para a próxima msg reiniciar o menu
    } else {
      userState[from].path = targetLevel; // Atualiza o caminho para o próximo nível
      const subMenu = buildMenuText(item.children);
      await send(`*${item.text}*\n\n${subMenu}\n\n_Escolha uma opção:_`);
    }

  } catch (error) {
    console.error("Erro:", error);
  }
});

client.initialize();
