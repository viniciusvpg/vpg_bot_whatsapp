from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
import json
import os
import asyncio
import subprocess
import threading
import platform
import hashlib
import hmac
import base64
import uuid as _uuid_mod
from cryptography.fernet import Fernet, InvalidToken

app = FastAPI(title="VPG Atendimento Whatsapp")

DATA_FILE = "data/config.json"
LICENSE_FILE = "data/license.dat"
BOT_PROCESS = None
connected_clients: List[WebSocket] = []

# ── License System ────────────────────────────────────────────────────────────

# Chave secreta interna — NÃO distribua ao cliente
_SECRET = b"p8MeLk78TCrOFczSkWRD3"


def get_machine_id() -> str:
    parts = []
    system_platform = platform.system()
    
    # Tenta pegar o ID no Windows
    if system_platform == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            parts.append(str(guid))
        except Exception:
            pass
            
    # Tenta pegar o ID no Linux (Oracle Cloud)
    elif system_platform == "Linux":
        try:
            if os.path.exists("/etc/machine-id"):
                with open("/etc/machine-id", "r") as f:
                    parts.append(f.read().strip())
        except Exception:
            pass

    # Fallback comum para ambos (Nome da máquina + ID da placa de rede)
    parts.append(platform.node())
    parts.append(str(_uuid_mod.getnode()))
    
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
    return f"{digest[:8]}-{digest[8:16]}-{digest[16:24]}"


def _derive_fernet(machine_id: str) -> Fernet:
    mat = hashlib.sha256((_SECRET.decode() + machine_id).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(mat))


def _expected_code(machine_id: str) -> str:
    sig = hmac.new(_SECRET, machine_id.encode("utf-8"), hashlib.sha256).hexdigest().upper()
    return f"{sig[:4]}-{sig[4:8]}-{sig[8:12]}-{sig[12:16]}"


def verify_activation_code(machine_id: str, code: str) -> bool:
    clean = code.replace("-", "").upper()
    expected = _expected_code(machine_id).replace("-", "")
    return hmac.compare_digest(clean, expected)


def save_license(machine_id: str, code: str):
    os.makedirs("data", exist_ok=True)
    f = _derive_fernet(machine_id)
    payload = f"{machine_id}:{code.replace('-', '').upper()}".encode("utf-8")
    with open(LICENSE_FILE, "wb") as fp:
        fp.write(f.encrypt(payload))


def check_license() -> bool:
    if not os.path.exists(LICENSE_FILE):
        return False
    try:
        machine_id = get_machine_id()
        f = _derive_fernet(machine_id)
        with open(LICENSE_FILE, "rb") as fp:
            encrypted = fp.read()
        payload = f.decrypt(encrypted).decode("utf-8")
        stored_id, stored_code = payload.split(":", 1)
        if stored_id != machine_id:
            return False
        return verify_activation_code(machine_id, stored_code)
    except (InvalidToken, ValueError, Exception):
        return False

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


class ActivationRequest(BaseModel):
    code: str

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "business_name": "Meu Negócio",
        "business_logo": "",
        "welcome_message": "Olá! Bem-vindo. Como posso ajudar?",
        "menu": []
    }

