# E-Brush Studio V1.3 · Agent 设备执行层

本次新增：

1. 基于原有 BrushSource 与同一串口接收器的独立录制会话，支持开始、查看、停止和故障终止。
2. 将数据完整性与采样质量检测纳入移交门控，生成具有 SHA-256 摘要的 `ebrush.handoff.v1` 清单。
3. 本机 `127.0.0.1:8765` 双权限 Token API，支持 RC 工作流事件镜像。
4. Material 风格“Agent 工作流”界面、实时进度、操作日志与本地紧急终止按钮。
5. 新增的按钮与参数区域接入 F1、鼠标悬停帮助库；原有新手教程保留。
6. `START_AGENT_LIVE.ps1` 用于 Windows 真实串口与 Agent 工作流启动。

接口、运行方法、安全确认和验收请参阅 [双 Studio 快速入门](docs/AGENT_WORKFLOW_QUICKSTART_CN.md)。

验证：模拟笔端与 RC Studio HTTP 端到端流程及 E-Brush 自动化测试已通过；目标机器需验证真实串口、接收帧率、Qt 原生窗口、硬件断连恢复。
