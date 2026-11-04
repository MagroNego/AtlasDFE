@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo AtlasDFE - Web
echo ============================================
echo.

where py >nul 2>&1
if errorlevel 1 (
  echo ERRO: Python Launcher nao encontrado.
  echo Instale Python 3.11 ou superior e execute este arquivo novamente.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente virtual...
  py -m venv .venv
  if errorlevel 1 goto :erro
)

echo Atualizando pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :erro

echo Instalando aplicacao...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :erro

if not exist "dados" mkdir "dados"
if not exist "secrets" mkdir "secrets"
if not exist "logs" mkdir "logs"

echo.
".venv\Scripts\python.exe" -c "import nfe_consulta; import nfe_consulta.web.app; print('Versao:', nfe_consulta.__version__)"
if errorlevel 1 goto :erro

echo.
echo Instalacao concluida.
echo.
echo Proximos passos:
echo   1. O acesso padrao dispensa login, inclusive no Admin
echo   2. Execute INICIAR_WEB.cmd e acesse http://127.0.0.1:8080
echo   3. Em Admin, selecione o banco existente e configure o certificado A1
echo   4. Para SQLCipher, use secrets\db-password.txt
echo   Consulte MANUAL_DE_INSTALACAO.md e DISTRIBUICAO_DFE_QIVE.md
echo.
pause
exit /b 0

:erro
echo.
echo ERRO: a instalacao nao foi concluida.
pause
exit /b 1
