@echo off
echo.
echo  WhatsApp Bot Manager - Instalacao
echo  ====================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado. Instale em: https://python.org
    pause
    exit /b 1
)

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Node.js nao encontrado. Instale em: https://nodejs.org
    pause
    exit /b 1
)

echo [OK] Instalando dependencias Python...
pip install -r requirements.txt -q

echo [OK] Instalando dependencias Node.js...
npm install --silent

echo.
echo  Instalacao concluida!
echo.
echo  Para iniciar o bot, execute:  python main.py
echo  Acesse em:                    http://localhost:8000
echo.
pause
