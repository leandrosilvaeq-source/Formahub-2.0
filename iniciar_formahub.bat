@echo off
title FormaHub 2.0
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Ambiente virtual .venv nao encontrado.
    echo Crie com: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo Iniciando FormaHub 2.0 em http://127.0.0.1:8000
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000"

uvicorn app.main:app --host 127.0.0.1 --port 8000

pause
