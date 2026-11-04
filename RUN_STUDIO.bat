@echo off
setlocal
rem Studio consumes the HP60C v13 shared memory. If the pipeline is not running,
rem HP60CSource starts it in the background (disable with --no-camera-supervisor).
rem Do NOT pin --port here: the S3 bridge is found by VID/PID (CH343/CH340/CP210x)
rem and re-detected after every unplug. Override only when you must:
rem   RUN_STUDIO.bat --port COM7
cd /d "%~dp0studio"
py -3 -u main.py --camera-backend shm %*
