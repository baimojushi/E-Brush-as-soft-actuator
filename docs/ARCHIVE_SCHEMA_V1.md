# BRUSH 过程数据归档 Schema v1

| 项 | 值 |
|---|---|
| schema_version | `1.0.0` |
| 状态 | Draft（待 M0 评审冻结） |
| 对应 Plane 任务 | EBSA seq=42「定义归档格式 schema v1」 |
| 依据 | 纲领 §10、§10.2；通道契约 `CHOP_CHANNEL_CONTRACT.md`（firmware v1.1.0） |
| 接口参考实现 | `host/brush_serial.py`（`_decode_data` 为原始帧字段权威定义） |

---

## 1. 设计目标与原则

本 schema 定义**脱离 TouchDesigner、厂商 SDK 与任何模型内部变量之后仍可完整解析**的归档层格式，是本周期数据采集的长期保存契约。

原则：

1. **纯语义层**：所有字段以物理量、单位、时间戳与字符串枚举表达；不出现 TD operator 路径、CHOP 通道对象、Python 对象引用、固件寄存器名。固件 v1.1.0 的 17 通道 CHOP 契约是本 schema 工程值列的子集镜像，但归档不依赖 CHOP 存在。
2. **原始码优先**：每帧同时保存原始计数列与工程量列。工程量全部可由原始码 + 标定常数重算；标定常数版本记录在 `episode.json`，不混入逐帧列。
3. **episode（片段）为基本单位**：采集、切分、标注、存档均以 episode 为一个目录。帧只是 episode 内部元素。（时间窗口与最小证据单位的最终口径由 seq=50 冻结；本 schema 以 segments 字段预留分层，不因 seq=50 未定而阻塞。）
4. **三重时间锚**：MCU 单调时钟（µs）、宿主 UTC（ns）、二者同步状态与残差并列保存；任何下游可仅凭时间列重建对齐。
5. **版本与可追溯**：固件版本、标定 id、换算常数版本、设备序列号、转换工具版本必须落盘；缺一项即视为元数据不完整（质量字段会标注）。
6. **授权先行**（§10.1）：每 episode 必须携带 `license_mode`，与数据授权协议四模式对齐；未选择前默认 `self_only`。

## 2. 目录与命名

```
<dataset_root>/
  dataset.json                      # 数据集级清单（见 §7）
  <site_id>/                        # 场地：实验室/展厅等物理环境标识
    <writer_id>/                    # 书写者：匿名化 ID（见 §5.1）
      <episode_id>/                 # 单个片段目录
        episode.json                # 元数据（必有，本文 §3–§6）
        signal.csv                  # 笔端逐帧表（必有）
        gt_pose.csv                 # 真值逐帧表（可选：启用深度相机真值链时必写）
        events.csv                  # episode 内离散事件（可选）
```

- `episode_id`：`{UTC起始时间 YYYYMMDDTHHMMSSZ}-{writer内4位序号}`，如 `20260915T013000Z-0007`。全局唯一性由路径 `site/writer/episode_id` 保证。
- 文件一律 UTF-8、LF 换行；CSV 含表头；缺失数值用**空单元格**（不用 `NaN`/`-9999` 哨兵值）。

## 3. `episode.json` — 标识与设备版本

```json
{
  "schema_version": "1.0.0",
  "episode_id": "20260915T013000Z-0007",
  "site_id": "lab-desk-a",
  "writer_id": "W-0007",
  "created_utc": "2026-09-15T01:31:42Z",

  "device": {
    "brush_sn": "BRUSH-2026-0031",
    "firmware_version": "1.1.0",
    "chop_contract_version": "v1.1.0",
    "hall_variant": 1,
    "hall_lsb_per_mT": 0.25,
    "imu_model": "IMU660RB",
    "conversion_constants_version": "host-v1"
  },

  "calibration": {
    "calibration_id": 41,
    "pose_zero_corr_qwqxqyqz": [1.0, 0.0, 0.0, 0.0],
    "pose_zero_captured_utc": null,
    "hall_center_extrinsics_id": null,
    "note": "calibration_id 取自固件 DATA 帧；pose_zero_* 为显示标定；hall_center_extrinsics 待 seq=58 产出后以 id 引用其标定档案"
  }
}
```

