@echo off
rem Spheroid Measure - Windows installer.
rem ASCII only, CRLF line endings (see .gitattributes): cmd.exe misreads batch files with LF
rem endings or UTF-8 characters, which made this script fail with "'_DIR' is not recognized".
setlocal enabledelayedexpansion

set "APP_DIR=%~dp0"
set "APP_DIR=%APP_DIR:~0,-1%"
set "CONDA_BASE=%USERPROFILE%\miniforge3"
set "MINIFORGE_URL=https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe"

echo ======================================
echo   Sferoid Measure - Kurulum
echo ======================================
echo.

rem 1. Miniforge
if not exist "%CONDA_BASE%\Scripts\conda.exe" (
    echo ^>^> Miniforge bulunamadi, indiriliyor...
    powershell -NoProfile -Command "Invoke-WebRequest -Uri '%MINIFORGE_URL%' -OutFile '%TEMP%\miniforge_install.exe'"
    if not exist "%TEMP%\miniforge_install.exe" goto :dl_fail
    "%TEMP%\miniforge_install.exe" /InstallationType=JustMe /RegisterPython=0 /S /D=%CONDA_BASE%
    del "%TEMP%\miniforge_install.exe"
    if not exist "%CONDA_BASE%\Scripts\conda.exe" goto :inst_fail
    echo ^>^> Miniforge kuruldu.
) else (
    echo ^>^> Miniforge zaten kurulu.
)

call "%CONDA_BASE%\Scripts\activate.bat" "%CONDA_BASE%"
if errorlevel 1 goto :act_fail

rem 2. Conda environment
call conda env list | findstr /B "sferoid " >nul 2>&1
if !errorlevel! == 0 (
    echo ^>^> 'sferoid' ortami guncelleniyor...
    call conda env update -n sferoid -f "%APP_DIR%\environment.yml" --prune
) else (
    echo ^>^> 'sferoid' ortami olusturuluyor, 5-10 dakika surebilir...
    call conda env create -f "%APP_DIR%\environment.yml"
)
if errorlevel 1 goto :env_fail
echo ^>^> Bagimliliklar kuruldu.

rem 3. Desktop shortcut (calls the launcher, which also installs updates)
set "LAUNCHER=%USERPROFILE%\Desktop\Sferoid Measure.bat"
(
    echo @echo off
    echo call "%APP_DIR%\launcher\launch.bat"
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
    call "%APP_DIR%\launcher\launch.bat"
)
goto :end

:dl_fail
echo.
echo HATA: Miniforge indirilemedi. Internet, VPN ya da antivirus engelliyor olabilir.
echo Miniforge'u elle kurup bu dosyayi tekrar calistirin:
echo %MINIFORGE_URL%
goto :end

:inst_fail
echo.
echo HATA: Miniforge kurulumu tamamlanamadi.
goto :end

:act_fail
echo.
echo HATA: conda etkinlestirilemedi: %CONDA_BASE%
goto :end

:env_fail
echo.
echo HATA: 'sferoid' ortami kurulamadi. Yukaridaki hata mesajina bakin.
goto :end

:end
echo.
pause
endlocal
