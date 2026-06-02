# 🤖 WhatsApp Bot Manager

Bot de atendimento para WhatsApp com interface web de gerenciamento.

## 📋 Requisitos

| Requisito | Versão mínima | Download |
|-----------|---------------|----------|
| Python    | 3.9+          | [python.org](https://python.org) |
| Node.js   | 18+           | [nodejs.org](https://nodejs.org) |

## 🚀 Instalação

### Linux / Mac
```bash
chmod +x install.sh
./install.sh
```

### Windows
```
install.bat
```

### Manual
```bash
pip install -r requirements.txt
npm install
```

## ▶️ Executar

```bash
python3 main.py
```

Acesse: **http://localhost:8000**

---

## 📱 Como usar

### Aba "Conexão"
1. Clique em **"Iniciar Bot"**
2. Aguarde o QR Code aparecer
3. No WhatsApp: **⋮ → Dispositivos vinculados → Vincular um dispositivo**
4. Escaneie o QR Code
5. O bot ficará ativo enquanto o servidor estiver rodando

### Aba "Configurações"
- **Nome e boas-vindas**: Personalize o nome do negócio e mensagem inicial
- **Fluxo de atendimento**: Monte o menu com até 2 níveis
  - Cada opção pode ter subopções **ou** uma resposta final
  - Use emojis para deixar mais amigável: `📦 Produtos`, `💬 Suporte`

### Comandos do cliente
| Comando | Ação |
|---------|------|
| `oi` / `olá` / `menu` / `0` | Reinicia o atendimento e mostra o menu |
| `1`, `2`, `3`... | Seleciona a opção correspondente |

---

## 📁 Estrutura

```
whatsapp-bot/
├── main.py           # Servidor FastAPI (backend)
├── bot.js            # Bot WhatsApp (gerado automaticamente)
├── templates/
│   └── index.html    # Interface web
├── data/
│   └── config.json   # Configurações salvas
├── requirements.txt
├── package.json
└── README.md
```

## ⚠️ Notas

- A sessão do WhatsApp fica salva na pasta `.wwebjs_auth` — não precisa escanear o QR toda vez
- Não feche o terminal enquanto o bot estiver ativo
- Para usar em produção, configure um servidor Linux com `screen` ou `pm2`

### Usar com PM2 (produção)
```bash
npm install -g pm2
python3 main.py &  # inicia o servidor Python
pm2 start bot.js   # gerencia o processo Node com reinício automático
```
