# E-Brush Studio × SAM3 6015 — 生产数据回路

## 已落地的源码能力

本交付实现的是 **Studio 源码侧** 的实时关键帧掩码 + 全视频上传/处理/结果下发。**SAM3 1.6–3 FPS 不会因为客户端改变而变为 20 FPS 全帧语义推理。** 本实现不篡改 SAM3 服务，不新增 GPU 模型实例。

| 通路 | 实现 | 结果与验收边界 |
| --- | --- | --- |
| 实时语义 | `studio/sam3_client.py` | bounded/latest-only RGB 帧、`POST /v1/segment` mode=single、处理所有返回对象掩码、原帧 ID 和 host 时间戳、彩色 overlay、PNG 掩码档案、错误和速率计数 |
| 全视频处理 | `studio/sam3_video_jobs.py` | 读取**全部**视频帧、按服务帧数上限分块（默认 50，硬上限 400）、`POST /video/segment?batch_mode=true`、完整 ZIP 解包、帧号严格映射、逐块原子提交、失败续跑 |
| 结果下发 | `studio/agent_control.py` | 作业状态/manifest、完整 ZIP、原始视频、单帧单对象掩码，可使用 HTTP Range 续传 |
| Studio UI | `studio/main.py`、`studio/sam3_production_panel.py` | 上传按钮、队列暂停/恢复、失败重试、逐帧掩码叠加预览、切换回实时画面 |
| Agent 侦控 | `tools/sam3_studio_agent.py` | 结构化 schema/state/diagnose、命令、历史事件、SHA256、作业数、失败原因、下载工具 |

**完整视频**指被明确选中/提交的源视频内可解码的全部帧，绝不偷偷设置 frame_stride>1。若视频声明帧数与实际解码帧数不一致，作业失败，不能标记完整。摄像机端全量 RGB-D 原始录制是独立课题：主 Studio 的 `HP60CSource.poll()` 为最新快照接口，不能据此声称无损记录每个 SDK 回调帧。真实完整 RGB-D 数据需使用项目已有 `studio/rgbd_semantic/studio_capture.py` 单所有者 SDK 队列采集与档案通路。

## 连接 Molink-Infer 6015

SAM3 部署仅监听 **Molink-Infer 上的 `127.0.0.1:6015`**。Studio 若在另一台 Windows 机器上运行，该 `127.0.0.1` 并不自动指向 Molink-Infer。可以通过已授权的 SSH 隧道将 Windows 本机端口转发到服务器：

```powershell
ssh -N -L 6015:127.0.0.1:6015 <your_molink_ssh_host>
```

或者使用已有经认证的 HTTPS 反向代理。不要为便于访问而将 `ALLOW_UNAUTH_LOCAL=1` 服务绑定到公网或无认证局域网。

`config/vision_validation.json` 的 SAM3 默认基址改为 `http://127.0.0.1:6015`。也可从命令行覆盖：

```powershell
python studio/main.py --camera-backend shm --sam3-url http://127.0.0.1:6015
```

依赖：`numpy`、`opencv-python`、`requests`、`PySide6`、`pyqtgraph`、`pyserial`，以及原 Studio 原有依赖。Studio 内的网络操作在后台线程完成，不加载本地 HF 模型。

## Studio 操作

1. 在主界面的 **SAM3 视频上传 / 完整掩码** dock 中点击“选择视频并提交完整处理”；该操作不会让 Qt UI 等待上传完成。
2. 每个作业会分块发送视频、下载每块 ZIP，并保存可检验的分块结果。界面显示 `QUEUED/RUNNING/COMPLETED/FAILED/PAUSED/INTERRUPTED`、已完成帧数、掩码数与错误。
3. 成功后拖动滑条、松开，浏览任一原视频帧与所有对象掩码叠加；“返回实时掩码”切回实时关键帧视图。
4. 网络中断或 Studio 重启后，使用“失败续跑”，从已校验 SHA256 的完整分块继续。中断的当前分块会重新发送，不会将半包结果标记成功。
5. 原视频、掩码 ZIP、`frames.jsonl`、`manifest.json` 保存在 `runtime/sam3_video_jobs/<job_id>/`。不要删除其中的 `source.*`、`chunks/` 来清理运行时缓存，它们是可追溯档案的一部分。

在线关键帧保持最新帧优先：默认最多每秒选取 1 个新帧，处理端仍可能更慢。未参与推理的帧计入 `rate_skipped`，已接收但被新帧替换的计入 `replaced`。**这不等于原始 HP60C 相机流发生丢帧。** 所有返回掩码只附加到其真正对应的来源帧上，`object_id_scope=frame`，`label_status=CANDIDATE`。

## 帧时间戳与 RGB-D 数据合同

视频文件仅有相对播放时间时，默认 `source_frame_id=video_frame_index`、`source_timestamp_ns=null`，不是 HP60C 相机真实时间戳。要保留原始设备对应关系，可在视频文件旁放置 `video.mp4.frames.jsonl`，或通过 Agent 命令显式指定 `frame_manifest_path`。每行示例：

```json
{"video_frame_index":0,"source_frame_id":1250,"source_timestamp_ns":1792728000000000000}
{"video_frame_index":1,"source_frame_id":1251,"source_timestamp_ns":1792728000050000000}
```

