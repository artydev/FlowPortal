@echo off
rem start_agent.bat - lance CSAgent (mode API headless) PUIS CSFlow, depuis ce dossier.
rem CSAgent n'ecrit que dans son dossier courant : il doit tourner ici pour que les
rem chemins relatifs des workflows (tmp\...) pointent au meme endroit que CSFlow.
rem Fermer CSFlow arrete aussi CSAgent.

cd /d "%~dp0"

rem ---- a adapter ----------------------------------------------------------
set CSAGENT_BIN=csagent
set PORT=8080
set API_KEY=demo-secret
rem MODEL : nom du modele (vide = routage automatique), ex : set MODEL=mon-modele-rapide
if not defined MODEL set MODEL=
rem -------------------------------------------------------------------------

if not "%ALBERT_API_KEY%"=="" goto key_ok
echo.
echo  ERREUR : ALBERT_API_KEY n'est pas definie.
echo  Lance :  set ALBERT_API_KEY=ta-cle
pause
exit /b 1
:key_ok

where %CSAGENT_BIN% >nul 2>&1
if not errorlevel 1 goto agent_ok
echo.
echo  ERREUR : %CSAGENT_BIN% est introuvable dans le PATH.
echo  Adapte CSAGENT_BIN en haut de ce fichier.
pause
exit /b 1
:agent_ok

rem NB : "python.exe" avec extension : "start python" peut ouvrir un DOSSIER nomme python du PATH.
rem Application CSFlow : exe a cote de ce fichier, sinon build\FlowPortal.exe, sinon app.py
rem WORKDIR = dossier de travail de CSFlow (exe -> son dossier ; app.py -> son dossier).
rem Le worker CSAgent DOIT tourner dans ce meme dossier, sinon tmp\... n'est pas partage.
set "CSFLOW_CMD="
set "WORKDIR=%~dp0"
if exist "%~dp0FlowPortal.exe" set CSFLOW_CMD="%~dp0FlowPortal.exe"
if defined CSFLOW_CMD goto flow_ok
if exist "%~dp0build\FlowPortal.exe" set CSFLOW_CMD="%~dp0build\FlowPortal.exe"
if exist "%~dp0build\FlowPortal.exe" set "WORKDIR=%~dp0build\"
if defined CSFLOW_CMD goto flow_ok
if exist "%~dp0app.py" set CSFLOW_CMD=python.exe "%~dp0app.py"
if defined CSFLOW_CMD goto flow_ok
echo.
echo  ERREUR : FlowPortal.exe ou app.py introuvable a cote de ce fichier.
pause
exit /b 1
:flow_ok
cd /d "%WORKDIR%"

echo.
echo   CSAgent -- mode API headless
echo   Dossier : %CD%
echo   Ecoute sur http://localhost:%PORT%
echo.

rem 0. tuer un ancien worker reste sur le port (mauvais dossier)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do taskkill /pid %%p /t /f >nul 2>&1

rem 1. CSAgent dans sa propre fenetre (reduite), meme dossier de travail
set "MODEL_ARG="
if defined MODEL set MODEL_ARG=--model %MODEL%
start "CSAgent-API" /min cmd /c %CSAGENT_BIN% --api --yes --port %PORT% --api-key %API_KEY% %MODEL_ARG% --mem csflow_worker --no-distill

rem 2. laisser a CSAgent le temps d'ouvrir son port
timeout /t 3 /nobreak >nul

rem 3. CSFlow : on attend sa fermeture
echo   Lancement de CSFlow...
start "FlowPortal" /wait %CSFLOW_CMD%

rem 4. CSFlow est ferme : on arrete CSAgent (par titre de fenetre, puis par port)
echo   CSFlow ferme -- arret de CSAgent.
taskkill /fi "WINDOWTITLE eq CSAgent-API*" /t /f >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do taskkill /pid %%p /t /f >nul 2>&1
exit /b 0