字段规则：

- `device.hall_variant`：`1|2|0`（0=未知，此时 `bx/by/bz_mT` 列为空）。
- `device.hall_lsb_per_mT`：本次采集使用的换算常数快照（便于脱离换算表版本复核）。
- `calibration.calibration_id`：逐帧也写入 CSV（可检测采集内漂移）。
- `pose_zero_corr_qwqxqyqz`：Capture Zero 产生的笔杆→世界系校正四元数（`w,x,y,z`，单位四元数=未标定）；`pose_zero_captured_utc` 为空表示未标定。此字段语义独立于 TD pose_mapper 实现，仅描述物理约定：**MCU +X 轴 = 笔杆轴；笔尖竖直向下时 ax≈+9.81 m/s², ay≈0, az≈0；纸面系 +X 纸右、+Y 纸前、+Z 纸法线**。

## 4. `episode.json` — 时间基准

```json
{
  "timebase": {
    "mcu_clock": "monotonic_us_since_boot",
    "host_clock": "posix_ns_wall",
    "clock_disciplined_at_start": true,
    "sync_rtt_ms_median": 1.8,
    "sync_rtt_ms_p95": 3.1,
    "offset_ns_host_minus_mcu": 1758241234000000000,
    "offset_uncertainty_ns": 1500000,
    "episode_t0_host_ns": 1758241234012345678,
    "nominal_stream_hz": 100
  }
}
```

- `offset_ns_host_minus_mcu`：episode 起止两次时钟握手的中值（MCU→host 映射参数快照）。
- `offset_uncertainty_ns`：RTT/2 上界估计。真值对齐、事件排序的不确定度引用该值。
- 逐帧 `sample_host_ns` 若由同步映射生成则保留映射原值；质量列 `clock_synced=0` 的帧，其 host 时间退化为接收时刻（帧到达抖动计入质量，不改写时间列）。

## 5. `episode.json` — 条件元数据与质量

### 5.1 条件元数据（本周期实验设计所需的最小集）

```json
{
  "conditions": {
    "task_kind": "dynamic_layout_repeated_motion",
    "char_id": "永",
    "stroke_pattern": "hen-gna-horizontal-hu",
    "writer_anon_level": "pseudonymous",
    "brush": {"model": "e-Brush-v3", "tip_mm": 2.0, "shaft_mm": 10.0},
    "surface": {"board_steel": true, "steel_plate_id": "SP-02", "deformation_state": "as_installed", "felt": false},
    "magnet_config": {"count": 6, "layout_id": "hex-ring-60mm", "magnetization": "N45-axial"},
    "layout": {"board_pose_id": "deskA-pose3", "height_cm": 74.0},
    "repetition_index": 3,
    "repetition_group": "rg-20260915-a1",
    "instructions": ["write_char:永", "speed:normal", "repeat:3"],
    "session_id": "sess-20260915-am"
  }
}
```

- `writer_anon_level`：`anonymous|pseudonymous|identified`——配合 §10.1 授权协议；`identified` 时真实身份只存于单独的授权档案，不进入数据集。
- `repetition_group`：同一动作跨布局重复采集（seq=68）用同组 id 关联；组级统计不进 episode。
- `instructions`：字符串枚举列表，自由文本仅允许出现在 `note`。
- `magnet_config.count` ∈ {4,6,8,…} 支撑磁铁数量消融（seq=64）；`layout_id`/`board_pose_id` 支撑布局空间采样与未见布局基准（seq=67/71）。

### 5.2 质量汇总与授权

```json
{
  "quality": {
    "frames_written": 2140,
    "frame_id_gaps": 2,
    "host_queue_drops": 0,
    "frames_with_drop_flags": {"sched_overrun": 1, "hall_stale": 1, "imu_stale": 0, "hall_read_fail": 0, "imu_read_fail": 0, "tx_backpressure": 0},
    "frames_clock_unsynced": 37,
    "frames_quat_stale": 0,
    "duration_s": 21.4,
    "verdict": "ok",
    "excluded_reason": null,
    "excluded_by": null
  },
  "license": {
    "mode": "self_only",
    "agreement_id": "AGR-W-0007-001",
    "consent_captured_utc": "2026-09-14T22:10:00Z"
  }
}
```

