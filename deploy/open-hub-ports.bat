@echo off
:: Right-click -> Run as administrator  (or just double-click; it will UAC)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%~dp0windows-open-hub-ports.ps1\"'"
echo Hub ports 8080/7443/7500-7502 should now be reachable at this PC's Wi-Fi IP.
pause
