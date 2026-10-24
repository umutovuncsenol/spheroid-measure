@echo off
chcp 65001 >nul
set "APP_DIR=%~dp0.."

set "CONDA_BASE=%USERPROFILE%\miniforge3"
if not exist "%CONDA_BASE%\Scripts\conda.exe" set "CONDA_BASE=%USERPROFILE%\miniconda3"
if not exist "%CONDA_BASE%\Scripts\conda.exe" set "CONDA_BASE=C:\ProgramData\miniforge3"

call "%CONDA_BASE%\Scripts\activate.bat"

:: Otomatik güncelleme
cd /d "%APP_DIR%"
git rev-parse --git-dir >nul 2>&1
if not errorlevel 1 (
  echo Guncellemeler kontrol ediliyor...
  git fetch origin main --quiet 2>nul
  for /f %%i in ('git rev-parse HEAD') do set LOCAL=%%i
  for /f %%i in ('git rev-parse origin/main 2^>nul') do set REMOTE=%%i
  if not "%LOCAL%"=="%REMOTE%" (
    echo Guncelleme bulundu, indiriliyor...
    git pull origin main --quiet
    git diff HEAD@{1} HEAD -- environment.yml | findstr /r "." >nul 2>&1
    if not errorlevel 1 (
      echo Yeni bagimliliklar kuruluyor...
      call conda env update -n sferoid -f "%APP_DIR%\environment.yml" --prune
    )
    echo Guncelleme tamamlandi.
  ) else (
    echo Uygulama guncel.
  )
)

call conda activate sferoid
python -m app
