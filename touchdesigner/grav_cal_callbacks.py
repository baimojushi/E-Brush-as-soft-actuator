# Static gravity calibration for the e-Brush 3D pose.
# A Script CHOP on /project1/scene3d/grav_cal. Press Calibrate while holding
# the brush at the desired rest pose; the corrected quaternion output then
# reorients the 3D model so that pose reads as the gravity-aligned reference.
import numpy as np

_OUT_NAMES = ["qw_c", "qx_c", "qy_c", "qz_c"]
_READ_OP = "/project1/out1"

class _Q:
    __slots__ = ("w", "x", "y", "z")
    def __init__(self, w, x, y, z):
        self.w, self.x, self.y, self.z = float(w), float(x), float(y), float(z)
    @classmethod
    def read(cls):
        n = op(_READ_OP)  # noqa: F821 - TD global
        try:
            return cls(n["q_w"][0], n["q_x"][0], n["q_y"][0], n["q_z"][0])
        except Exception:
            return cls(1.0, 0.0, 0.0, 0.0)
    def conj(self):
        return _Q(self.w, -self.x, -self.y, -self.z)
    def mul(self, o):
        return _Q(
            self.w * o.w - self.x * o.x - self.y * o.y - self.z * o.z,
            self.w * o.x + self.x * o.w + self.y * o.z - self.z * o.y,
            self.w * o.y - self.x * o.z + self.y * o.w + self.z * o.x,
            self.w * o.z + self.x * o.y - self.y * o.x + self.z * o.w,
        )
    def norm(self):
        m = float(np.sqrt(self.w * self.w + self.x * self.x + self.y * self.y + self.z * self.z))
        if m < 1e-9:
            return self
        return _Q(self.w / m, self.x / m, self.y / m, self.z / m)
    def aslist(self):
        return [self.w, self.x, self.y, self.z]


def onSetupParameters(scriptOp):
    return [("Calibrate", 0, "Hold brush at rest pose; pulse to set gravity reference.")]


def onPulse(parName, scriptOp):
    if str(parName).lower() != "calibrate":
        return
    q = _Q.read().norm()
    parent = scriptOp.parent()
    parent.store("grav_cal", q.conj().aslist())
    parent.store("grav_cal_ready", True)
    tdu.print("[grav_cal] calibrated q=%s -> corr=%s" % (q.aslist(), q.conj().aslist()))  # noqa: F821


def onCook(scriptOp):
    cal = scriptOp.parent().fetch("grav_cal", [1.0, 0.0, 0.0, 0.0])
    calq = _Q(*cal).norm()
    live = _Q.read().norm()
    out = calq.mul(live).norm().aslist()
    # Ensure the output channels exist, then write the corrected quaternion.
    for i, name in enumerate(_OUT_NAMES):
        if i >= len(scriptOp.chans):
            scriptOp.appendChan(name)
    for i in range(4):
        scriptOp.chans[i].vals = [out[i]]