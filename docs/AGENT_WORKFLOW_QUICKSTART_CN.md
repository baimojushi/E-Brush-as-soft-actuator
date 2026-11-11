# 双 Studio Agent 工作流 · Windows 安装、操作与验收

> 版本：E-Brush Studio V1.3 / RC Studio V0.5。两套程序使用各自的工作目录、进程、服务端口和数据库。编排器运行在 RC Studio 内，采集执行器运行在 E-Brush 内。

## 1. 工作流能做什么

- **实时采集模式**：RC 创建工作流 → E-Brush 设备健康检查 → 操作员确认每次握笔动作 → E-Brush 录制独立 `.brlog` → 自动生成 SHA-256 移交清单 → RC 验证并导入 → 按原始记录隔离训练/验证/测试 → ESN 训练、基线评估与保存结果。
- **离线清单模式**：选择至少三份不同原始采集记录的移交清单 → RC 离线核验、训练与评估；无需连接笔端。
- **事件可视化**：RC 每约 950 ms 刷新工作流列表与步骤时间线；实时模式将同一 `workflow_id` 的序列事件镜像到 E-Brush，E-Brush 约每 450 ms 刷新采集面板。跨软件刷新延迟受本机运行状态影响。
- **外部 Agent**：RC 的 MCP（`--allow-write`）或 HTTP API 可创建和读取工作流；默认 MCP 为只读。完成训练所需的工作流执行器由 RC Studio 本地服务运行，外部 Agent 负责规划、调用工具与回应用户。

### 责任与数据边界

| E-Brush Studio · 127.0.0.1:8765 | RC Studio · 127.0.0.1:8766 |
|---|---|
| 唯一持有 USB/COM 接收器；负责数据采集、质量检测、标定与保存原始文件 | 负责创建实验工作流、验证移交清单、建立独立数据集、训练 ESN、计算评估指标 |
| 接收工作流事件，并在 GUI 展示当前录制及诊断 | 生成持久化事件日志、实验 ID 和模型；显示训练指标与基线 |
| 发生故障时可在 E-Brush 本地 GUI 紧急终止录制 | 可取消等待中的工作流或训练任务；重新启动后标记未完成的工作流为 `interrupted` |

## 2. 首次安装与启动

以下命令在 **Windows PowerShell** 中使用。请先将两个 ZIP 解压到固定目录，示例以 `D:\Work\EBrush-Studio-v1.3-Agent-Workflow` 与 `D:\Work\RCStudio-v0.5-Agent-Workflow` 表示。实际目录以你的解压位置为准。

**E-Brush：**

```powershell
cd 'D:\Work\EBrush-Studio-v1.3-Agent-Workflow'
powershell -ExecutionPolicy Bypass -File .\SETUP_STUDIO.ps1
# 设备接入真实 COM7 时，启用采集与 Agent 服务；按实际端口修改 COM7。
powershell -ExecutionPolicy Bypass -File .\START_AGENT_LIVE.ps1 -BrushPort COM7 -CameraBackend none
```

E-Brush 启动后，在 `runtime\agent_credentials.json` 自动生成只用于本机的读写令牌。请保护该文件；包含设备控制权限的 write_token 应仅授予可信本机软件。

**RC Studio：** 另开一个 PowerShell 窗口，并执行：

```powershell
cd 'D:\Work\RCStudio-v0.5-Agent-Workflow'
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_agent_gui.ps1 -EBrushRoot 'D:\Work\EBrush-Studio-v1.3-Agent-Workflow'
```

此启动脚本会把 E-Brush 的令牌文件路径传给 RC 子进程，仅 RC 在调用采集 API 时读取授权文件。两个服务分别绑定回环地址，保持原有端口分工。**如果此前启动了旧版 RC 服务，请先结束旧版服务再启动 V0.5**；RC 客户端会检查服务版本，防止连接到缺少工作流接口的旧进程。

没有硬件时，可以用 E-Brush 的 `START_UI_PREVIEW.ps1` 学习界面；该预览关闭了电子毛笔连接。体验完整训练流程可使用下文离线模式。

## 3. 新手图形操作

