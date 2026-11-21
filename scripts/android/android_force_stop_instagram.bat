@echo off
REM Force-stop Instagram app (run at 06:00)
adb shell am force-stop com.instagram.android
adb shell am kill-all
echo Instagram processes killed at %TIME%
