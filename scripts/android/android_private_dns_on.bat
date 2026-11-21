@echo off
REM Enable Private DNS pointing to hostname
adb shell settings put global private_dns_mode hostname
adb shell settings put global private_dns_specifier instagram-blocker.local
echo Private DNS enabled: instagram-blocker.local
