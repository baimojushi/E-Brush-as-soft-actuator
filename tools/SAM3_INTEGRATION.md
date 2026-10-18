# e-Brush Studio SAM3 视频分割集成

## 概述

为 e-Brush Studio 添加 SAM3 (Segment Anything Model 3) 视频分割能力，用于实时毛笔/书写轨迹的视觉分割与姿态验证。

## 架构变更

### API 版本对齐
- **旧版**: `/image/segment` (单帧分割，已废弃)
- **新版**: `/video/segment` (视频分割，SAM3 v1.2+)

### 核心组件

| 文件 | 说明 |
|------|------|
| `e-Brush/tools/sam3_video_client.py` | 独立可复用的 SAM3 视频分割客户端 |
| `ebrush_python_studio_v06_autodebug/app/sam3_client.py` | Studio 内部使用的集成版本 |
| `ebrush_python_studio_v06_autodebug/config/vision_validation.json` | 统一配置文件 |

## 配置说明

### vision_validation.json (sam3 节点)
```json
{
  "sam3": {
    "base_url": "https://desktop-kc6mgc1.tailb2b872.ts.net/sam3",
    "api_key_env": "SAM3_API_KEY",
    "prompt": "calligraphy brush",
    "jpeg_quality": 88,
    "timeout_s": 30.0,
    "min_interval_s": 0.5,
    "clip_frames": 30,
    "clip_fps": 30
  }
}
```

| 参数 | 说明 | 推荐值 |
|------|------|--------|
| `base_url` | SAM3 服务地址 (Tailscale Funnel) | 见上 |
| `api_key_env` | 环境变量名，存放 API Key | `SAM3_API_KEY` |
| `prompt` | 文本提示词 (COCO 类别) | `calligraphy brush` / `pen` / `brush` |
| `jpeg_quality` | 帧编码质量 | 80-90 |
| `timeout_s` | 请求超时 | 30-60s |
| `min_interval_s` | 两次请求最小间隔 | 0.5-2.0s |
| `clip_frames` | 每个 clip 的帧数 | 15-60 |
| `clip_fps` | clip 编码帧率 | 30 |

### 环境变量
```bash
# 必须设置
export SAM3_API_KEY="your-api-key-from-molink-console"

# 可选：本地开发免认证 (服务端需设置 ALLOW_UNAUTH_LOCAL=1)
# export SAM3_API_KEY=""
```

## 使用方法

### 1. 独立测试
```bash
cd e-Brush/tools
python sam3_video_client.py --prompt "calligraphy brush" --frames 30
```

### 2. Studio 集成启动
```bash
cd ebrush_python_studio_v06_autodebug
python -m app.main --sam3-prompt "calligraphy brush"
```

### 3. 运行时调整 (GUI)
Studio 启动后，右侧面板提供：
- **SAM3 prompt**: 文本提示词
- **Clip frames**: 每次分割的帧数 (5-120)
- **Clip FPS**: 编码帧率 (10-60)
- **SAM3 health**: 健康检查按钮

## 数据流向

```
HP60C 相机 (30fps)
    ↓
Studio main.tick() 采集 RGB 帧
    ↓
SAM3Client.submit(rgb, frame_id, host_ns, camera_context)
    ↓
内部缓冲 → 组装 clip (clip_frames 帧) → 写临时 MP4
    ↓
POST /video/segment (multipart: file + text_prompt + params)
    ↓
SAM3 服务端推理 (双 RTX 3090)
    ↓
返回 ZIP (含所有帧的 PNG 掩码 + meta.json)
    ↓
客户端解码 → 取最新帧最佳掩码 → VisionPoseEstimator 估计 3D 轴
    ↓
与 IMU 姿态对比 → 可视化验证
```

## 关键设计决策

### 为什么用视频 API 替代单帧 API？
1. **SAM3 官方只提供视频端点** - v1.2 移除了 `/image/segment`
2. **时序一致性** - 视频分割利用帧间关联，掩码更稳定
3. **批处理效率** - 单次请求处理多帧，摊销网络/模型加载开销

### 实时性权衡
- **延迟来源**: 网络传输 + 模型推理 + 客户端缓冲组装
- **典型延迟**: 500ms - 3s (取决于 clip_frames 和服务端负载)
- **缓解策略**: 
  - `min_interval_s` 控制请求频率
  - 仅保留最新 clip_frames 帧，旧帧自动丢弃
  - 结果仅用于**事后验证/可视化**，不阻塞主管线

### 掩码选择策略 (`_pick`)
结合三个因子：
1. **SAM 原始分数** (主导)
2. **形状先验** - 偏好细长形状 (elongation)，符合毛笔几何
3. **时序一致性** - IoU 与上一帧掩码对比

## 常见问题

### Q: 健康检查通过但分割无结果？
- 检查 `prompt` 是否为 COCO 支持类别：`person`, `brush`, `pen`, `pencil`, `hand`
- 尝试更通用的词：`brush`, `writing instrument`
- 确认视频内容确实包含目标物体

### Q: 显存不足 (OOM)？
- 减小 `clip_frames` (如 15)
- 减小 `MAX_SIDE` 环境变量 (服务端配置，默认 1024)
- 使用 `frame_stride` 采样 (当前固定为 1)

### Q: 请求超时？
- 增加 `timeout_s` 到 60-120s
- 减少 `clip_frames`
- 检查 Tailscale 网络延迟

### Q: 掩码质量差/抖动？
- 增加 `clip_frames` 让时序平滑更强
- 调整 `VisionPoseEstimator` 的 `erode_px`, `ransac_threshold_mm`
- 确保光照充足、背景干净

## 监控与调试

### Agent 发布的状态 (端口 8765)
```json
{
  "sam3": {
    "last_frame": 1234,
    "prompt": "calligraphy brush",
    "last_error": "",
    "latency_ms": 1250.3,
    "score": 0.87
  }
}
```

### 日志关键字
- `SAM3Worker` - 工作线程名
- `submit()` - 帧提交
- `latest()` - 结果读取
- `latency_ms` - 端到端延迟

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-10-05 | 初始集成：适配 SAM3 v1.2 `/video/segment`，替代废弃 `/image/segment` |

## 相关文档
- SAM3 API 文档: `D:\BaiduNetdiskDownload\SAM3_API_使用文档.md`
- e-Brush 姿态映射: `e-Brush/tools/README_CN.md`
- Studio 架构: `ebrush_python_studio_v06_autodebug/README.md` (如存在)