- `license.mode` ∈ `self_only | timed_public | paid_public | free_forever`（对齐 §10.1 四模式；协议正文由 seq=41 交付）。**撤回只作用于尚未分发副本，因此 `mode` 允许被后续 episode 或数据集清单更新，但本文件为采集时刻快照，不回改**；权威当前态在 `dataset.json` 与授权档案。
- `verdict` ∈ `ok | suspect | excluded`；`excluded_by` 记录质量门控（M4/seq=57）的执行者与规则版本。episode 一旦 `excluded` 不删除、不重写，仅由下游按 `verdict` 过滤——保证可审计。
- 完整性字段（`frames_written` 与 `frame_id` 连续性、`duration_s`）在此聚合，逐帧证据在 CSV。

## 6. 逐帧 CSV 列定义

### 6.1 `signal.csv`（笔端，必有；一行一帧）

| 列 | 类型 | 单位/语义 | 空值语义 |
|---|---|---|---|
| `frame_id` | uint32 | 固件递增帧号（回绕取模） | — |
| `calibration_id` | uint32 | 固件标定 id | — |
| `sample_time_us` | uint64 | MCU 单调时钟 µs（采样意图时刻） | — |
| `hall_time_us` | uint64 | MCU Hall 采样时刻 µs | Hall 缺样时空 |
| `imu_time_us` | uint64 | MCU IMU 采样时刻 µs | IMU 缺样时空 |
| `sample_host_ns` | uint64 | 宿主时间 ns（同步映射或退化为接收时刻） | — |
| `hall_host_ns` | uint64 | 同上（Hall） | Hall 缺样时空 |
| `imu_host_ns` | uint64 | 同上（IMU） | IMU 缺样时空 |
| `host_rx_ns` | uint64 | 宿主串口收包时刻 ns | — |
| `clock_synced` | 0/1 | 该帧 host 时间是否来自同步映射 | — |
| `sync_rtt_ms` | float | 最近一次握手 RTT ms | 未同步时空 |
| `device_status` | uint16 | 位掩码原始值 | — |
| `drop_flags` | uint8 | 位掩码原始值 | — |
| `hall_x_raw` `hall_y_raw` `hall_z_raw` `hall_temp_raw` | int16 | Hall 原始码 | — |
| `hall_conv_status` `hall_device_status` `imu_status` | uint8 | 传感器状态原始字节 | — |
| `imu_temp_raw` `gyro_x_raw` `gyro_y_raw` `gyro_z_raw` `accel_x_raw` `accel_y_raw` `accel_z_raw` | int16 | IMU 原始码 | — |
| `bx_mT` `by_mT` `bz_mT` | float32 | 磁场 B（mT，body 系） | hall_variant 未知时空 |
| `hall_temp_C` `imu_temp_C` | float32 | 温度 °C | — |
| `gx_rad_s` `gy_rad_s` `gz_rad_s` | float32 | 角速度 rad/s（body 系） | — |
| `ax_m_s2` `ay_m_s2` `az_m_s2` | float32 | 加速度 m/s² **含重力**（body 系） | — |
| `q_w` `q_x` `q_y` `q_z` | float32 | 姿态四元数（6轴滤波，body→world，world 由固件约定） | 滤波无效时空（见 `pose_valid` 位） |

约束：
- 列顺序即上表；新增列只允许追加在表尾（次版本升级），不改名、不删除、不移位（主版本才允许破坏性变更）。
- 工程值可由原始值列重算；校验工具比对偏差 >1e-4 时报 `conversion_drift`。

### 6.2 `gt_pose.csv`（真值链，可选；一行一真值样本）

