from __future__ import annotations
import copy,json,os,re,threading,time
from collections import deque
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
SCHEMA_VERSION="ebrush.agent.v1"
def _j(x):
    try:
        import numpy as np
        if isinstance(x,np.ndarray):return x.tolist()
        if isinstance(x,np.generic):return x.item()
    except Exception:pass
    if isinstance(x,(str,int,float,bool)) or x is None:return x
    if isinstance(x,dict):return {str(k):_j(v) for k,v in x.items()}
    if isinstance(x,(list,tuple,deque)):return [_j(v) for v in x]
    return str(x)
def _get(d,path):
    cur=d
    for p in [x for x in path.split(".") if x]:
        if not isinstance(cur,dict) or p not in cur:raise KeyError(path)
        cur=cur[p]
    return cur
class AgentControl:
    """Versioned generic control plane. New read-only features only need publish(namespace, dict)."""
    def __init__(self,runtime_dir,host="127.0.0.1",port=8765):
        self.dir=Path(runtime_dir);self.dir.mkdir(parents=True,exist_ok=True);(self.dir/"snapshots").mkdir(exist_ok=True)
        self.host=host;self.port=int(port);self.lock=threading.RLock()
        self.s={"meta":{"schema":SCHEMA_VERSION,"started_unix":time.time(),"pid":os.getpid()}}
        self.commands={};self.params={};self.events=deque(maxlen=500);self.server=None
        self.artifact_provider=None
        self.events_path=self.dir/"events.jsonl";self.state_path=self.dir/"state.json"
        self.register_command("ping",lambda a:{"pong":True},"Liveness check")
        self.register_command("snapshot",lambda a:self.snapshot(),"Persist current state")
        self.register_command("diagnose",lambda a:self.diagnose(),"Deterministic system diagnosis")
    def register_artifact_provider(self, provider):
        """Opt-in strict ID based SAM3 archive delivery, never arbitrary file paths."""
        self.artifact_provider=provider

    def publish(self,namespace,value):
        with self.lock:self.s[str(namespace)]=_j(value);self.s["meta"]["updated_unix"]=time.time()
    def event(self,event,**fields):
        rec={"t_unix":time.time(),"event":str(event),**_j(fields)}
        with self.lock:
            self.events.append(rec)
            if self.events_path.is_file() and self.events_path.stat().st_size > 10*1024*1024:
                self.events_path.replace(self.dir/"events.jsonl.1")
            with self.events_path.open("a",encoding="utf-8") as f:
                f.write(json.dumps(rec,ensure_ascii=False)+"\n")
    def register_command(self,name,fn,description=""):self.commands[str(name)]={"fn":fn,"description":description}
    def register_parameter(self,path,getter,setter,description="",type_name=""):self.params[str(path)]={"get":getter,"set":setter,"description":description,"type":type_name}
    def schema(self):
        with self.lock:return {"schema":SCHEMA_VERSION,"commands":{k:{"description":v["description"]} for k,v in self.commands.items()},"parameters":{k:{"description":v["description"],"type":v["type"]} for k,v in self.params.items()},"namespaces":sorted(self.s)}
    def state(self,path=""):
        with self.lock:s=copy.deepcopy(self.s)
        return _get(s,path) if path else s
    def execute(self,name,args=None):
        c=self.commands.get(name)
        if not c:raise KeyError("unknown command: "+name)
        self.event("command",name=name,args=args or {});return _j(c["fn"](args or {}))
    def set_parameter(self,path,value):
        p=self.params.get(path)
        if not p:raise KeyError("parameter is not writable/registered: "+path)
        out=p["set"](value);self.event("parameter_set",path=path,value=value);return _j(out)
    def snapshot(self):
        s=self.state();stamp=time.strftime("%Y%m%d_%H%M%S")+"_%03d"%(int(time.time()*1000)%1000)
        path=self.dir/"snapshots"/("state_"+stamp+".json");path.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding="utf-8");return {"path":str(path)}
    def diagnose(self):
        s=self.state();c=[]
        def add(n,st,d=""):c.append({"name":n,"status":st,"detail":d})
        cam=s.get("camera",{});add("camera","PASS" if cam.get("state")=="RUNNING" else "WARN",cam.get("reason",""))
        br=s.get("brush",{});add("brush","PASS" if not br.get("error") else "WARN",br.get("error",""))
        po=s.get("pose",{});add("pose_reference","PASS" if po.get("reference_locked") else "WARN",po.get("reference_state",""))
        sa=s.get("sam3",{});add("sam3","PASS" if sa.get("last_frame",-1)>=0 and not sa.get("last_error") else "WARN",sa.get("last_error",""))
        live=sa.get("live",{})
        if live:
            add("sam3_live_worker","PASS" if live.get("enabled") and not live.get("last_error") else "WARN",
                live.get("last_error") or f"pending={live.get('pending')} busy={live.get('busy')}")
            add("sam3_live_backpressure","PASS" if int(live.get("pending",0))<=1 else "FAIL",
                f"rate_skipped={live.get('rate_skipped')} replaced={live.get('replaced')}")
        jobs=sa.get("video",{})
        if jobs:
            add("sam3_video_worker","PASS" if jobs.get("worker_alive") else "FAIL",
                f"queued={jobs.get('queued')} busy={jobs.get('busy_job')}")
            add("sam3_video_failed_jobs","WARN" if jobs.get("jobs_failed",0) else "PASS",
                str(jobs.get("jobs_failed",0)))
        vi=s.get("vision",{})
        if vi.get("ok"):
            dv=float(vi.get("depth_valid_ratio",0));r=float(vi.get("line_rms_mm",999))
            add("depth_valid_ratio","PASS" if dv>=.65 else "WARN",f"{dv:.3f}");add("line_fit","PASS" if r<=7 else "WARN",f"RMS {r:.2f} mm")
        else:add("vision","WARN",vi.get("reason","waiting/no valid fit"))
        va=s.get("validation",{});e=va.get("shaft_error_deg")
        add("shaft_error","SKIP" if e is None else ("PASS" if float(e)<6 else "WARN"),"camera→board unavailable" if e is None else f"{float(e):.2f} deg")
        return {"schema":SCHEMA_VERSION,"checks":c}
    def flush_state(self):
        tmp=self.dir/"state.json.tmp"
        tmp.write_text(json.dumps(self.state(),ensure_ascii=False,indent=2),encoding="utf-8")
        os.replace(tmp,self.state_path)
    def start(self):
        if self.host not in ("127.0.0.1","::1","localhost"):
            raise ValueError("Unauthenticated Agent API must bind loopback; use a secured tunnel for remote agents")
        ctl=self
        class H(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def sendj(self,o,status=200):
                b=json.dumps(_j(o),ensure_ascii=False).encode();self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)
            def body(self):
                n=int(self.headers.get("Content-Length","0"))
                if n<0 or n>128*1024:raise ValueError("Agent request body too large")
                return json.loads(self.rfile.read(n) or b"{}")
            def sendfile(self,path):
                """Stream a verified result with optional single HTTP byte range."""
                import mimetypes
                import os
                path=Path(path)
                size=path.stat().st_size
                start,end=0,size-1
                rg=self.headers.get("Range")
                if rg:
                    m=re.fullmatch(r"bytes=(\d+)-(\d*)",rg.strip())
                    if not m:
                        return self.sendj({"error":"invalid range"},416)
                    start=int(m.group(1));end=int(m.group(2)) if m.group(2) else size-1
                    if start>min(end,size-1) or start>=size:
                        return self.sendj({"error":"range unsatisfiable"},416)
                    end=min(end,size-1)
                self.send_response(206 if rg else 200)
                self.send_header("Content-Type",mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                self.send_header("Content-Length",str(max(0,end-start+1)))
                self.send_header("Accept-Ranges","bytes")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Disposition",f'attachment; filename="{path.name}"')
                if rg:self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
                self.end_headers()
                with path.open("rb") as f:
                    f.seek(start)
                    remaining=end-start+1
                    while remaining>0:
                        chunk=f.read(min(1024*1024,remaining))
                        if not chunk:break
                        self.wfile.write(chunk)
                        remaining-=len(chunk)

            def do_GET(self):
                try:
                    u=urlparse(self.path)
                    if ctl.artifact_provider is not None:
                        provider=ctl.artifact_provider
                        if u.path=="/v1/sam3/jobs":
                            return self.sendj(provider.jobs())
                        m=re.fullmatch(r"/v1/sam3/jobs/([a-f0-9]{32})",u.path)
                        if m:return self.sendj(provider.status(m.group(1)))
                        m=re.fullmatch(r"/v1/sam3/jobs/([a-f0-9]{32})/(manifest|download|source)",u.path)
                        if m:
                            jid,kind=m.groups()
                            fn={"manifest":provider.get_manifest_path,
                                "download":provider.get_results_zip,
                                "source":provider.get_source_path}[kind]
                            return self.sendfile(fn(jid))
                        m=re.fullmatch(r"/v1/sam3/jobs/([a-f0-9]{32})/masks/(\d{1,9})/(\d{1,9})",u.path)
                        if m:
                            jid,fid,oid=m.groups()
                            return self.sendfile(provider.get_mask_path(jid,int(fid),int(oid)))
                    if u.path=="/v1/state":return self.sendj(ctl.state())
                    if u.path.startswith("/v1/state/"):return self.sendj(ctl.state(u.path[10:].replace("/",".")))
                    if u.path=="/v1/schema":return self.sendj(ctl.schema())
                    if u.path=="/v1/diagnose":return self.sendj(ctl.diagnose())
                    self.sendj({"error":"not found"},404)
                except Exception as e:self.sendj({"error":f"{type(e).__name__}: {e}"},400)
            def do_POST(self):
                try:
                    u=urlparse(self.path);d=self.body()
                    if u.path=="/v1/command":return self.sendj({"ok":True,"result":ctl.execute(d["name"],d.get("args"))})
                    if u.path=="/v1/set":return self.sendj({"ok":True,"result":ctl.set_parameter(d["path"],d.get("value"))})
                    self.sendj({"error":"not found"},404)
                except Exception as e:self.sendj({"ok":False,"error":f"{type(e).__name__}: {e}"},400)
        self.server=ThreadingHTTPServer((self.host,self.port),H);threading.Thread(target=self.server.serve_forever,daemon=True,name="AgentControlHTTP").start();self.event("agent_control_started",host=self.host,port=self.port,schema=SCHEMA_VERSION)
    def close(self):
        try:self.flush_state()
        except:pass
        if self.server:self.server.shutdown();self.server.server_close()
