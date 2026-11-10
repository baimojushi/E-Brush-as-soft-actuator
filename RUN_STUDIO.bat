@echo off
setlocal
rem Studio consumes the HP60C v13 shared memory. If the pipeline is not running,
rem HP60CSource starts it in the background (disable with --no-camera-supervisor).
cd /d "%~dp0studio"
py -3 -u main.py --camera-backend shm --port COM7 %*
