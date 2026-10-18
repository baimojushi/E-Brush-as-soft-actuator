# e-Brush 最终姿态重映射

## 文件

- `ebrush_pose_calibration.json`：两轮实测数据联合得到的标定参数。
- `ebrush_realtime_pose_mapper.py`：实时姿态重映射。
- `RUN_REALTIME_POSE.bat`：Windows 双击启动。

## 默认使用方法

关闭 TouchDesigner/串口监视器。

启动前把毛笔放在 `UP_R0`：

- 笔杆竖直；
- 笔尖向下、笔尾向上；
- R0 非对称标记朝纸面 `+X`（右）；
- 保持约 2 秒不动。

然后双击：

`RUN_REALTIME_POSE.bat`

看到 stderr：

`REFERENCE LOCKED`

以后即可正常运动。

## 为什么每次启动仍需要 R0

这块 IMU 是六轴方案。实测断电 A/B 的同一物理姿态只差约 1.58°，而差异几乎完全绕重力轴，但六轴 IMU 本身仍无法提供绝对地理 heading。

所以程序不会把不可观测的 yaw 冒充成绝对值。默认每次启动用一次已知 `UP_R0` 建立纸面 +X/+Y。

## 主要实时输出

每帧 stdout 输出 JSONL。

推荐使用：

- `brush_axis_x/y/z`：毛笔笔杆 +Z（笔尖→笔尾）在纸面坐标中的单位向量。
- `tilt_from_vertical_deg`：相对竖直方向倾角。
- `tilt_x_deg / tilt_y_deg`：两个纸面方向的倾斜表示。
- `shaft_azimuth_deg`：笔杆水平投影方位；笔杆接近竖直时没有物理意义。
- `heading_rel_deg`：经本次启动 R0 对齐后的相对 heading，不是磁北绝对航向。
- `brush_q_w/x/y/z`：最终 `brush -> board` 四元数。
- `pose_quality`：基于校准后加速度模长与 1g 的接近程度给出的动态质量提示。
- `reference_locked`：启动参考是否完成。
- `heading_absolute`：固定为 false，明确禁止误用成绝对罗盘航向。

## CSV

需要同时保存：

`python ebrush_realtime_pose_mapper.py --csv pose_output.csv`

## 已知标定限制

原始 `XN45_R0` 段存在明显动态污染，因此没有参与笔杆轴拟合。

手工 `R90/R180/R270` 时笔杆轴保持不够严格，因此“绕笔杆 roll 的零位”没有用这些段做高精度拟合；最终 roll 零位锚定到更可靠的 `UP_R0` 非对称标记参考。

下一阶段如果用 TouchDesigner 摄像头识别毛笔上的非对称视觉标记，可以把视觉 heading 作为低频绝对参考；IMU 继续负责高频连续姿态。

---

## SAM3 视频分割集成 (2026-10-05 新增)

新增 `sam3_video_client.py` 适配 SAM3 v1.2 API (`/video/segment`)，替代已废弃的 `/image/segment`。

### 文件
- `sam3_video_client.py` — 独立可复用的 SAM3 视频分割客户端
- `SAM3_INTEGRATION.md` — 详细集成文档

### 用途
- 实时毛笔视频分割
- 视觉轴估计 (VisionPoseEstimator)
- 与 IMU 姿态对比验证

### 快速测试
```bash
python sam3_video_client.py --prompt "calligraphy brush" --frames 30
```
需设置环境变量 `SAM3_API_KEY`。
