from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List, Dict
import json
import os
import subprocess
import threading

app = FastAPI(title="VPG Atendimento Whatsapp")

# DICIONÁRIOS MULTI-TENANT (Isolamento de Sessões)
BOT_PROCESSES: Dict[str, subprocess.Popen] = {}
BOT_STATES: Dict[str, str] = {}
LAST_QR: Dict[str, str] = {}
connected_clients: Dict[str, List[WebSocket]] = {}

# ── Modelos ──────────────────────────────────────────────────────────────────
class MenuItem(BaseModel):
    id: str
    text: str
    children: Optional[List["MenuItem"]] = []
    final_response: Optional[str] = None

MenuItem.model_rebuild()

class BotConfig(BaseModel):
    business_name: str
    business_logo: Optional[str] = None
    welcome_message: str
    menu: List[MenuItem]
    show_products: bool = False
    enable_scheduling: bool = False
    scheduling_days_ahead: int = 7
    show_price_on_schedule: bool = True

# ── Helpers (ISOLADOS POR SESSÃO) ─────────────────────────────────────────────
def get_data_file(sessao: str) -> str:
    return f"data/config_{sessao}.json"

def load_config(sessao: str) -> dict:
    path = get_data_file(sessao)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "business_name": "Meu Negócio",
        "business_logo": "",
        "welcome_message": "Olá! Bem-vindo. Como posso ajudar?",
        "menu": [],
        "show_products": False,
        "enable_scheduling": False,
        "scheduling_days_ahead": 7,
        "show_price_on_schedule": True
    }

