@echo off
setlocal

echo [1/4] Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no está instalado o no está en PATH.
    exit /b 1
)

echo [2/4] Creando entorno virtual...
if not exist ".venv" (
    python -m venv .venv
)

echo [3/4] Actualizando pip...
call .venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1

echo [4/4] Instalando dependencias...
call .venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo Dependencias instaladas correctamente.
echo Para ejecutar: .venv\Scripts\python.exe main.py
exit /b 0
