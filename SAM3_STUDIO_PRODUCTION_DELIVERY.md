# E-Brush Studio SAM3 6015 生产数据回路 — 源码交付记录

日期：2026-10-23

## 本次实际完成

- 将旧的 `studio/sam3_client.py` 缺少有效唤醒/无界片段缓冲/缺少帧索引约束的路径替换为异步最新关键帧客户端，面向 6015 的 `mode=single` 接口；全对象掩码、正确原帧归属、最近邻逆映射和可视化。
- 在 Studio 增加完整视频生产作业管理器：本地源视频保留、原帧依次编码成 <=400 帧的小段、批量 HTTP 上传、远端 ZIP 元数据与每帧多掩码校验、每段原子保存和 SHA256、失败续跑。
- 扩展 Studio 主界面：全视频上传、队列暂停恢复、失败重试、进度查看、任一帧掩码预览及恢复实时画面。
- 扩展 Agent 状态/控制/下载接口，支持原视频和掩码包独立下发、Range 断点下载，限制无认证控制接口仅监听 loopback。
- 恢复原生视频帧与原相机帧身份合同：可选 `<视频文件>.frames.jsonl`，不虚构 HP60C 设备时间戳；未知深度配准与三维标注状态一律为 CANDIDATE。
- 统一单 GPU 服务访问：同一个 HTTP transport 顺序提交 live single 与完整视频 batch 分块，不并发打满单实例 SAM3 服务。
- 新增 Agent CLI 和详细运维操作/故障修复文档。

## 可用状态边界

- **源码完成 / 模拟 HTTP 端到端：PASS**：`pytest -q studio/tests/test_sam3_production.py` = 10 passed；`python -m compileall -q studio` = PASS；`python tests/test_python.py` = PASS。
- **Molink-Infer 6015 实际返回协议/生产连通：UNVERIFIED**，本执行环境无法进入实际服务器。
- **PySide6 可视 UI：UNVERIFIED**，本执行环境未安装 PySide6，未能执行界面端点的可视回归。
- **HP60C 硬件、原始 RGB-D 无丢帧、2D→3D：UNVERIFIED/BLOCKED**，没有直接接入硬件，Studio 的 `HP60CSource.poll()` 本来就是最新帧视图而非无损 SDK 回调队列。
- **全帧 20 FPS SAM3 实时分割：NOT CLAIMED**。在线可返回实时的、实际处理的关键帧全部对象掩码；完整视频所有帧可通过独立视频批量作业完成，不能将二者的吞吐混为一谈。
- **仓库既有 firmware mock 编译：FAIL（独立于本次 Studio 变更）**：`sh tests/run_local_tests.sh` 在 `src/c3_node.cpp` 上因 `tests/native_stubs/Arduino.h` 等缺少 `INPUT_PULLUP/OUTPUT_OPEN_DRAIN/digitalRead` 与 NimBLE/Serial 桩 API 而未通过；本轮未修改固件与桩代码。

操作、Agent 接口、所有输出清单、恢复路径和最小真实业务确认：参见 `docs/SAM3_STUDIO_PRODUCTION.md`。
