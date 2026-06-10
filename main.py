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
    show_products: bool = False  # <-- NOVA CHAVE AQUI

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
        "show_products": False 
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

# ── WebSocket para QR code (ISOLADO POR SESSÃO) ───────────────────────────────
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
            
            # SALVANDO NA MEMÓRIA DO SERVIDOR
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
        print(f"Erro no WebSocket da sessão {sessao}: {e}")
        if websocket in connected_clients[sessao]:
            connected_clients[sessao].remove(websocket)

# ── API Routes (COM PARÂMETRO SESSÃO) ─────────────────────────────────────────
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
    if sessao in BOT_PROCESSES and BOT_PROCESSES[sessao]:
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


# ── Bot JS Generator (ISOLADO POR SESSÃO) ─────────────────────────────────────
def generate_bot_js(sessao: str):
    config = load_config(sessao)
    menu_json = json.dumps(config["menu"], ensure_ascii=False)
    welcome = config["welcome_message"].replace("`", "\\`").replace('"', '\\"')
    
    bot_code = f"""const {{ Client, LocalAuth }} = require('whatsapp-web.js');
const qrcode = require('qrcode');
const WebSocket = require('ws');

const ws = new WebSocket('ws://127.0.0.1:8000/ws/{sessao}');
const menu = {menu_json};
const userState = {{}}; 

const client = new Client({{
  authStrategy: new LocalAuth({{
    clientId: "client-{sessao}",
    dataPath: "./.wwebjs_auth_{sessao}"
  }}),
  authTimeoutMs: 0,
  puppeteer: {{ 
    executablePath: '/usr/bin/google-chrome-stable',
    headless: true,
    protocolTimeout: 0,
    args: [
      '--no-sandbox', 
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-canvas',
      '--no-first-run',
      '--no-zygote',
      '--disable-gpu'
    ]
  }}
}});

client.on('qr', async (qr) => {{
  try {{
    const qrDataUrl = await qrcode.toDataURL(qr);
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({{ type: 'qr', data: qrDataUrl }}));
  }} catch (err) {{
    console.error('Erro no QR:', err);
  }}
}});

client.on('ready', () => {{
  console.log('Bot conectado (Sessao: {sessao})!');
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({{ type: 'bot_status', status: 'connected' }}));
}});

function buildMenuText(items) {{
  return items.map((item, idx) => `*${{idx + 1}}.* ${{item.text}}`).join('\\n');
}}

client.on('message', async (msg) => {{
  try {{
    const from = msg.from;
    const body = msg.body ? msg.body.trim() : "";

    if (msg.fromMe) return;
    if (from === 'status@broadcast' || from.includes('@newsletter')) return;

    let chat;
    try {{ chat = await msg.getChat(); }} catch (e) {{}}

    const send = async (text) => {{
      if (chat) return await chat.sendMessage(text);
      return await client.sendMessage(from, text);
    }};

    if (body.toLowerCase() === 'menu' || body === '0') {{
      userState[from] = {{ path: [], active: true }};
      await send(`{welcome}\\n\\n${{buildMenuText(menu)}}\\n\\n_Digite o número da opção desejada._`);
      return;
    }}

    if (!userState[from] || !userState[from].active) {{
      userState[from] = {{ path: [], active: true }};
      await send(`{welcome}\\n\\n${{buildMenuText(menu)}}\\n\\n_Como posso ajudar? Digite uma opção:_`);
      return;
    }}

    const choice = parseInt(body) - 1;
    if (isNaN(choice) || choice < 0) {{
      await send('❌ Opção inválida. Digite o número ou *menu* para reiniciar.');
      return;
    }}

    let currentPath = userState[from].path;
    let targetLevel = [...currentPath, choice];
    let tempMenu = menu;
    let item = null;

    for (const idx of targetLevel) {{
      if (!tempMenu[idx]) {{
        await send('❌ Opção inválida.');
        return;
      }}
      item = tempMenu[idx];
      tempMenu = item.children || [];
    }}

    if (item.final_response || !item.children || item.children.length === 0) {{
      await send(item.final_response || 'Obrigado pelo contato!');
      await send('_Digite *menu* para voltar ao início._');
      userState[from].active = false;
    }} else {{
      userState[from].path = targetLevel;
      await send(`*${{item.text}}*\\n\\n${{buildMenuText(item.children)}}\\n\\n_Escolha uma opção:_`);
    }}

  }} catch (error) {{
    console.error("Erro:", error);
  }}
}});

client.initialize();
"""
    with open(f"bot_{sessao}.js", "w", encoding="utf-8") as f:
        f.write(bot_code)
        
def read_bot_output(sessao: str):
    process = BOT_PROCESSES.get(sessao)
    if not process: return
    for line in process.stdout:
        print(f"[BOT {sessao}] {line.strip()}")

@app.get("/", response_class=HTMLResponse)
def root():
    return FileResponse("templates/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    os.makedirs("data", exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)