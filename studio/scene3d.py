from __future__ import annotations
import numpy as np
from PySide6 import QtWidgets, QtCore
import pyqtgraph.opengl as gl

def _empty(item):
    item.setData(pos=np.empty((0,3),float))

def _make_brush_stroke(segments=12, length=1.0, base_width=0.12, tip_width=0.02):
    """Return (positions, colors) for a tapered brush stroke along +Z axis.
    Along brush +Z from bristle tip (narrow) toward the ferrule (wider).
    """
    # Generate centerline points along Z axis
    z_vals = np.linspace(0, length, segments + 1)
    # Width tapers from base to tip
    widths = np.linspace(tip_width, base_width, segments + 1)
    
    # Build a quad strip along the centerline (4 vertices per segment: left-bottom, right-bottom, left-top, right-top)
    # But for a line plot, we'll create a thick line with varying width using multiple parallel lines
    # Simpler: create a centerline + two edge lines for the taper effect
    
    # Centerline (main stroke)
    centerline = np.column_stack([np.zeros_like(z_vals), np.zeros_like(z_vals), z_vals])
    
    # Left and right edges (for taper visualization)
    left_edge = np.column_stack([-widths/2, np.zeros_like(z_vals), z_vals])
    right_edge = np.column_stack([widths/2, np.zeros_like(z_vals), z_vals])
    # Front and back edges (slight 3D thickness)
    front_edge = np.column_stack([np.zeros_like(z_vals), -widths/2, z_vals])
    back_edge = np.column_stack([np.zeros_like(z_vals), widths/2, z_vals])
    
    # Combine all edges for a "brush stroke" wireframe
    pos = np.vstack([centerline, left_edge, right_edge, front_edge, back_edge])
    
    # Color gradient: base (ink-heavy, darker) -> tip (lighter)
    # Use a warm ink color: dark reddish-brown at base, lighter orange at tip
    n = len(z_vals)
    colors = np.zeros((5 * n, 4), float)
    for i in range(n):
        t = i / max(1, n - 1)
        # Ink color: dark brown -> warm orange
        r = 0.45 + 0.35 * t
        g = 0.20 + 0.25 * t
        b = 0.08 + 0.12 * t
        a = 0.95 - 0.35 * t  # more opaque at base
        col = [r, g, b, a]
        colors[i] = col                      # centerline
        colors[n + i] = col                  # left edge
        colors[2*n + i] = col                # right edge
        colors[3*n + i] = col                # front edge
        colors[4*n + i] = col                # back edge
    
    return pos, colors

def _make_axes(length=1.0):
    """Return (positions, colors) for XYZ axes from origin."""
    verts = np.array([
        [0,0,0], [length,0,0],
        [0,0,0], [0,length,0],
        [0,0,0], [0,0,length],
    ], float)
    colors = np.array([
        [1,0,0,1],[1,0,0,1],
        [0,1,0,1],[0,1,0,1],
        [0,0,1,1],[0,0,1,1],
    ], float)
    return verts, colors

def _quat_to_R(q):
    """Hamilton quaternion (w,x,y,z) -> rotation matrix."""
    w,x,y,z = map(float, q)
    n = np.sqrt(w*w + x*x + y*y + z*z)
    if n < 1e-12:
        return np.eye(3)
    w,x,y,z = w/n, x/n, y/n, z/n
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w),   2*(x*z+y*w)],
        [2*(x*y+z*w),   1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w),   2*(y*z+x*w),   1-2*(x*x+y*y)],
    ], float)

