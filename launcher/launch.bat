@echo off
rem Start the Spheroid Review app on Windows. ASCII only, CRLF line endings (see .gitattributes):
rem cmd.exe misreads batch files with LF endings or UTF-8 characters.
setlocal
set "APP_DIR=%~dp0.."

rem Use the conda installation that has the 'sferoid' environment.
set "CONDA_BASE="
for %%B in ("%USERPROFILE%\miniforge3" "%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "C:\ProgramData\miniforge3") do (
  if not defined CONDA_BASE if exist "%%~B\envs\sferoid\python.exe" set "CONDA_BASE=%%~B"
)
if not defined CONDA_BASE (
  echo Conda environment 'sferoid' not found. Run install.bat first.
  pause
  exit /b 1
)

call "%CONDA_BASE%\Scripts\activate.bat" "%CONDA_BASE%"
if errorlevel 1 (
  echo Could not activate conda at %CONDA_BASE%.
  pause
  exit /b 1
)

rem Automatic update (skipped silently when git is missing or the pull fails)
cd /d "%APP_DIR%"
where git >nul 2>&1
if not errorlevel 1 (
  git rev-parse --git-dir >nul 2>&1
  if not errorlevel 1 call :update
)

call conda activate sferoid
python -m app %*
if errorlevel 1 (
  echo.
  echo The app stopped with an error. Take a screenshot of the messages above.
  pause
)
exit /b

:update
echo Checking for updates...
git fetch origin main --quiet 2>nul
set "LOCAL="
set "REMOTE="
for /f %%i in ('git rev-parse HEAD 2^>nul') do set "LOCAL=%%i"
for /f %%i in ('git rev-parse origin/main 2^>nul') do set "REMOTE=%%i"
if "%LOCAL%"=="%REMOTE%" (
  echo App is up to date.
  exit /b 0
)
echo Update found, downloading...
git pull origin main --quiet
if errorlevel 1 (
  echo Update failed; continuing with the current version.
  exit /b 0
)
git diff HEAD@{1} HEAD -- environment.yml | findstr /r "." >nul 2>&1
if not errorlevel 1 (
  echo Installing new dependencies...
  call conda env update -n sferoid -f "%APP_DIR%\environment.yml" --prune
)
echo Update finished.
exit /b 0