def save_config(config: dict):
    os.makedirs("data", exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

async def broadcast(message: dict):
    for ws in connected_clients[:]:
        try:
            await ws.send_json(message)
        except:
            connected_clients.remove(ws)

# ── WebSocket para QR code ────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            if message.get("type") in ["qr", "bot_status"]:
                await broadcast(message)
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
    except Exception as e:
        print(f"Erro no WebSocket: {e}")
        if websocket in connected_clients:
            connected_clients.remove(websocket)

# ── API Routes ────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    return load_config()

@app.post("/api/config")
def update_config(config: BotConfig):
    save_config(config.model_dump())
    return {"status": "ok", "message": "Configuração salva com sucesso!"}

@app.post("/api/bot/start")
async def start_bot():
    global BOT_PROCESS
    if BOT_PROCESS and BOT_PROCESS.poll() is None:
        return {"status": "already_running"}
    generate_bot_js()
    try:
        BOT_PROCESS = subprocess.Popen(
            ["node", "bot.js"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        threading.Thread(target=read_bot_output, daemon=True).start()
        return {"status": "started"}
    except FileNotFoundError:
        return {"status": "error", "message": "Node.js não encontrado."}

@app.post("/api/bot/stop")
async def stop_bot():
    global BOT_PROCESS
    if BOT_PROCESS:
        BOT_PROCESS.terminate()
        BOT_PROCESS = None
        await broadcast({"type": "bot_status", "status": "stopped"})
        return {"status": "stopped"}
    return {"status": "not_running"}

@app.get("/api/bot/status")
def bot_status():
    global BOT_PROCESS
    if BOT_PROCESS and BOT_PROCESS.poll() is None:
        return {"status": "running"}
    return {"status": "stopped"}


# ── License API ───────────────────────────────────────────────────────────────

@app.get("/api/license/status")
def license_status():
    machine_id = get_machine_id()
    licensed = check_license()
    return {"licensed": licensed, "machine_id": machine_id}


@app.post("/api/license/activate")
def license_activate(body: ActivationRequest):
    machine_id = get_machine_id()
    if not verify_activation_code(machine_id, body.code):
        raise HTTPException(status_code=400, detail="Código de ativação inválido.")
    save_license(machine_id, body.code)
    return {"status": "ok", "message": "Licença ativada com sucesso!"}

# ── Bot JS Generator ──────────────────────────────────────────────────────────

def generate_bot_js():
    config = load_config()
    menu_json = json.dumps(config["menu"], ensure_ascii=False)
    welcome = config["welcome_message"].replace("`", "\\`").replace('"', '\\"')
    
    bot_code = f"""const {{ Client, LocalAuth }} = require('whatsapp-web.js');
const qrcode = require('qrcode');
const WebSocket = require('ws');

const ws = new WebSocket('ws://127.0.0.1:8000/ws');
const menu = {menu_json};
const userState = {{}}; 

const client = new Client({{
  authStrategy: new LocalAuth({{
    clientId: "client-one",
    dataPath: "./.wwebjs_auth"
  }}),
  authTimeoutMs: 0, // Desativa o timeout de autenticação
  puppeteer: {{ 
    executablePath: '/usr/bin/google-chrome-stable', // <--- FORÇA O CHROME DO SISTEMA
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
  }}
}});

client.on('qr', async (qr) => {{
  try {{
    const qrDataUrl = await qrcode.toDataURL(qr);
    const msg = JSON.stringify({{ type: 'qr', data: qrDataUrl }});
    if (ws.readyState === WebSocket.OPEN) ws.send(msg);
  }} catch (err) {{
    console.error('Erro no QR:', err);
  }}
}});

client.on('ready', () => {{
  console.log('Bot conectado!');
  const msg = JSON.stringify({{ type: 'bot_status', status: 'connected' }});
  if (ws.readyState === WebSocket.OPEN) ws.send(msg);
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

    // RESET: Se o usuário digitar 'menu' ou '0'
    if (body.toLowerCase() === 'menu' || body === '0') {{
      userState[from] = {{ path: [], active: true }};
      const menuText = buildMenuText(menu);
      await send(`{welcome}\\n\\n${{menuText}}\\n\\n_Digite o número da opção desejada._`);
      return;
    }}

    // INÍCIO: Se não houver estado ativo, inicia o bot
    if (!userState[from] || !userState[from].active) {{
      userState[from] = {{ path: [], active: true }};
      const menuText = buildMenuText(menu);
      await send(`{welcome}\\n\\n${{menuText}}\\n\\n_Como posso ajudar? Digite uma opção:_`);
      return;
    }}

    // NAVEGAÇÃO: Só entra aqui se o estado estiver ativo
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
      userState[from].active = false; // Desativa para a próxima msg reiniciar o menu
    }} else {{
      userState[from].path = targetLevel; // Atualiza o caminho para o próximo nível
      const subMenu = buildMenuText(item.children);
      await send(`*${{item.text}}*\\n\\n${{subMenu}}\\n\\n_Escolha uma opção:_`);
    }}

  }} catch (error) {{
    console.error("Erro:", error);
  }}
}});

client.initialize();
"""
    with open("bot.js", "w", encoding="utf-8") as f:
        f.write(bot_code)
        
def read_bot_output():
    global BOT_PROCESS
    if not BOT_PROCESS:
        return
    for line in BOT_PROCESS.stdout:
        print(f"[BOT] {line.strip()}")

@app.get("/", response_class=HTMLResponse)
def root():
    return FileResponse("templates/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(DATA_FILE):
        save_config(load_config())
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)