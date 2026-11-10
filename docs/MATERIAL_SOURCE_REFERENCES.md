# UI/UX 设计依据与实现边界

核对日期：2026-11-10。以下列出官方公开源代码与设计文档，供桌面交互设计参考。

## 官方参考

1. AndroidX Compose Material 3：`Tooltip.kt`
   https://raw.githubusercontent.com/androidx/androidx/androidx-main/compose/material3/material3/src/commonMain/kotlin/androidx/compose/material3/Tooltip.kt
   参考富提示的标题、正文、操作区，以及锚点定位、鼠标/键盘触发和点击外部关闭等交互结构。
2. Google Material Web：`docs/theming/color.md`
   https://github.com/material-components/material-web/blob/main/docs/theming/color.md
   使用 primary/on-primary、surface/on-surface、outline 等语义色角色，让不同控件共享清晰一致的配色体系。
3. Qt Widgets：`QToolTip`
   https://doc.qt.io/qt-6/qtooltip.html
   参考 QWidget 工具提示、帮助事件及控件/视图的帮助入口；项目另用 Qt 富文本与独立窗口实现可交互长说明。

## 本项目的设计选择

本项目以 PySide6 原生控件适配 Material 设计原则；实现和维护均由本项目负责。
参考实现的结构与交互思路，不宣称逐像素复刻 Material 原生组件。

悬停卡片采用 16 px 圆角、语义配色、默认 650 ms 停留延迟和可滚动全文。
长篇解释本身超出了常见短工具提示的使用方式，因此另外提供“固定并深入了解”和独立学习中心，
避免用户必须始终把鼠标停在某个精确位置。卡片默认不主动激活窗口；不使用浮动动画。

默认字号 14 px，可在 12–20 px 间调整；延迟可在 300–2000 ms 间调整。
这些数值是本项目的桌面适配选择，不冒称 Material 官方强制规定。
跟随深浅主题，提供键盘焦点触发、F1、可见焦点边框、可读文本和可复制的完整说明。
高亮只画轮廓，鼠标事件穿透，不以透明遮罩替代真实按钮。

官方库持续更新；如需复现实验或精确比对，请固定对应提交版本。
工程中保留原项目许可证和版权文件；没有把第三方组件代码全文复制入项目。
