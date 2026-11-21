@echo off
REM Disable Private DNS (use system default)
adb shell settings put global private_dns_mode off
echo Private DNS disabled
