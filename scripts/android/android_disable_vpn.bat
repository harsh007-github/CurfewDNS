@echo off
REM Stop RethinkDNS VPN service (run at 23:30)
adb shell am stopservice -n com.celzero.rethink/.core.VpnService
echo RethinkDNS VPN stopped at %TIME%
