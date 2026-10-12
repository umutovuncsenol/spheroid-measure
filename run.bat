@echo off
rem Start the Spheroid Review app. Creates a project-local virtual environment on first run.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment in .venv ...
  py -3 -m venv .venv || python -m venv .venv
)
rem .venv\.installed is a copy of the requirements that were installed; reinstall when they change
fc /b requirements.txt .venv\.installed >nul 2>&1 || (
  echo Installing dependencies into .venv ...
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt && copy /y requirements.txt .venv\.installed >nul
)
.venv\Scripts\python.exe -m app %*
