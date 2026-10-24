@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "APP_DIR=%~dp0"
set "APP_DIR=%APP_DIR:~0,-1%"
set "CONDA_BASE=%USERPROFILE%\miniforge3"
set "MINIFORGE_URL=https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe"

echo ======================================
echo   Sferoid Measure — Kurulum
echo ======================================
echo.

:: 1. Miniforge kontrolü
if not exist "%CONDA_BASE%\Scripts\conda.exe" (
  echo ^>^> Miniforge bulunamadi, indiriliyor...
  powershell -Command "Invoke-WebRequest -Uri '%MINIFORGE_URL%' -OutFile '%TEMP%\miniforge_install.exe'"
  "%TEMP%\miniforge_install.exe" /InstallationType=JustMe /RegisterPython=0 /S /D=%CONDA_BASE%
  del "%TEMP%\miniforge_install.exe"
  echo ^>^> Miniforge kuruldu.
) else (
  echo ^>^> Miniforge zaten kurulu.
)

call "%CONDA_BASE%\Scripts\activate.bat"

:: 2. Conda ortamı oluşturma
conda env list | findstr /B "sferoid " >nul 2>&1
if !errorlevel! == 0 (
  echo ^>^> 'sferoid' ortami guncelleniyor...
  call conda env update -n sferoid -f "%APP_DIR%\environment.yml" --prune
) else (
  echo ^>^> 'sferoid' ortami olusturuluyor (5-10 dakika surebilir^)...
  call conda env create -f "%APP_DIR%\environment.yml"
)

echo ^>^> Bagimliliklar kuruldu.

:: 3. Masaüstü kısayol oluşturma
set "LAUNCHER=%USERPROFILE%\Desktop\Sferoid Measure.bat"
(
  echo @echo off
  echo call "%CONDA_BASE%\Scripts\activate.bat" sferoid
  echo cd /d "%APP_DIR%"
  echo python -m app
) > "%LAUNCHER%"

echo.
echo ======================================
echo   Kurulum tamamlandi!
echo.
echo   Uygulamayi acmak icin masaustundeki
echo   'Sferoid Measure' dosyasina
echo   cift tiklayin.
echo ======================================
echo.
set /p ANSWER="Simdi uygulamayi baslatmak ister misiniz? (e/h): "
if /i "%ANSWER%"=="e" (
  call conda activate sferoid
  cd /d "%APP_DIR%"
  python -m app
)

pause
