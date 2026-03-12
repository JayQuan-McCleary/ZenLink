@echo off
REM Syncs root source files into extension/ for AMO publishing.
REM Run this before building the .xpi or uploading to AMO.

echo Syncing root -^> extension/...

copy /Y manifest.json extension\manifest.json
copy /Y background\background.js extension\background\background.js
copy /Y content\content.js extension\content\content.js
copy /Y native\bridge.py extension\native\bridge.py
copy /Y native\zen-bridge.ps1 extension\native\zen-bridge.ps1
copy /Y icons\icon-48.png extension\icons\icon-48.png
copy /Y icons\icon-96.png extension\icons\icon-96.png

echo.
echo Done. extension/ is now in sync with root.
echo Remember to rebuild the .xpi if publishing to AMO.
