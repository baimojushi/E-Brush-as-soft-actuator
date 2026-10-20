from pathlib import Path
import sys
import threading
import time
import serial
class HP60CSource:
    def __init__(self,root,backend="direct"):self.root=Path(root);self.backend=backend;self.impl=None;self.error=""
    def start(self):
        try:
            if self.backend=="none":
                self.impl=None;self.error="按请求禁用 HP60C（仅用于姿态/Hall 标定）"
                return
            if self.backend=="direct":
                from hp60c_direct import HP60CDirectSDK
                original_sdk = r"D:\Express Program Files\Angstrong Viewer V2.1.10_240109\Angstrong Viewer V2.1.10_240109\RelWithDebInfo"
                self.impl=HP60CDirectSDK(Path(original_sdk));self.impl.start()
            else:
                d=self.root.parent/"hp60c_v13";sys.path.insert(0,str(d));import hp60c_shm_reader_v13 as r;self.impl=r.Reader()
        except Exception as e:self.error=f"{type(e).__name__}: {e}";self.impl=None
    def poll(self):
        if not self.impl:return None
        try:return self.impl.poll() if self.backend=="direct" else self.impl.try_get_latest(max_age_ms=500)
        except Exception as e:self.error=str(e);return None
    def status(self):
        if not self.impl:return {"state":"OFFLINE","reason":self.error}
        try:return self.impl.status() if self.backend=="direct" else self.impl.get_status()
        except Exception as e:return {"state":"ERROR","reason":str(e)}
    def close(self):
        if self.impl:
            try:self.impl.close()
            except:pass

def auto_detect_port(verbose=False):
    import serial.tools.list_ports
    ports = list(serial.tools.list_ports.comports())
    candidates = []
    for p in ports:
        vid = p.vid or 0
        pid = p.pid or 0
        if (vid == 0x1A86 and pid == 0x7523) or (vid == 0x0403 and pid == 0x6001):
            candidates.append(p.device)
        if verbose:
            print(f"[PORT] {p.device} vid={vid:04x} pid={pid:04x} desc={p.description}")
    if verbose:
        print(f"[PORT] Keyword candidates ({len(candidates)}): {candidates}")
    if not candidates:
        return None
    return candidates[0]

class BrushSerial:
    def __init__(self, port, baud=921600):
        self.port = port
        self.baud = baud
        self.ser = None
        self.latest = None
        self._thread = None
        self._running = False
        
    def start(self):
        self._connect()
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        
    def _connect(self):
        self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        print(f"[BRUSH] Connected to {self.port} @ {self.baud}")
        
    def _read_loop(self):
        buf = bytearray()
        while self._running:
            try:
                if self.ser and self.ser.is_open:
                    data = self.ser.read(1024)
                    if data:
                        buf.extend(data)
                        while b'\n' in buf:
                            line, buf = buf.split(b'\n', 1)
                            try:
                                import json
                                self.latest = json.loads(line.decode())
                            except:
                                pass
                else:
                    time.sleep(0.1)
            except Exception as e:
                print(f"[BRUSH] Read error: {e}")
                break
                
    def close(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        if self.ser and self.ser.is_open:
            self.ser.close()

class BrushSource:
    def __init__(self, root_dir, port=None, baud=921600, auto_reconnect=True):
        self.root_dir = Path(root_dir)
        self.port = port
        self.baud = baud
        self.auto_reconnect = auto_reconnect
        self.bs = None
        self.error = ""
        self._lock = threading.Lock()
        self._reconnect_thread = None
        self._stop_reconnect = False
        self._ever_connected = False
        self._reconnect_backoff = 1.0
        
    def start(self):
        try:
            if str(self.root_dir / "studio") not in sys.path:
                sys.path.insert(0, str(self.root_dir / "studio"))
            from brush_serial import BrushSerial as StudioBrushSerial
            if self.port is None:
                self.port = auto_detect_port(verbose=True)
            self._connect_brush()
            if self.auto_reconnect:
                self._start_reconnect_thread()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self.bs = None
            
    def _connect_brush(self):
        if self.port is None:
            self.port = auto_detect_port(verbose=True)
        if self.port is None:
            raise RuntimeError("未检测到毛笔串口")
        self.bs = BrushSerial(self.port, self.baud)
        self.bs.start()
        self._ever_connected = True
        self._reconnect_backoff = 1.0
        self.error = ""
        print(f"[BRUSH] Connected to {self.port}")
        
    def _start_reconnect_thread(self):
        self._stop_reconnect = False
        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._reconnect_thread.start()
        
    def _reconnect_loop(self):
        while not self._stop_reconnect:
            time.sleep(2)
            with self._lock:
                if self.bs is None and self._ever_connected:
                    self._do_reconnect()
                    
    def _do_reconnect(self):
        print("[BRUSH] Attempting reconnect...")
        new_port = auto_detect_port(verbose=False)
        if new_port is None:
            print("[BRUSH] No port found, waiting...")
            self._reconnect_backoff = min(self._reconnect_backoff * 1.5, 10.0)
            time.sleep(self._reconnect_backoff)
            return
        try:
            self.port = new_port
            self.bs = BrushSerial(self.port, self.baud)
            self.bs.start()
            self._reconnect_backoff = 1.0
            print(f"[BRUSH] Reconnected to {self.port}")
        except Exception as e:
            print(f"[BRUSH] Reconnect failed: {e}")
            self.bs = None
            self._reconnect_backoff = min(self._reconnect_backoff * 1.5, 10.0)
            time.sleep(self._reconnect_backoff)
            
    def latest(self):
        with self._lock:
            try:
                return dict(self.bs.latest) if self.bs and self.bs.latest else None
            except Exception:
                return None
                
    def status(self):
        with self._lock:
            connected = self.bs is not None and self.bs.ser is not None and self.bs.ser.is_open
            return {
                "connected": connected,
                "port": self.port,
                "ever_connected": self._ever_connected,
                "auto_reconnect": self.auto_reconnect,
                "reconnect_backoff": self._reconnect_backoff,
                "error": self.error
            }
            
    def close(self):
        self._stop_reconnect = True
        if self._reconnect_thread:
            self._reconnect_thread.join(timeout=1)
        with self._lock:
            if self.bs:
                try:
                    self.bs.close()
                except:
                    pass
                self.bs = None
