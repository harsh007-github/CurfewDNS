@echo off
REM Prepare RethinkDNS VPN (run at 06:00)
adb shell cmd connectivity vpn prepare com.celzero.rethink
echo RethinkDNS VPN prepared at %TIME%