class Scene3D(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        L=QtWidgets.QVBoxLayout(self)
        self.view=gl.GLViewWidget(); L.addWidget(self.view)
        self.view.setCameraPosition(distance=900,elevation=28,azimuth=-55)
        grid=gl.GLGridItem(); grid.setSize(700,700); grid.setSpacing(50,50); self.view.addItem(grid)
        
        # --- Existing items ---
        self.imu=gl.GLLinePlotItem(width=5,antialias=True)
        self.vis=gl.GLLinePlotItem(width=4,antialias=True)
        self.axes=[gl.GLLinePlotItem(width=3,antialias=True) for _ in range(3)]
        self.hall_local_axes=[gl.GLLinePlotItem(width=6,antialias=True) for _ in range(3)]
        self.hall_local_zero=[gl.GLLinePlotItem(width=1,antialias=True) for _ in range(3)]
        self.world_flux_mean=gl.GLLinePlotItem(width=5,antialias=True)
        self.world_flux_inst=gl.GLLinePlotItem(width=2,antialias=True)
        self.world_flux_cloud=gl.GLScatterPlotItem(size=5,pxMode=True)
        for x in [self.imu,self.vis,*self.axes,*self.hall_local_axes,*self.hall_local_zero,self.world_flux_mean,self.world_flux_inst,self.world_flux_cloud]:
            self.view.addItem(x)
        
        # --- NEW: IMU Orientation Cube (3-axis attitude simulation) ---
        self.imu_cube = gl.GLLinePlotItem(width=3, antialias=True)
        self.imu_axes = gl.GLLinePlotItem(width=4, antialias=True)
        self.r0_marker = gl.GLLinePlotItem(width=6, antialias=True)
        self.view.addItem(self.imu_cube)
        self.view.addItem(self.imu_axes)
        self.view.addItem(self.r0_marker)
        self.imu_cube_scale = 80.0  # mm
        self.imu_axes_len = 120.0   # mm
        
        # --- NEW: Euler angle labels (2D overlay) ---
        self.euler_info = QtWidgets.QLabel("IMU 姿态：Roll=0.0° Pitch=0.0° Yaw=0.0°")
        self.euler_info.setStyleSheet("font-family: monospace; font-size: 12px; color: #e0e0e0; background: rgba(0,0,0,150); padding: 6px;")
        self.euler_info.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        # Add as overlay widget (will be positioned in resizeEvent)
        self.euler_info.setParent(self.view)
        self.euler_info.move(10, 10)
        self.euler_info.show()
        
        self.pc=gl.GLScatterPlotItem(size=1.5,pxMode=True); self.view.addItem(self.pc)
        self.info=QtWidgets.QLabel("三维场景：等待数据"); L.addWidget(self.info)
        self.show_pc=True
        self.world_anchor=np.array([-220.0,-220.0,90.0],float)

    def _draw_pointcloud(self, camera):
        if not self.show_pc or camera is None or camera.get("pointcloud") is None:
            self.pc.setData(pos=np.empty((0,3)),size=1.2,color=(.65,.65,.65,.0)); return
        p=np.asarray(camera["pointcloud"])
        if p.ndim==3 and p.shape[2]>=3: p=p[::8,::8,:3].reshape(-1,3)
        elif p.ndim==2 and p.shape[1]>=3: p=p[::64,:3]
        else: p=np.empty((0,3))
        p=p[np.isfinite(p).all(1)] if len(p) else p
        if len(p):
            med=np.median(np.linalg.norm(p,axis=1))
            p=p*(1000 if med<10 else 1)
            p=p[np.linalg.norm(p,axis=1)<3000][:12000]
            self.pc.setData(pos=p,size=1.2,color=(.65,.65,.65,.35))
        else:
            self.pc.setData(pos=np.empty((0,3)),size=1.2,color=(.65,.65,.65,.0))

    def update_scene(self, camera=None, pose=None, vision=None, hall=None):
        O=np.zeros(3)
        for item in [self.imu,self.vis,*self.axes,*self.hall_local_axes,*self.hall_local_zero,self.world_flux_mean,self.world_flux_inst,self.imu_cube,self.imu_axes,self.r0_marker]:
            _empty(item)
        self.world_flux_cloud.setData(pos=np.empty((0,3)), size=5, color=(0.0,0.0,0.0,0.0))
        hall_txt="霍尔：无数据"
        if pose and pose.get("reference_locked") and "brush_axis_x" in pose:
            z=np.array([pose["brush_axis_x"],pose["brush_axis_y"],pose["brush_axis_z"]],float)
            x=np.array([pose["brush_x_axis_x"],pose["brush_x_axis_y"],pose["brush_x_axis_z"]],float)
            y=np.array([pose["brush_y_axis_x"],pose["brush_y_axis_y"],pose["brush_y_axis_z"]],float)
            self.imu.setData(pos=np.vstack([O,O+z*260]),color=(1,1,1,1))
            for item,v,col in zip(self.axes,[x,y,z],[(1,0,0,1),(0,1,0,1),(0,0,1,1)]):
                item.setData(pos=np.vstack([O,O+v*100]),color=col)
            
            # --- NEW: IMU Orientation Brush Stroke & Axes (3-axis attitude simulation) ---
            if "brush_q_w" in pose and "brush_q_x" in pose and "brush_q_y" in pose and "brush_q_z" in pose:
                q = np.array([pose["brush_q_w"], pose["brush_q_x"], pose["brush_q_y"], pose["brush_q_z"]], float)
                R = _quat_to_R(q)
                
                # Brush +Z is tip -> tail. Bristles start at the paper tip (O)
                # and widen toward the ferrule, NOT beyond the tail.
                stroke_pos = O
                stroke_verts, stroke_colors = _make_brush_stroke(
                    segments=16, length=65.0, base_width=23.0, tip_width=2.5
                )
                # Rotate and translate: stroke local +Z aligns with brush shaft (z)
                stroke_verts = (R @ stroke_verts.T).T + stroke_pos
                self.imu_cube.setData(pos=stroke_verts, color=stroke_colors)
                
                # Render IMU axes at brush origin
                axes_verts, axes_colors = _make_axes(self.imu_axes_len)
                axes_verts = (R @ axes_verts.T).T + O
                self.imu_axes.setData(pos=axes_verts, color=axes_colors)
                
                # Update 2D euler angle overlay
                roll = pose.get("roll_about_brush_rel_deg", 0.0)
                pitch_x = pose.get("tilt_x_deg", 0.0)
                pitch_y = pose.get("tilt_y_deg", 0.0)
                yaw = pose.get("heading_rel_deg", 0.0)
                shaft_az = pose.get("shaft_azimuth_deg", 0.0)
                self.euler_info.setText(
                    f"IMU 姿态 (毛笔坐标系):\n"
                    f"  Roll (绕轴):     {roll:+7.1f}°\n"
                    f"  Pitch X (侧倾):  {pitch_x:+7.1f}°\n"
                    f"  Pitch Y (前后倾): {pitch_y:+7.1f}°\n"
                    f"  Yaw (偏航):      {yaw if yaw is not None else '—':>7}\n"
                    f"  Shaft Azimuth:   {shaft_az if shaft_az is not None else '—':>7}°"
                )
            else:
                _empty(self.imu_cube)
                _empty(self.imu_axes)
                _empty(self.r0_marker)
                self.euler_info.setText("IMU 姿态：等待四元数数据")
            
            if hall and hall.get("present") and hall.get("runtime_mapping",{}).get("compatible",True):
                ratio=float(hall.get("sensor_origin_ratio_on_brush",0.65))
                S=O+z*(260*ratio)
                comps=hall.get("brush_components_mT",{})
                vals=[float(comps.get("x",0.0)), float(comps.get("y",0.0)), float(comps.get("z",0.0))]
                basis=[x,y,z]
                cols=[(1,0.35,0.35,0.96),(0.25,0.95,0.45,0.96),(0.35,0.55,1,0.96)]
                vmax=max(0.05, max(abs(v) for v in vals))
                for zero_item, item, val, vec, col in zip(self.hall_local_zero, self.hall_local_axes, vals, basis, cols):
                    zero_item.setData(pos=np.vstack([S-vec*34,S+vec*34]), color=(0.7,0.72,0.78,0.18))
                    half=65.0*abs(val)/vmax
                    item.setData(pos=np.vstack([S-vec*half, S+vec*half]), color=col)

                W0=self.world_anchor
                mean=hall.get("world_mean_unit"); inst=hall.get("world_unit"); B=float(hall.get("B_total_mT",0.0))
                if mean is not None:
                    mu=np.asarray(mean,float)
                    self.world_flux_mean.setData(pos=np.vstack([W0,W0+mu*(120+min(80,B*180))]), color=(0.2,1.0,1.0,0.98))
                if inst is not None:
                    iu=np.asarray(inst,float)
                    self.world_flux_inst.setData(pos=np.vstack([W0,W0+iu*(110+min(70,B*170))]), color=(1.0,1.0,0.55,0.75))
                hist=np.asarray(hall.get("world_history_xyz",[]),float)
                if hist.size:
                    H=hist.copy()
                    mags=np.linalg.norm(H,axis=1)
                    mask=mags>1e-9
                    H=H[mask]; mags=mags[mask]
                    if len(H):
                        U=H/mags[:,None]
                        pts=W0 + U*(95 + np.minimum(85,mags[:,None]*160))
                        self.world_flux_cloud.setData(pos=pts, size=4, color=(0.2,1.0,1.0,0.22))
                hall_txt=f'霍尔 |B|={B:.3f} mT | 世界磁通固定显示'
            elif hall and hall.get("present"):
                hall_txt="霍尔外参与当前 PoseMapper 安装方向不兼容；已隐藏三维映射"
        else:
            # No valid pose - clear IMU visualization
            _empty(self.imu)
            _empty(self.imu_cube)
            _empty(self.imu_axes)
            _empty(self.r0_marker)
            for item in self.axes:
                _empty(item)
            self.euler_info.setText("等待 UP0 确认与参考锁定：笔尖向下、笔尾向上，R0 朝纸面 +X")
        
        if vision and vision.get("ok") and "axis_camera" in vision:
            a=np.asarray(vision["axis_camera"],float)
            o=np.asarray(vision.get("origin_camera_mm",[0,0,0]),float)
            ln=float(vision.get("visible_length_mm",220) or 220)
            self.vis.setData(pos=np.vstack([o-a*ln*.5,o+a*ln*.5]),color=(1,1,0,1))
        self._draw_pointcloud(camera)
        self.info.setText("白色=IMU 映射 | 黄色=视觉（相机坐标系） | RGB=毛笔局部轴 | 黄色短线=R0 +X 标记 | 笔杆上的粗红绿蓝=局部霍尔瞬时分量（居中） | 左下角青/黄=世界磁通均值/瞬时 | 纸面网格=700 mm | 蓝/红/绿立方体=IMU 三轴姿态 | "+hall_txt)
