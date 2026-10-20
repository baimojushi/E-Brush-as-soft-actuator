"""
HP60C Supervisor Windows Service (无弹窗版)
"""
import win32serviceutil
import win32service
import win32event
import servicemanager
import subprocess
import os
import sys
import time
import signal

SERVICE_NAME = "HP60C_Supervisor"
SERVICE_DISPLAY_NAME = "HP60C Camera Supervisor (TouchDesigner Bridge)"
SERVICE_DESCRIPTION = "守护 HP60C worker 进程，处理相机热插拔自动恢复"

SUPERVISOR_PATH = r"D:\项目\交互艺术\书法微更新\hp60c_v13\hp60c_supervisor_v13.py"
LEGACY_DIR = r"D:\项目\交互艺术\书法微更新\hp60c_v13"
LEGACY_MODULE = "hp60c_td_bridge_v7"
LOG_DIR = r"D:\项目\交互艺术\书法微更新\hp60c_v13\logs"
PYTHON_EXE = r"C:\Program Files\Python312\python.exe"


class HP60CSupervisorService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.worker_proc = None
        self.running = False

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.worker_proc and self.worker_proc.poll() is None:
            self.worker_proc.terminate()
            try:
                self.worker_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.worker_proc.kill()
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (SERVICE_NAME, "")
        )
        self.main()

    def main(self):
        cmd = [
            PYTHON_EXE, SUPERVISOR_PATH,
            "--mode", "legacy",
            "--legacy-dir", LEGACY_DIR,
            "--legacy-module", LEGACY_MODULE,
            "--log-dir", LOG_DIR,
        ]

        self.worker_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.running = True

        while self.running:
            rc = self.worker_proc.poll()
            if rc is not None:
                servicemanager.LogMsg(
                    servicemanager.EVENTLOG_WARNING_TYPE,
                    0x0001,
                    (f"Worker exited with code {rc}, restarting...", "")
                )
                time.sleep(2)
                _ = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                result = win32event.WaitForSingleObject(self.hWaitStop, 1000)
                if result == win32event.WAIT_OBJECT_0:
                    self.running = False


if __name__ == '__main__':
    win32serviceutil.HandleCommandLine(HP60CSupervisorService)