def save_config(sessao: str, config: dict):
    os.makedirs("data", exist_ok=True)
    with open(get_data_file(sessao), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

async def broadcast(sessao: str, message: dict):
    if sessao in connected_clients:
        for ws in connected_clients[sessao][:]:
            try:
                await ws.send_json(message)
            except:
                connected_clients[sessao].remove(ws)

# ── WebSocket para QR code ───────────────────────────────────────────────────
@app.websocket("/ws/{sessao}")
async def websocket_endpoint(websocket: WebSocket, sessao: str):
    await websocket.accept()
    if sessao not in connected_clients:
        connected_clients[sessao] = []
    connected_clients[sessao].append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("type") == "bot_status":
                BOT_STATES[sessao] = message.get("status")
            elif message.get("type") == "qr":
                BOT_STATES[sessao] = "waiting_qr"
                LAST_QR[sessao] = message.get("data")
                
            if message.get("type") in ["qr", "bot_status"]:
                await broadcast(sessao, message)
    except WebSocketDisconnect:
        if websocket in connected_clients[sessao]:
            connected_clients[sessao].remove(websocket)
    except Exception as e:
        if websocket in connected_clients[sessao]:
            connected_clients[sessao].remove(websocket)

# ── API Routes ────────────────────────────────────────────────────────────────
@app.get("/api/config")
def get_config(sessao: str = "default"):
    return load_config(sessao)

@app.post("/api/config")
def update_config(config: BotConfig, sessao: str = "default"):
    save_config(sessao, config.model_dump())
    return {"status": "ok", "message": "Configuração salva com sucesso!"}

@app.post("/api/bot/start")
async def start_bot(sessao: str = "default"):
    if sessao in BOT_PROCESSES and BOT_PROCESSES[sessao].poll() is None:
        return {"status": "already_running"}
    generate_bot_js(sessao)
    try:
        process = subprocess.Popen(
            ["node", f"bot_{sessao}.js"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        BOT_PROCESSES[sessao] = process
        BOT_STATES[sessao] = "starting"
        threading.Thread(target=read_bot_output, args=(sessao,), daemon=True).start()
        return {"status": "started"}
    except FileNotFoundError:
        return {"status": "error", "message": "Node.js não encontrado."}

@app.post("/api/bot/stop")
async def stop_bot(sessao: str = "default"):
    if sessao in BOTPROCESSES and BOT_PROCESSES[sessao]:
        BOT_PROCESSES[sessao].terminate()
        del BOT_PROCESSES[sessao]
        BOT_STATES[sessao] = "stopped"
        await broadcast(sessao, {"type": "bot_status", "status": "stopped"})
        return {"status": "stopped"}
    return {"status": "not_running"}

@app.get("/api/bot/status")
def bot_status(sessao: str = "default"):
    if sessao in BOT_PROCESSES and BOT_PROCESSES[sessao].poll() is None:
        state = BOT_STATES.get(sessao, "starting")
        qr_data = LAST_QR.get(sessao, "") if state == "waiting_qr" else ""
        return {"status": state, "qr": qr_data}
    return {"status": "stopped"}


# ── Bot JS Generator (Motor de Agendamento Nativo) ─────────────────────────────
def generate_bot_js(sessao: str):
    config = load_config(sessao)
    menu_json = json.dumps(config["menu"], ensure_ascii=False)
    welcome = config["welcome_message"].replace("`", "\\`").replace('"', '\\"')
    show_products = str(config.get("show_products", False)).lower()
    enable_scheduling = str(config.get("enable_scheduling", False)).lower()
    show_price = str(config.get("show_price_on_schedule", True)).lower()
    days_ahead = config.get("scheduling_days_ahead", 7)
    
    bot_code = f"""const {{ Client, LocalAuth }} = require('whatsapp-web.js');
const qrcode = require('qrcode');
const WebSocket = require('ws');
const axios = require('axios'); 

const ws = new WebSocket('ws://127.0.0.1:8000/ws/{sessao}');
const menuBase = {menu_json};
const userState = {{}}; 

const showProducts = {show_products};
const enableScheduling = {enable_scheduling};
const showPriceOnSchedule = {show_price};
const daysAhead = {days_ahead};

const delay = ms => new Promise(res => setTimeout(res, ms));
const botMessages = {{}}; 
let servicosCadastrados = [];

async function fetchServicos() {{
    try {{
        const res = await axios.get(`https://app.vpgsolucoes.com.br/api/bot/servicos?estabelecimento_id={sessao}`);
        if(res.data && res.data.servicos) {{
            servicosCadastrados = res.data.servicos;
        }}
    }} catch(err) {{ 
        console.error("Erro ao buscar servicos com Axios:", err.message); 
    }}
}}

const client = new Client({{
  authStrategy: new LocalAuth({{ clientId: "client-{sessao}", dataPath: "./.wwebjs_auth_{sessao}" }}),
  authTimeoutMs: 0,
  puppeteer: {{ 
    executablePath: '/usr/bin/google-chrome-stable',
    headless: true,
    protocolTimeout: 0,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-accelerated-2d-canvas', '--no-first-run', '--no-zygote', '--disable-gpu']
  }}
}});

client.on('qr', async (qr) => {{
  try {{
    const qrDataUrl = await qrcode.toDataURL(qr);
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({{ type: 'qr', data: qrDataUrl }}));
  }} catch (err) {{}}
}});

client.on('ready', () => {{
  console.log('Bot conectado (Sessao: {sessao})!');
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({{ type: 'bot_status', status: 'connected' }}));
}});

function buildMenuText(items) {{
  return items.map((item, idx) => `*${{idx + 1}}.* ${{item.text}}`).join('\\n');
}}

function getNextDaysList() {{
    let days = [];
    let curr = new Date();
    const week = ['Dom','Seg','Ter','Qua','Qui','Sex','Sáb'];

    let dHoje = String(curr.getDate()).padStart(2, '0');
    let mHoje = String(curr.getMonth() + 1).padStart(2, '0');
    days.push(`Hoje ${{dHoje}}/${{mHoje}}`);

    for(let i=1; i<=daysAhead; i++) {{
        curr.setDate(curr.getDate() + 1);
        let d = String(curr.getDate()).padStart(2, '0');
        let m = String(curr.getMonth() + 1).padStart(2, '0');
        days.push(`${{week[curr.getDay()]}} ${{d}}/${{m}}`);
    }}
    return days;
}}

function getActiveMenu() {{
    let activeMenu = JSON.parse(JSON.stringify(menuBase));
    if (showProducts) activeMenu.push({{ text: "📋 Nossos Serviços/Produtos", is_catalogo: true }});
    if (enableScheduling) activeMenu.push({{ text: "📅 Agendar Horário", is_agendamento: true }});
    return activeMenu;
}}

client.on('message_create', async (msg) => {{
  try {{
    const from = msg.from;
    const body = msg.body ? msg.body.trim() : "";
    const textLower = body.toLowerCase();
    
    const chatId = msg.fromMe ? msg.to : msg.from;

    if (chatId === 'status@broadcast' || chatId.includes('@newsletter')) return;

    if (!userState[chatId]) userState[chatId] = {{ active: false, flow: 'menu' }};

    // ==============================================================
    // 1. TRAVA DE AUTO-LOOP E INTERVENÇÃO HUMANA
    // ==============================================================
    if (msg.fromMe) {{
        if (msg.to === msg.from) return; 
        if (botMessages[chatId] && (Date.now() - botMessages[chatId] < 5000)) return;

        userState[chatId].lastOwnerMessage = Date.now();
        userState[chatId].active = false;
        return; 
    }}

    // ==============================================================
    // 2. EXTRAIR O NÚMERO DE TELEFONE REAL DO CLIENTE
    // ==============================================================
    if (!userState[from].realPhone) {{
        let rawNum = from.split('@')[0]; 
        try {{
            const contact = await msg.getContact();
            if (contact && contact.id && contact.id.user) {{ rawNum = contact.id.user; }}
        }} catch (e) {{}}

        if (rawNum.startsWith('55') && (rawNum.length === 13 || rawNum.length === 12)) {{
            rawNum = rawNum.substring(2);
        }}
        userState[from].realPhone = rawNum;
    }}

    // ==============================================================
    // 3. VERIFICAR TRAVAS DE TEMPO
    // ==============================================================
    const TWELVE_HOURS = 12 * 60 * 60 * 1000;
    if (userState[from].lastOwnerMessage && (Date.now() - userState[from].lastOwnerMessage < TWELVE_HOURS)) {{
        if (textLower !== 'menu') return;
    }}

    if (userState[from].cooldownUntil && (Date.now() < userState[from].cooldownUntil)) {{
        if (textLower !== 'menu') return;
    }}

    let chat;
    try {{ chat = await msg.getChat(); }} catch (e) {{}}
    
    // ==============================================================
    // 4. FUNÇÃO CENTRAL DE ENVIO
    // ==============================================================
    const send = async (text) => {{
        try {{ if (chat) await chat.sendStateTyping(); }} catch(e) {{}}
        
        const waitTime = Math.floor(Math.random() * 1500) + 1000; 
        await delay(waitTime);
        
        try {{
            botMessages[from] = Date.now();
            if (chat) await chat.sendMessage(text);
            else await client.sendMessage(from, text);
        }} catch (err) {{
            console.error('Erro de rede ao enviar:', err);
        }}
    }};

    // ==============================================================
    // 5. ENTRADA PRINCIPAL / RESET DO MENU
    // ==============================================================
    const isGreeting = ['menu', 'oi', 'olá', 'ola', 'bom dia', 'boa tarde', 'boa noite', '0'].includes(textLower);

    if (!userState[from].active || isGreeting) {{
      userState[from].active = true;
      userState[from].lastOwnerMessage = 0; 
      userState[from].cooldownUntil = 0;    
      userState[from].path = [];
      userState[from].flow = 'menu';
      
      if(showProducts) await fetchServicos(); 
      await send(`{welcome}\\n\\n${{buildMenuText(getActiveMenu())}}\\n\\n_Digite o número da opção desejada._`);
      return;
    }}

    // ==========================================================
    // INICIAR A LISTA DE SERVIÇOS 
    // ==========================================================
    async function startSchedulingFlow(userFrom) {{
        userState[userFrom].flow = 'schedule_service';
        if (servicosCadastrados.length === 0) await fetchServicos();
        if (servicosCadastrados.length === 0) {{
            await send('❌ Desculpe, não há serviços cadastrados no sistema para agendar no momento.');
            userState[userFrom].active = false; return;
        }}
        let servText = servicosCadastrados.map((s, i) => {{
            let priceStr = showPriceOnSchedule ? ` - R$ ${{(s.valor || 0).toFixed(2).replace('.', ',')}}` : '';
            return `*${{i+1}}.* ${{s.nome}}${{priceStr}}`;
        }}).join('\\n');
        await send(`📅 *Agendamento*\\n\\nQual serviço você deseja realizar?\\n\\n${{servText}}\\n\\n_Digite o número correspondente ao serviço:_`);
    }}

    // ==========================================
    // MÁQUINA DE ESTADOS - AGENDAMENTO NATIVO
    // ==========================================
    if (userState[from].flow.startsWith('schedule_')) {{
        const choice = parseInt(body) - 1;
        
        // --- ETAPA: AÇÃO AGENDAMENTO EXISTENTE ---
        if (userState[from].flow === 'schedule_existing_action') {{
            if (body === '1') {{
                // Mostrar os agendamentos salvos na memória
                let text = `📋 *Seus agendamentos em aberto:*\\n\\n`;
                if(userState[from].existing_appointments) {{
                    userState[from].existing_appointments.forEach(a => {{
                        text += `📅 *${{a.data}}* às *${{a.hora}}* - ${{a.servico || 'Serviço'}}\\n`;
                    }});
                }}
                text += `\\n_Digite *menu* para voltar._`;
                await send(text);
                userState[from].active = false;
            }} else if (body === '2') {{
                await startSchedulingFlow(from);
            }} else {{
                await send("❌ Opção inválida.\\nDigite *1* para Consultar, *2* para Novo Agendamento ou *menu* para voltar.");
            }}
            return;
        }}

        // --- ETAPA: ESCOLHER SERVIÇO E FILTRAR DIAS ---
        if (userState[from].flow === 'schedule_service') {{
            if (isNaN(choice) || choice < 0 || choice >= servicosCadastrados.length) {{
                await send('❌ Serviço inválido. Escolha um número da lista.'); return;
            }}
            const servicoObj = servicosCadastrados[choice];
            userState[from].selectedService = servicoObj.nome;
            userState[from].selectedServiceId = servicoObj.id;
            
            await send("⏳ Cruzando sua escolha com nossa agenda oficial para encontrar os melhores dias...");
            
            const daysToCheck = getNextDaysList();
            const availableDays = [];
            
            const checks = daysToCheck.map(async (dayStr) => {{
                try {{
                    const res = await axios.post('https://app.vpgsolucoes.com.br/api/bot/horarios-livres', {{
                        estabelecimento_id: parseInt('{sessao}'), servico_id: userState[from].selectedServiceId, data: dayStr
                    }});
                    if (res.data && res.data.horarios && res.data.horarios.length > 0) {{
                        return {{ day: dayStr, horarios: res.data.horarios }};
                    }}
                }} catch(e) {{}}
                return null;
            }});
            
            const results = await Promise.all(checks);
            results.forEach(r => {{ if(r) availableDays.push(r); }});
            
            if (availableDays.length === 0) {{
                await send(`Poxa, não temos nenhum dia com horários livres para esse serviço nos próximos ${{daysAhead}} dias.\\n\\nDigite *menu* e tente escolher outro serviço.`);
                userState[from].active = false;
                return;
            }}
            
            userState[from].availableDaysData = availableDays;
            userState[from].flow = 'schedule_day';
            
            let daysText = availableDays.map((d, i) => `*${{i+1}}.* ${{d.day}}`).join('\\n');
            await send(`Você escolheu *${{servicoObj.nome}}*.\\n\\nTemos vagas abertas nestes dias:\\n\\n${{daysText}}\\n\\n_Digite o número correspondente ao dia:_`);
            return;
        }}

        // --- ETAPA: ESCOLHER HORA ---
        if (userState[from].flow === 'schedule_day') {{
            const availableDays = userState[from].availableDaysData;
            if (isNaN(choice) || choice < 0 || choice >= availableDays.length) {{
                await send('❌ Opção inválida. Escolha um número da lista de dias.'); return;
            }}
            
            const selectedDayData = availableDays[choice];
            userState[from].selectedDay = selectedDayData.day;
            userState[from].horariosDisponiveis = selectedDayData.horarios;
            
            userState[from].flow = 'schedule_time';
            
            let timeText = selectedDayData.horarios.map((h, i) => `*${{i+1}}.* ${{h}}`).join('\\n');
            await send(`Ótimo! Estes são os horários livres para *${{selectedDayData.day}}*:\\n\\n${{timeText}}\\n\\n_Digite o número do horário desejado:_`);
            return;
        }}

        // --- ETAPA: FINALIZAÇÃO ---
        if (userState[from].flow === 'schedule_time') {{
            const horarios = userState[from].horariosDisponiveis;
            if (isNaN(choice) || choice < 0 || choice >= horarios.length) {{
                await send('❌ Horário inválido. Escolha um número da lista.'); return;
            }}
            userState[from].selectedTime = horarios[choice];
            await checkCadastro(from, send);
            return;
        }}

        if (userState[from].flow === 'schedule_cadastro') {{
            userState[from].nome = body;
            await finalizarAgendamento(from, send);
            return;
        }}
    }}

    async function checkCadastro(userFrom, sendFunc) {{
        const clientPhone = userState[userFrom].realPhone;
        try {{
            const res = await axios.post('https://app.vpgsolucoes.com.br/api/bot/check-cliente', {{ 
                whatsapp: clientPhone, estabelecimento_id: parseInt('{sessao}') 
            }});

            if (res.data.registrado) {{
                userState[userFrom].nome = res.data.nome;
                await finalizarAgendamento(userFrom, sendFunc);
            }} else {{
                userState[userFrom].flow = 'schedule_cadastro';
                await sendFunc("Notei que ainda não tem cadastro com a gente. Por favor, digite seu *Nome e Sobrenome* para confirmar a reserva:");
            }}
        }} catch (err) {{ 
            userState[userFrom].flow = 'schedule_cadastro';
            await sendFunc("Por favor, digite seu *Nome e Sobrenome* para confirmar a reserva na agenda:");
        }}
    }}

    async function finalizarAgendamento(userFrom, sendFunc) {{
        const s = userState[userFrom];
        const clientPhone = s.realPhone;
        let nomeFinal = s.nome || 'Não informado';
        
        let resumo = `✅ *AGENDAMENTO CONFIRMADO!*\\n\\n👤 Cliente: *${{nomeFinal}}*\\n📌 Serviço: *${{s.selectedService}}*\\n📅 Data: *${{s.selectedDay}}*\\n⏰ Horário: *${{s.selectedTime}}*`;
        resumo += `\\n\\nJá anotei na agenda oficial. Te esperamos!\\n\\n_Digite *menu* para voltar ao início._`;
        
        await sendFunc(resumo);
        userState[userFrom].active = false;
        userState[userFrom].cooldownUntil = Date.now() + 60000;
        
        axios.post('https://app.vpgsolucoes.com.br/api/bot/registrar-agendamento', {{
            whatsapp: clientPhone, nome: nomeFinal, servico_id: s.selectedServiceId, data: s.selectedDay, hora: s.selectedTime, estabelecimento_id: parseInt('{sessao}')
        }}).catch(err => console.log('Erro ao salvar no painel:', err.message));
    }}

    // ==========================================
    // FLUXO DO MENU PRINCIPAL
    // ==========================================
    if (!userState[from].flow.startsWith('schedule_')) {{
        const activeMenu = getActiveMenu();
        const choice = parseInt(body) - 1;
        
        if (isNaN(choice) || choice < 0) {{
          await send('❌ Opção inválida. Digite um número.'); return;
        }}

        let currentPath = userState[from].path;
        let targetLevel = [...currentPath, choice];
        let tempMenu = activeMenu;
        let item = null;

        for (const idx of targetLevel) {{
          if (!tempMenu || !tempMenu[idx]) {{ 
              await send('❌ Opção inválida. Digite o número correspondente.'); return; 
          }}
          item = tempMenu[idx];
          tempMenu = item.children || [];
        }}

        if (item.is_catalogo) {{
            let servText = servicosCadastrados.map(s => `✔️ ${{s.nome}} - R$ ${{(s.valor || 0).toFixed(2).replace('.', ',')}}`).join('\\n');
            await send(`📋 *Nossos Serviços*\\n\\n${{servText}}\\n\\n_Digite *menu* para voltar._`);
            userState[from].active = false;
            userState[from].cooldownUntil = Date.now() + 60000; 
            return;
        }}

        if (item.is_agendamento) {{
            // A verificação agora é totalmente silenciosa, sem texto!
            try {{
                const res = await axios.post('https://app.vpgsolucoes.com.br/api/bot/check-agendamentos', {{
                    whatsapp: userState[from].realPhone, estabelecimento_id: parseInt('{{sessao}}')
                }});
                
                // Se a API encontrar agendamentos, mostra as opções
                if (res.data && res.data.agendamentos && res.data.agendamentos.length > 0) {{
                    userState[from].existing_appointments = res.data.agendamentos;
                    userState[from].flow = 'schedule_existing_action';
                    
                    let text = `Notei que já consta um agendamento em aberto no sistema.\n\n*1* - Consultar meu agendamento\n*2* - Novo Agendamento\n\n_Digite *menu* para voltar._`;
                    await send(text);
                    return;
                }}
            }} catch(e) {{
                console.log("Erro ao checar agendamentos:", e.message);
            }}
            
            // Se não tiver agendamento ou a API falhar, vai direto perguntar o serviço
            await startSchedulingFlow(from);
            return;
        }}

        if (item.final_response || !item.children || item.children.length === 0) {{
          await send(item.final_response || 'Obrigado pelo contato!');
          await send('_Digite *menu* para voltar ao início._');
          userState[from].active = false;
          userState[from].cooldownUntil = Date.now() + 60000; 
        }} else {{
          userState[from].path = targetLevel;
          userState[from].active = true; 
          await send(`*${{item.text}}*\\n\\n${{buildMenuText(item.children)}}\\n\\n_Escolha uma opção:_`);
        }}
    }}
  }} catch (error) {{ console.error("Erro:", error); }}
}});

client.initialize();
"""
    with open(f"bot_{sessao}.js", "w", encoding="utf-8") as f:
        f.write(bot_code)
        
def read_bot_output(sessao: str):
    process = BOT_PROCESSES.get(sessao)
    if not process: return
    for line in process.stdout:
        pass 

@app.get("/", response_class=HTMLResponse)
def root():
    return FileResponse("templates/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    os.makedirs("data", exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)