#!/bin/bash
# ─────────────────────────────────────────────
#  WhatsApp Bot Manager - Script de Instalação
# ─────────────────────────────────────────────

set -e

echo ""
echo "🤖 WhatsApp Bot Manager - Instalação"
echo "======================================"
echo ""

# Verifica Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 não encontrado. Instale em: https://python.org"
    exit 1
fi

# Verifica Node.js
if ! command -v node &> /dev/null; then
    echo "❌ Node.js não encontrado."
    echo "   Instale em: https://nodejs.org (versão LTS recomendada)"
    exit 1
fi

echo "✅ Python3: $(python3 --version)"
echo "✅ Node.js: $(node --version)"
echo ""

# Instala dependências Python
echo "📦 Instalando dependências Python..."
pip install -r requirements.txt -q

# Instala dependências Node.js
echo "📦 Instalando dependências Node.js (whatsapp-web.js)..."
echo "   ⚠️  Isso pode levar alguns minutos..."
npm install --silent

echo ""
echo "✅ Instalação concluída!"
echo ""
echo "▶  Para iniciar, execute:"
echo "   python3 main.py"
echo ""
echo "🌐 Acesse em: http://localhost:8000"
echo ""