侧车文件与原始视频一并保存并计算 SHA256；必须与解码帧数完全一致，ID 和提供的时间戳严格递增。**复制这些时间戳并不证明 RGB/depth 硬件同步或深度配准成功。** 原图到推理图的实际缩放比例、尺寸和最近邻掩码反变换保存在 `frames.jsonl`，无裁切、无填充。所有 3D 标注仍为候选，真实 HP60C 标定与数据采集需要独立验收。

## Agent API（默认仅本机回环 `127.0.0.1:8765`）

只读：

```text
GET /v1/schema
GET /v1/state/sam3
GET /v1/diagnose
GET /v1/sam3/jobs
GET /v1/sam3/jobs/<job_id>
GET /v1/sam3/jobs/<job_id>/manifest
GET /v1/sam3/jobs/<job_id>/download        # 所有帧掩码 ZIP / manifest，不重复打包原视频
GET /v1/sam3/jobs/<job_id>/source          # 源视频独立下载
GET /v1/sam3/jobs/<job_id>/masks/<video_frame_index>/<object_id>
```

`download`、`source` 和 `masks` 支持 `Range: bytes=start-end`。仅允许严格 job UUID 和数字帧/对象 ID；不支持任意服务器文件路径读取。下载 ZIP 内 `frames.jsonl` 对每帧均有记录，`NO_OBJECT` 为模型空结果，不能删除来伪造完成率。

控制调用（POST `/v1/command`）：

```json
{"name":"sam3.video.submit","args":{"path":"D:/videos/episode.mp4","prompt":"calligraphy brush"}}
{"name":"sam3.video.retry","args":{"job_id":"<job_id>"}}
{"name":"sam3.video.pause","args":{}}
{"name":"sam3.video.resume","args":{}}
{"name":"sam3.video.status","args":{"job_id":"<job_id>"}}
{"name":"sam3.live.enable","args":{"enabled":true}}
{"name":"sam3.live.status","args":{}}
{"name":"diagnose","args":{}}
```

也可用辅助 CLI：

```bash
python tools/sam3_studio_agent.py diagnose
python tools/sam3_studio_agent.py upload /path/to/episode.mp4 --prompt brush
python tools/sam3_studio_agent.py jobs
python tools/sam3_studio_agent.py download <job_id> --output result.zip
```

注意：传给 `sam3.video.submit` 的 `path` 是 **Studio 计算机本地路径**，不是 Agent 所在远程电脑的路径。使用网络挂载时要保证 Studio 进程有读取权限。Agent HTTP 默认无额外身份验证，因此源码拒绝绑定非回环 IP；远程控制应通过安全隧道和授权网关，不得裸露接口。

## Debug 与恢复清单

- `GET /health/ready` 只说明服务可接收请求；真实成功必须看到有效单帧 PNG 或完整 ZIP。
- `sam3.live.status` 可看 `accepted/completed/failed/rate_skipped/replaced/last_error`；`sam3.video.status` 可看 `queued/busy_job/jobs_failed`。
- 本地 `runtime/events.jsonl`（10 MB 轮转）和 `runtime/sam3_video_jobs/events.jsonl` 记录操作与失败；`runtime/state.json` 原子替换，避免快照中途崩溃导致 JSON 损坏。
- `401/403`：检查 API key/代理配置，不要重试来绕过认证。`404`：先核查 endpoint；HF Single `/v1/segment`、Batch `/video/segment?batch_mode=true`。
- `500/502/503/504`、超时：带上帧号和作业 ID 检查远端模型状态；临时 HTTP 错误最多重试 2 次；未知 ZIP 格式或缺失 mask 得分会 **FAIL CLOSED**，不能生成假掩码。
- 断开/关机：已完成分块保存 `response.zip/chunk.json/PNG` 且附 SHA256，重启后标记 `INTERRUPTED`；Agent/UI 可继续，受损分块单独重传。
- `ERROR: original frame count mismatch`：输入视频可能损坏或解码器未能读完；换可信原视频。**不能改 `frame_stride` 掩盖问题。**
- `ERROR: sidecar ...`：校对相机原始帧 ID/时间戳和视频真实帧数；不能自动填充或伪造时间戳。
- `not enough space`：保留已有作业档案，清理无关盘空间或扩容存储后重试；不会悄悄删除原视频。
- 原始 20 FPS 输入与 SAM3 约 1–3 FPS 输出需要业务分离。扩大队列不能产生 20 FPS 全帧实时语义能力。
- 本实现把“实时掩码”定义为**实时异步下发的关键帧全对象掩码**。若需要全采集帧掩码，请提交完整录制视频走离线全帧处理。

## 代码侧验证与实际生产验收边界

```bash
pytest -q studio/tests/test_sam3_production.py
python -m compileall -q studio
sh tests/run_local_tests.sh
```

本地 Mock HTTP 试验覆盖：单帧多对象、完整视频多分块、全部原始帧序号、ZIP 校验、原视频和结果下发、HTTP Range 下载、故障后从校验过的分块续跑。**这些不等于已经连接 Molink-Infer 6015 或真实 HP60C。** 本次交付环境不具备服务端/实际摄像头和 PySide6，故不能声称已通过真实生产联调或 GUI 视觉验证。实际部署时只需做一次最小真实业务验证：1 张图片 + 短视频 ZIP + Studio 界面预览/Agent 下载，对照 6015 真实响应字段调整兼容性即可；不要求重跑新的长时间 GPU 压测。