| 列 | 类型 | 单位/语义 |
|---|---|---|
| `gt_t_host_ns` | uint64 | 真值时间戳（宿主 UTC ns，与 `signal.csv` 同基准） |
| `x_mm` `y_mm` `z_mm` | float32 | 笔尖/笔中段在世界（纸面）系位置 mm；定义：笔**尖**为原点语义 |
| `qw qx qy qz` | float32 | 笔杆姿态四元数（世界系，可选提供；无则空） |
| `sigma_pos_mm` | float32 | 该帧位置 1σ 不确定度（由 seq=53 标定流程给出，未标定时空） |
| `src` | string 枚举 | `hp60c_track | hand_guide | none`（真值来源，设备无关语义） |
| `uncalibrated` | 0/1 | 相机↔笔面外参未标定为 1（seq=54 完成后应为 0） |

- 真值与笔端流**不做预对齐、不插值进 signal.csv**；对齐是消费端职责（用各自 ns 时间戳与不确定度窗口）。归档只保证同基准、可解析、带不确定度。
- 深度相机设备的帧格式、SDK 对象一律不出现在此层。

### 6.3 `events.csv`（可选）

`event_t_host_ns, name, arg` 三列。`name` ∈ `segment_start | segment_end | instruction | capture_zero | sync_reacquired | note`。`segment_*` 支撑 seq=50 冻结后的窗口切分回填；`capture_zero` 与 episode.json 标定字段互相印证。

## 7. `dataset.json`（数据集级清单）

```json
{
  "schema_version": "1.0.0",
  "dataset_id": "ds-20260915-singlehall-baseline",
  "purpose": "single_hall_field_baseline_v1_freeze (seq=66)",
  "created_utc": "...", "updated_utc": "...",
  "license_current": {"mode_by_writer": {"W-0007": "self_only"}},
  "episodes": [
    {"path": "lab-desk-a/W-0007/20260915T013000Z-0007",
     "sha256_manifest": "…", "frames": 2140, "verdict": "ok"}
  ],
  "conversion": {"tool": "brush-serialize", "tool_version": "0.1.0"}
}
```

- `sha256_manifest`：episode 目录内三个（含可选两个）文件的内容哈希，写于 episode 关闭时；此后只追加校验、不改内容。
- `license_current` 为当前权威态（`episode.json.license` 为历史快照）；协议变更流程由 seq=41 文档定义。

## 8. 与 TouchDesigner 的关系（消费端示例，非依赖）

TD `serial_bridge`（Script CHOP）可在演示/预演中作为**读取方**按本 schema 播放 episode（File In DAT + 表达式重建通道），但：
- 生产端 = `host/brush_serial.py` 直出 CSV/JSON，**不经 TD**；
- pose_mapper 的 `rx_deg/ry_deg/px_m` 等属于**运行时衍生通道**，不是归档字段；任何衍生量若要归档，必须在 episode.json 声明计算定义后另存衍生列，原始列不替换。

## 9. 最小可解析示例与验收

`archive_examples/`（同目录）附：`episode.json`（2 帧迷你 episode）+ `signal.csv`（2 行）+ `verify_schema_v1.py`（仅 Python 标准库的解析与一致性检查）。验证脚本可证明：不依赖 numpy/pandas/TD/SDK 即可完整读出原始值、工程量、时间基准与质量字段。

## 10. 本 schema 明确不做的事

- 不冻结时间窗口/最小证据单位（seq=50 职责，未定不阻塞 v1：episode 容器与 segments 字段已兼容任意窗口粒度）。
- 不定教学权重与势合度度量（seq=74 职责）。
- 不存原始串口字节流（`.brlog` 类字节日志可在 `episode.json.raw_ref` 可选外链，默认不启用）。
- 不做 BOM/GIS/压缩专有格式强绑定；归档正文即文本容器，Parquet/NPZ 仅作为性能层衍生副本。

## 11. 版本规则

`schema_version` 遵循 semver：列名/列序/单位/枚举语义变更 = major；新增可选列/字段 = minor；文档澄清 = patch。读取方遇 minor 升级按缺列空值兼容；遇 major 拒绝并要求迁移工具。
