from pathlib import Path
class ShaderWatch:
    def __init__(self,path):self.path=Path(path);self.mtime=0;self.source="";self.error=""
    def poll(self):
        try:
            m=self.path.stat().st_mtime
            if m>self.mtime:self.source=self.path.read_text(encoding="utf-8");self.mtime=m;self.error="";return True
        except Exception as e:self.error=str(e)
        return False
