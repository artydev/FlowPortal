@echo off
rem start_csagent.bat - mode "CSAgent lance CSFlow" (MCP).
rem  1. un CSAgent worker (--api, port 8080) dans une fenetre reduite : etapes agent des workflows ;
rem  2. le CSAgent interactif ici, avec CSFlow declare comme serveur MCP (mcp.json), SANS --yes.
rem Fermer le CSAgent interactif arrete le worker.

cd /d "%~dp0"
set CSAGENT_BIN=csagent
set PORT=8080
set API_KEY=demo-secret
rem MODEL : nom du modele (vide = routage automatique), ex : set MODEL=mon-modele-rapide
if not defined MODEL set MODEL=

if not "%ALBERT_API_KEY%"=="" goto key_ok
echo.
echo  ERREUR : ALBERT_API_KEY n'est pas definie.
pause
exit /b 1
:key_ok

where %CSAGENT_BIN% >nul 2>&1
if not errorlevel 1 goto agent_ok
echo.
echo  ERREUR : %CSAGENT_BIN% est introuvable dans le PATH.
pause
exit /b 1
:agent_ok

rem WORKDIR = dossier de travail de CSFlow (celui de son exe, sinon de app.py).
rem Le worker doit tourner dans ce dossier pour partager tmp\... avec CSFlow.
set "WORKDIR=%~dp0"
if exist "%~dp0CSFlow.exe" goto wd_ok
if exist "%~dp0build\CSFlow.exe" set "WORKDIR=%~dp0build\"
:wd_ok

if exist mcp.json goto mcp_ok
if exist CSFlow.exe copy mcp.windows.exe.example mcp.json >nul
if exist mcp.json goto mcp_made
if exist app.py copy mcp.windows.source.example mcp.json >nul
if exist mcp.json goto mcp_made
echo  ERREUR : mcp.json introuvable (et ni CSFlow.exe ni app.py a cote de ce fichier).
pause
exit /b 1
:mcp_made
echo   mcp.json cree (verifiez la commande si besoin).
:mcp_ok

rem tuer un ancien worker reste sur le port (il aurait le mauvais dossier)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do taskkill /pid %%p /t /f >nul 2>&1

echo   Worker CSAgent dans : %WORKDIR%
set "MODEL_ARG="
if defined MODEL set MODEL_ARG=--model %MODEL%
start "CSAgent-Worker" /min /d "%WORKDIR%" cmd /c %CSAGENT_BIN% --api --yes --port %PORT% --api-key %API_KEY% %MODEL_ARG% --mem csflow_worker --no-distill
timeout /t 3 /nobreak >nul

echo   CSAgent interactif avec le serveur MCP CSFlow...
%CSAGENT_BIN% --mcp-config mcp.json

echo   CSAgent ferme -- arret du worker.
taskkill /fi "WINDOWTITLE eq CSAgent-Worker*" /t /f >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do taskkill /pid %%p /t /f >nul 2>&1
exit /b 0