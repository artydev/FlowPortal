@echo off
rem ------------------------------------------------------------
rem Compilation de CSFlow avec Nuitka (un seul exe)
rem A lancer depuis le dossier workflow\ (celui qui contient app.py)
rem Les scripts des noeuds `python` sont executes par l'exe lui-meme :
rem seuls les modules de la bibliotheque standard listes ici (--include-module)
rem sont disponibles pour eux. Ajoutez les votres au besoin.
rem ------------------------------------------------------------

python -m nuitka ^
    --onefile ^
    --windows-console-mode=disable ^
    --remove-output ^
    --output-dir=build ^
    --output-filename=FlowPortal.exe ^
    --include-data-dir=ui=ui ^
    --include-data-dir=workflows=workflows ^
    --include-data-files=csflow.schema.json=csflow.schema.json ^
    --include-data-files=CSFLOW_LLM_GUIDE.md=CSFLOW_LLM_GUIDE.md ^
    --include-data-files=REFERENCE.md=REFERENCE.md ^
    --include-package=eel ^
    --include-package-data=eel ^
    --include-package=yaml ^
    --include-package=nodes ^
    --include-module=mcp_server ^
    --include-module=ui_launcher ^
    --include-module=audio_client ^
    --include-module=mcp_client ^
    --include-module=portal ^
    --include-module=assist ^
    --include-module=runpy ^
    --include-package=unittest ^
    --include-module=csv ^
    --include-module=json ^
    --include-module=datetime ^
    --include-module=re ^
    --include-module=math ^
    --include-module=statistics ^
    --include-module=collections ^
    --include-module=itertools ^
    --include-module=decimal ^
    --include-module=fractions ^
    --include-module=random ^
    --include-module=textwrap ^
    --include-module=argparse ^
    --include-module=pathlib ^
    --include-module=sqlite3 ^
    --include-module=hashlib ^
    --include-module=zipfile ^
    --include-module=string ^
    app.py

if %errorlevel% neq 0 (
    echo Erreur lors de la compilation avec Nuitka.
    exit /b %errorlevel%
)
copy /y start_agent.bat build\start_agent.bat >nul
copy /y start_csagent.bat build\start_csagent.bat >nul
copy /y mcp.windows.exe.example build\mcp.windows.exe.example >nul
xcopy /e /i /y skills build\skills >nul
echo Compilation reussie : build\FlowPortal.exe
echo Livrez build\FlowPortal.exe ET build\start_agent.bat dans le meme dossier : lancez start_agent.bat (CSAgent puis CSFlow).
echo Mode MCP : lancez build\start_csagent.bat (CSAgent lance FlowPortal --mcp) ; le dossier build\skills est lu par CSAgent.
echo Au premier lancement, CSFlow cree a cote de l exe les dossiers workflows et tmp, le schema, CSFLOW_LLM_GUIDE.md et REFERENCE.md.