# E-Brush Studio · Material 3 UI V1.1 — 使用与责任边界

## 本次迭代范围

这次以仓库内的 **E-Brush 原始工作台源码**为基础迭代；并非直接修改此前单独交付的 Material 3 包（该包不在当前工程附件内）。UI 采用 Material 3 启发的 Qt 颜色与形状令牌，不引入 Material Web、WebView 或新的 UI 依赖。

**已经增加**：顶栏品牌与浅/深色主题（QSettings 持久化）、六个导航区、实时质量监控卡片、UP0 校准步骤导航、Agent 事件只读记录、RC Studio 服务只读探测、`.brlog` / `.csv` 数据移交 JSON 清单。

### 六个导航区

| E-Brush 内部页面 | 归属 / 功能 |
|---|---|
| 总览 | 采集质量、彩色相机、三维姿态、霍尔视图 |
| 传感器 | IMU/Hall 状态、磁通、三维姿态 |
| 相机 | 彩色、深度、SAM3 叠加 |
| 标定 | 姿态映射校准（含 7 步引导）、Hall 外参和三维姿态 |
| 诊断 | 接收质量、IMU ↔ 视觉验证、本地 Agent 事件 |
| RC 交接 | 只读检查 RC Studio（127.0.0.1:8766）与导出移交清单 |

视图只切换原有 QWidget 的展示状态，不创建第二套 USB 接收器，不复制相机实例，也不改变原来的采集/标定状态机。

## RC Studio 与 E-Brush 的硬边界

| 能力 | E-Brush Studio | RC Studio |
|---|---|---|
| ESP32-S3 USB 串口唯一占有 | ✅ 唯一负责 | ❌ 不连接 E-Brush 串口 |
| 传感器解码、采集、相机、姿态与外参标定 | ✅ | ❌ |
| 原始 `.brlog`/CSV 的归档来源 | ✅ | 读取副本或指定文件 |
| 实时接收质量仪表盘与 Agent 状态观测 | ✅ | 可读外部数据，但不拥有设备 |
| 原始日志解析成训练 episode、数据清洗与数据集划分 | 原始采集侧只提供质量信息 | ✅ |
| ESN、EBrush Motion Predictor V1、基线评估、模型/实验数据库 | ❌ | ✅ |
| LLM Agent | **本机 8765 服务：E-Brush 设备/标定命令** | **独立 8766 服务：训练/实验命令** |
| 互操作 | 只读 GET 端口探测与手动生成 metadata JSON | 由 RC Studio 自己执行导入和训练 |

**没有跨进程命令转发**：E-Brush 不调用 RC Studio 的 POST、不发起训练、不访问其模型存储与实验数据库。8766 的只读探测仅在用户点击“检查 RC Studio”后进行；若服务 HTTP 可达但不能确认身份，会明确显示“未验证”。

### 数据移交清单

1. 打开 `RC 交接` → `生成数据移交清单…`。
2. 选择本机真实存在的 `.brlog` 或 `.csv`。
3. 选择保存位置，生成 `.json` 文件（不会覆盖已有文件）。
4. 清单包含原始文件绝对路径、大小、SHA-256、观测质量快照和 RC 任务提示。**它不是训练数据本身**。
5. 到 **RC Studio** 按其自身的 `project ebrush import ...` / GUI 数据导入功能读取原始数据，清单用作辅助核对。RC Studio 没有确认实现清单解析器，不应声称直接支持该 JSON 格式。

注意：移交清单记录的是当前接收视窗的质量快照，并非对历史文件逐帧的验证，也不证明物理采样率或标签精度。历史 `.brlog` 的质量分析应在 RC Studio 数据导入时单独执行。

## 在 Windows 上启动

需要 Python 3.10+，硬件模式仍需原设备驱动/SDK。解压 ZIP，打开 PowerShell 进入工程根目录：

```powershell
# 首次安装：使用独立 .venv，避免影响 RC Studio 的 Python 环境
powershell -ExecutionPolicy Bypass -File .\SETUP_STUDIO.ps1

# 无设备 UI 预览（需保留 127.0.0.1:8765 空闲）
powershell -ExecutionPolicy Bypass -File .\START_UI_PREVIEW.ps1
```

若已配置了自己的 Python 环境，也可在 `studio` 目录直接运行：

```powershell
py -3 -u main.py --camera-backend none --no-brush
```

需要真实采集时，运行根目录 `RUN_STUDIO.bat`（默认 COM7，可按设备端口调整）。RC Studio 本体必须单独安装、单独启动，不在此工程包中。

## 新增代码结构

| 源文件 | 责任 |
|---|---|
| `studio/material_theme.py` | 原生 Qt Material 3 风格令牌、深浅主题 |
| `studio/studio_sections.py` | 六区页面预设；只操作显示状态 |
| `studio/quality_monitor.py` | 解码器帧计数、4 秒吞吐率/编号缺口、IMU/Hall 最新状态（纯 Python） |
| `studio/research_panels.py` | 质量、Agent 审计、RC 交接 UI |
| `studio/data_handoff.py` | 只读哈希原始文件，输出移交 JSON（纯 Python） |
| `studio/rcstudio_bridge.py` | 仅对 localhost:8766 的 GET 探测（纯 Python） |
| `studio/pose_calibration_ui.py` | 原状态机的 7 步可视化，不跳过验证 |
| `studio/main.py` | 组装导航、主题、Agent 状态、显式交接操作 |

## 验收状态和限制

- 已增加独立于 Qt/硬件的质量计算、清单不变性、只读 HTTP GET、双主题测试。
- 保留旧硬件固件、串口解码器、PoseCalibrationSession 算法和 RC Studio 的独立边界。
- **当前环境未安装 PySide6、PyQtGraph，也无真实 Windows 设备**：语法测试及无硬件逻辑测试不能代替原生 GUI 显示、COM7、RGB-D、SAM3 或跨软件联调验收。
- 质量仪表盘的接收速率基于解析器 `data_frames` 计数变化，不把 UI 刷新 FPS 当采样率；Hall 新鲜度目前由最新帧 `hall_valid` / `hall_stale` 标志反映，非完整逐帧检验。
- Agent 原有 HTTP 写接口仍属于原程序，默认应仅监听 `127.0.0.1:8765`；**此轮没有新开放 RC Studio 写接口**。如需允许不可信进程操作，应另做鉴权与权限分级。