1. 在 E-Brush 查看“采集质量”，确认帧率与数据状态稳定；进入“Agent 工作流”页面查看录制状态和时间线。
2. 在 RC Studio 打开“Agent 自动化流程”标签，选择“实时采集 · E-Brush 设备”。设置独立录制数量（至少 3）与每次录制秒数（建议先用 12 秒），点击“创建实验工作流”。
3. 当 RC 显示 `waiting_operator` 时，操作员准备好当前握笔动作，再点击“确认设备就绪 · 开始采集”。每份录制都需要新的确认。
4. E-Brush 显示录制帧数；在记录结束时自动关闭原始日志并生成交接清单。RC 将提示下一次采集，直到收齐独立记录。
5. RC 自动校验文件摘要、导入有效记录并提交 ESN。GUI 的步骤表显示 `importing`、`training` 和 `evaluation`，最后显示实验 ID、测试 RMSE、持续性基线 RMSE 等指标。
6. 通过 `workflow_id` 将设备事件、移交清单、训练 Run 与指标关联起来，以便复现和审计。

### 离线模式

在 E-Brush 对至少三份独立 `.brlog` 使用“RC Studio 数据交接”生成清单。在 RC 的 Agent 标签选择“离线处理 · 已有移交清单”，每行粘贴一份 `.handoff.json` 的绝对路径，然后创建工作流。程序会核对文件大小、SHA-256 与独立源文件数量。

## 4. 命令行与外部 LLM Agent

```powershell
$RC = '.\.venv\Scripts\python.exe'
& $RC -m rcstudio workflow create --config examples\agent-workflow-live.json
& $RC -m rcstudio workflow list
& $RC -m rcstudio workflow show <workflow_id>
# 工作流提示 waiting_operator 且操作员确认就绪时执行
& $RC -m rcstudio workflow approve <workflow_id>
& $RC -m rcstudio workflow cancel <workflow_id>
```

使用已有 `.handoff.json` 时，可先修改 `examples\agent-workflow-offline.example.json` 中的路径，再执行 `workflow create --config ...`。

MCP 外部 LLM 客户端可通过下面的命令连接 RC Studio：

```powershell
& $RC -m rcstudio mcp
# 用户显式允许提交/取消任务时启用写入工具
& $RC -m rcstudio mcp --allow-write
```

MCP 工具包括 `list_agent_workflows`、`get_agent_workflow`；启用写权限时还可使用 `create_agent_workflow`、`cancel_agent_workflow` 和 `import_ebrush_handoff`。实体采集的每次启动确认由 RC GUI 或本机交互式 CLI 提交，两者使用单独的 `operator_token`；LLM 的 MCP 写工具持有 `write_token`，只能创建与管理流程，无法代替现场操作员确认物理就绪。

### 主要 HTTP 接口

| 服务 | 读取 | 写入 |
|---|---|---|
| E-Brush 8765 | `/v1/health`、`/v1/state`、`/v1/acquisition/status`、`/v1/workflow/events` | `/v1/acquisition/start`、`/v1/acquisition/stop`、`/v1/acquisition/abort`、`/v1/workflow/event` |
| RC 8766 | `/v1/health`、`/v1/workflows`、`/v1/workflows/{id}`、`/v1/workflows/{id}/events` | `/v1/workflows`、`/v1/workflows/{id}/approve`、`/v1/workflows/{id}/cancel`、`/v1/projects/ebrush/import` |

状态查询需要只读或写入令牌；一般写入操作使用 write_token。实体采集确认需要 operator_token，且应由现场操作员通过 GUI 或本机 CLI 提交。`/v1/health` 公开最小服务身份信息，用于本机服务发现。RC Studio 的训练工作流会按原始文件级别检查三份独立数据，并将问题写入任务事件日志。

## 5. 失败恢复与安全操作

- 采集过程中检测到中断、低接收率、明显帧缺口或数据不足时，E-Brush 会保留诊断日志，并标记该轮采集失败，方便操作员调整后重新执行。
- RC GUI 的“取消工作流”会请求停止正在执行的步骤；如录制仍在继续，E-Brush GUI 的“紧急终止当前录制”可直接关闭本地记录。
- RC 服务重启会将此前运行中的工作流标记为 `interrupted`。先在 E-Brush 检查录制状态，再创建新任务；已完整导出的清单可以用于离线模式。
- 若文件路径已移动，重新从 E-Brush 生成移交清单。RC 依照清单中的实际绝对路径与文件摘要校验。
- 当前研究目标为未来约 50 ms 的角速度模长预测。RMSE 和持续性基线为相同测试集下的研究指标；更复杂的笔尖轨迹问题需要独立的标注与评估设计。

## 6. 本地验收范围

已执行自动化 HTTP 联调：模拟笔端解析器连接 E-Brush 采集 API，验证三次录制批准、BRLOG 文件、交接清单、RC 导入、ESN 训练与镜像事件。离线训练、访问权限、重复源文件拦截和取消流程也已自动化验证。原生 PySide6 的 Windows 高 DPI 显示、真实 COM 设备及第三方 MCP 客户端需要目标机器实机验收。
