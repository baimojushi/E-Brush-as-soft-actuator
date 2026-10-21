from pathlib import Path
import os
import subprocess
import sys
import threading
import time
import serial


class HP60CSource:
    """HP60C RGB-D source with a background reconnect loop.

    Backends:
      shm    - consume HP60C v13 shared memory (supervisor/worker owns the camera)
      direct - own the vendor SDK inside this process
      none   - deliberately disabled; never retried

    A supervisor restart replaces the named mappings, so a stale-but-attached
    Reader is dropped and re-created instead of trusting its frozen handles.
    """

    RECONNECT_TICK_S = 0.5
    RECONNECT_MIN_S = 1.0
    RECONNECT_MAX_S = 10.0
    STALE_RECONNECT_S = 3.0
    POLL_ERROR_LIMIT = 5
    DIRECT_MAX_AGE_S = 1.0
    SUPERVISOR_GRACE_S = 10.0
    SUPERVISOR_RETRY_MIN_S = 5.0
    SUPERVISOR_RETRY_MAX_S = 60.0
    DEFAULT_SDK_DIR = (r"D:\Express Program Files\Angstrong Viewer V2.1.10_240109"
                       r"\Angstrong Viewer V2.1.10_240109\RelWithDebInfo")

    def __init__(self, root, backend="shm", auto_reconnect=True,
                 auto_start_supervisor=True):
        self.root = Path(root)
        self.backend = backend
        self.auto_reconnect = bool(auto_reconnect) and backend != "none"
        self.auto_start_supervisor = (bool(auto_start_supervisor)
                                      and backend == "shm")
        self.impl = None
        self.error = ""
        self.reconnect_attempts = 0
        self.frames_delivered = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._open_impl = None
        self._impl_since = 0.0
        self._last_frame_mono = None
        self._last_token = None
        self._poll_errors = 0
        self._backoff = self.RECONNECT_MIN_S
        self._next_attempt = 0.0
        self._supervisor = None
        self._supervisor_starts = 0
        self._supervisor_error = ""
        self._supervisor_retry = self.SUPERVISOR_RETRY_MIN_S
        self._supervisor_next_start = 0.0

    def _make_impl(self):
        if self._open_impl is not None:
            return self._open_impl()
        if self.backend == "direct":
            host = self.root / "host"
            if str(host) not in sys.path:
                sys.path.insert(0, str(host))
            from hp60c_direct import HP60CDirectSDK
            impl = HP60CDirectSDK(Path(os.environ.get("HP60C_SDK_DIR")
                                       or self.DEFAULT_SDK_DIR))
            impl.start()
            return impl
        if self.backend != "shm":
            raise ValueError(f"unknown camera backend: {self.backend}")
        d = self.root.parent / "hp60c_v13"
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
        import hp60c_shm_reader_v13 as reader
        impl = reader.Reader()
        impl.try_attach()
        return impl

    def _connect(self):
        if self.backend == "none" or self._stop.is_set():
            return False
        with self._lock:
            if self.impl is not None:
                return True
        self.reconnect_attempts += 1
        try:
            impl = self._make_impl()
        except Exception as exc:
            with self._lock:
                self.error = f"{type(exc).__name__}: {exc}"
                self._backoff = min(self._backoff * 1.5, self.RECONNECT_MAX_S)
                self._next_attempt = time.monotonic() + self._backoff
            print(f"[CAMERA] Connect failed: {self.error}")
            return False
        with self._lock:
            if self.impl is not None:
                try:
                    impl.close()
                except Exception:
                    pass
                return True
            self.impl = impl
            self._impl_since = time.monotonic()
            self._last_frame_mono = None
            self._last_token = None
            self._poll_errors = 0
            self._backoff = self.RECONNECT_MIN_S
            self._next_attempt = 0.0
            self.error = ""
        print(f"[CAMERA] {self.backend} source connected")
        return True

    def _drop(self, reason):
        with self._lock:
            impl, self.impl = self.impl, None
            self.error = reason
            self._last_token = None
            self._last_frame_mono = None
            self._poll_errors = 0
            self._next_attempt = 0.0
        if impl is not None:
            try:
                impl.close()
            except Exception:
                pass
        if reason:
            print(f"[CAMERA] dropped: {reason}")

    def _start_reconnect_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._reconnect_loop, name="HP60CReconnect", daemon=True)
        self._thread.start()

    @staticmethod
    def _token(frame):
        if not frame:
            return None
        return (frame.get("packet_id"), frame.get("host_ns"),
                frame.get("generation"))

    def _impl_dead(self):
        with self._lock:
            impl = self.impl
            errors = self._poll_errors
            last = self._last_frame_mono
            since = self._impl_since
        if impl is None:
            return False
        if errors >= self.POLL_ERROR_LIMIT:
            return True
        age = time.monotonic() - (last if last is not None else since)
        if age <= self.STALE_RECONNECT_S:
            return False
        if self.backend == "shm":
            # No supervisor means the mapping is not attachable; Reader retries
            # by itself, so do not churn a fresh Reader every few seconds.
            return getattr(impl, "status_map", None) is not None
        return True

    @staticmethod
    def _supervisor_process_running():
        """True if any Python process already runs the v13 supervisor."""
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'",
                 "get", "CommandLine", "/value"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return "hp60c_supervisor_v13" in (out.stdout or "").lower()
        except Exception:
            return False

    def _spawn_supervisor(self):
        """Start the camera pipeline that feeds the shared memory we consume."""
        shm_dir = self.root.parent / "hp60c_v13"
        sup_py = shm_dir / "hp60c_supervisor_v13.py"
        if not sup_py.is_file():
            self._supervisor_error = f"缺少 {sup_py}"
            self._supervisor_next_start = (time.monotonic()
                                           + self.SUPERVISOR_RETRY_MAX_S)
            return
        python = os.environ.get("HP60C_PYTHON") or (
            r"C:\Program Files\Python312\python.exe"
            if Path(r"C:\Program Files\Python312\python.exe").is_file()
            else sys.executable)
        mode = os.environ.get("HP60C_SUPERVISOR_MODE", "legacy")
        cmd = [python, "-u", str(sup_py), "--python", python,
               "--mode", mode, "--startup-timeout-s", "120"]
        if mode == "legacy":
            cmd += ["--legacy-dir", str(self.root.parent / "hp60c_ndi_sender"),
                    "--legacy-module", "hp60c_td_bridge_v7"]
        try:
            self._supervisor = subprocess.Popen(
                cmd, cwd=str(shm_dir),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            self._supervisor_error = f"{type(exc).__name__}: {exc}"
            self._supervisor_next_start = (time.monotonic()
                                           + self._supervisor_retry)
            return
        self._supervisor_starts += 1
        self._supervisor_error = ""
        self._supervisor_retry = self.SUPERVISOR_RETRY_MIN_S
        self._supervisor_next_start = (time.monotonic()
                                       + self.SUPERVISOR_RETRY_MIN_S)
        print(f"[CAMERA] started HP60C supervisor pid={self._supervisor.pid}")

    def _ensure_supervisor(self):
        now = time.monotonic()
        proc = self._supervisor
        if proc is not None and proc.poll() is None:
            return
        if proc is not None:
            self._supervisor = None
            self._supervisor_error = (f"supervisor exited with code "
                                      f"{proc.returncode}")
            self._supervisor_next_start = now + self._supervisor_retry
            self._supervisor_retry = min(self._supervisor_retry * 2,
                                         self.SUPERVISOR_RETRY_MAX_S)
            print(f"[CAMERA] {self._supervisor_error}; retrying later")
            return
        with self._lock:
            impl = self.impl
        if impl is not None and getattr(impl, "status_map", None) is not None:
            return  # an existing supervisor already feeds this shared memory
        if now < self._supervisor_next_start:
            return
        if self._supervisor_process_running():
            self._supervisor_next_start = now + self._supervisor_retry
            return
        self._spawn_supervisor()

    def stop_supervisor(self):
        """Kill the pipeline this source started (the worker is its child)."""
        proc, self._supervisor = self._supervisor, None
        if proc is None:
            return
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def _reconnect_loop(self):
        while not self._stop.wait(self.RECONNECT_TICK_S):
            if self._stop.is_set():
                break
            if not self.auto_reconnect:
                continue
            if self._impl_dead():
                self._drop(
                    f"no frames for > {self.STALE_RECONNECT_S:.0f}s")
            with self._lock:
                impl = self.impl
                wait = self._next_attempt - time.monotonic()
            if impl is None and wait <= 0:
                self._connect()
            if self.auto_start_supervisor:
                self._ensure_supervisor()

    def start(self):
        self._stop.clear()
        if self.backend == "none":
            self.impl = None
            self.error = "按请求禁用 HP60C（仅用于姿态/Hall 标定）"
            return
        self._connect()
        if self.auto_reconnect:
            self._supervisor_next_start = (time.monotonic()
                                           + self.SUPERVISOR_GRACE_S)
            self._start_reconnect_thread()

    def reconnect_now(self):
        self._drop("manual reconnect")
        self._connect()
        return self.status()

    def poll(self):
        with self._lock:
            impl = self.impl
        if impl is None:
            return None
        try:
            frame = (impl.poll() if self.backend == "direct"
                     else impl.try_get_latest(max_age_ms=500))
        except Exception as exc:
            with self._lock:
                self.error = f"{type(exc).__name__}: {exc}"
                self._poll_errors += 1
            return None
        if not frame:
            return None
        token = self._token(frame)
        with self._lock:
            if token != self._last_token:
                self._last_token = token
                self._last_frame_mono = time.monotonic()
                self.frames_delivered += 1
            self._poll_errors = 0
            self.error = ""
        if self.backend == "direct":
            host_ns = frame.get("host_ns")
            if host_ns and (time.perf_counter_ns() - int(host_ns)) / 1e9 > self.DIRECT_MAX_AGE_S:
                return None
        return frame

    def status(self):
        with self._lock:
            impl = self.impl
            error = self.error
            last = self._last_frame_mono
            next_attempt = self._next_attempt
        if impl is None:
            st = {"state": "DISABLED" if self.backend == "none" else "OFFLINE",
                  "attached": False, "reason": error}
        else:
            try:
                st = dict(impl.status() if self.backend == "direct"
                          else impl.get_status())
            except Exception as exc:
                st = {"state": "ERROR", "attached": False,
                      "reason": f"{type(exc).__name__}: {exc}"}
        age_s = None if last is None else max(0.0, time.monotonic() - last)
        retry_in = (max(0.0, next_attempt - time.monotonic())
                    if impl is None and self.auto_reconnect else None)
        with self._lock:
            sup = self._supervisor
        sup_pid = (sup.pid if sup is not None and sup.poll() is None else None)
        st.update({
            "backend": self.backend,
            "auto_reconnect": self.auto_reconnect,
            "reconnect_attempts": self.reconnect_attempts,
            "retry_in_s": round(retry_in, 1) if retry_in is not None else None,
            "frames_delivered": self.frames_delivered,
            "last_frame_age_s": round(age_s, 3) if age_s is not None else None,
            "supervisor_autostart": self.auto_start_supervisor,
            "supervisor_pid": sup_pid,
            "supervisor_starts": self._supervisor_starts,
            "supervisor_error": self._supervisor_error or None,
        })
        return st

    def close(self):
        self._stop.set()
        thr = self._thread
        if thr is not None and thr.is_alive() and thr is not threading.current_thread():
            thr.join(timeout=2.0)
        self._thread = None
        with self._lock:
            impl, self.impl = self.impl, None
        if impl is not None:
            try:
                impl.close()
            except Exception:
                pass


def auto_detect_port(verbose=False):
    """Probe BRUSH-V1 responders; never assume all S3 adapters use CH340 VID/PID."""
    from brush_serial import auto_detect_port as detect_brush_port
    return detect_brush_port(verbose=verbose)


class BrushSource:
    """Studio's ONLY serial receiver: COBS/CRC32 BRUSH-V1, not line JSON.

    Exposes the same latest()/status()/close() interface used by main.py.
    Extra S3 0x20/0x21 radio diagnostics are handled by the Wireless adapter.
    """

    def __init__(self, root_dir, port=None, baud=921600, auto_reconnect=True):
        self.root_dir = Path(root_dir)
        self.port = port
        self._preferred_port = port
        self.baud = baud
        self.auto_reconnect = bool(auto_reconnect)
        self.bs = None
        self.error = ""
        self._lock = threading.RLock()
        self._reconnect_thread = None
        self._stop_reconnect = threading.Event()
        self._ever_connected = False
        self._reconnect_backoff = 1.0

    def _prepare_imports(self):
        path = str(self.root_dir / 'studio')
        if path not in sys.path:
            sys.path.insert(0, path)
        from wireless_studio_adapter import WirelessBrushSerial
        return WirelessBrushSerial

    def _connect_brush(self):
        decoder = self._prepare_imports()
        if not self.port:
            self.port = auto_detect_port(verbose=True)
        if not self.port:
            raise RuntimeError('未检测到支持 BRUSH-V1 的 ESP32-S3 串口')
        if self._stop_reconnect.is_set():
            raise RuntimeError('串口连接已取消')

        receiver = decoder(self.port, self.baud, queue_size=0)
        try:
            receiver.start()
        except Exception:
            receiver.close()
            raise
        with self._lock:
            old = self.bs
            self.bs = receiver
            self.error = ''
            self._ever_connected = True
            self._reconnect_backoff = 1.0
        if old is not None:
            old.close()
        print(f'[BRUSH] BRUSH-V1 binary parser active: {self.port} @ {self.baud}')

    def start(self):
        self._stop_reconnect.clear()
        try:
            self._connect_brush()
        except Exception as exc:
            with self._lock:
                self.error = f'{type(exc).__name__}: {exc}'
            print(f'[BRUSH] Connect failed: {self.error}')
        if self.auto_reconnect:
            self._start_reconnect_thread()

    def _start_reconnect_thread(self):
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._stop_reconnect.clear()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, name='BrushSourceReconnect', daemon=True)
        self._reconnect_thread.start()

    def _reconnect_loop(self):
        while not self._stop_reconnect.wait(2.0):
            if not self.auto_reconnect:
                continue
            with self._lock:
                receiver = self.bs
            if receiver is not None and receiver.ser.is_open and receiver._running:
                continue
            try:
                if receiver is not None:
                    receiver.close()
                    with self._lock:
                        if self.bs is receiver:
                            self.bs = None
                # Auto detected ports may change after a USB unplug/replug.
                if self._preferred_port is None:
                    self.port = None
                self._connect_brush()
            except Exception as exc:
                with self._lock:
                    self.error = f'{type(exc).__name__}: {exc}'
                    self._reconnect_backoff = min(self._reconnect_backoff * 1.5, 10.0)
                # wait interruptibly so Close/Stop remains prompt
                self._stop_reconnect.wait(self._reconnect_backoff)

    def latest(self):
        with self._lock:
            receiver = self.bs
        if receiver is None:
            return None
        frame = receiver.latest
        return dict(frame) if frame else None

    def status(self):
        with self._lock:
            receiver = self.bs
            port = self.port
            error = self.error
            ever = self._ever_connected
            reconnect = self.auto_reconnect
            backoff = self._reconnect_backoff

        connected = bool(receiver and receiver.ser.is_open and receiver._running)
        latest = receiver.latest if receiver else None
        frame_count = int(receiver.stats.get('data_frames', 0)) if receiver else 0
        age_s = ((time.perf_counter_ns() - latest['host_rx_ns']) / 1e9
                 if latest and 'host_rx_ns' in latest else None)
        streaming = bool(connected and frame_count and age_s is not None and age_s <= 2.0)
        parser_stats = dict(receiver.stats) if receiver else {}
        protocol_error = str(getattr(receiver, 'last_protocol_error', '') or '') if receiver else ''
        io_error = str(getattr(receiver, 'last_io_error', '') or '') if receiver else ''
        return {
            'connected': connected,  # the port is open, not proof of valid packets
            'streaming_data': streaming,  # recent CRC-checked DATA frames
            'valid_data_frames': frame_count,
            'last_data_age_s': round(age_s, 3) if age_s is not None else None,
            'decoder': 'BRUSH-V1 COBS/CRC32',
            'port': port, 'ever_connected': ever,
            'auto_reconnect': reconnect, 'reconnect_backoff': backoff,
            'error': error or io_error or protocol_error,
            'parser_stats': parser_stats,
            'hello': dict(receiver.latest_hello) if receiver and receiver.latest_hello else None,
            'health': dict(receiver.latest_health) if receiver and receiver.latest_health else None,
            'link': dict(receiver.latest_link) if receiver and receiver.latest_link else None,
        }

    def close(self):
        self._stop_reconnect.set()
        thr = self._reconnect_thread
        if thr is not None and thr.is_alive() and thr is not threading.current_thread():
            thr.join(timeout=2.0)
        self._reconnect_thread = None
        with self._lock:
            receiver, self.bs = self.bs, None
        if receiver is not None:
            receiver.close()
