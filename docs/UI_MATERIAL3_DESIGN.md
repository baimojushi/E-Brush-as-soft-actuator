# E-Brush Studio UI Iteration — Material 3

## Design basis

The desktop UI adopts the role-based theming approach used by Google Material 3 and the open-source [`material-components/material-web`](https://github.com/material-components/material-web) project. The web components themselves are not embedded in this PySide6 application; their design-token model is translated into Qt palette roles and QSS component rules. The Material Web repository currently describes itself as being in maintenance mode, so this project deliberately avoids a runtime dependency on it.

Reference material: [Material Web introduction](https://github.com/material-components/material-web/blob/main/docs/intro.md), [theming tokens](https://github.com/material-components/material-web/blob/main/docs/theming/README.md), and [color roles](https://github.com/material-components/material-web/blob/main/docs/theming/color.md).

## Visual system

- **Brand seed:** deep teal, selected to complement sensor instrumentation and the brush workflow.
- **Color roles:** primary, primary-container, secondary, tertiary, surface-container levels, on-surface, outline, error, success and warning.
- **Shape:** 10–20 px rounded controls and surfaces, with consistent pill-shaped status/filter chips.
- **Typography:** Segoe UI Variable / Segoe UI on Windows, with Microsoft YaHei UI fallback.
- **Elevation and hierarchy:** app bar, dock surfaces, workspace toolbar, filter chips and view cards are separated by tonal surface changes and subtle outlines rather than heavy shadows.
- **Themes:** light and dark palettes are available and the selected mode is persisted in QSettings.

## Workspace structure

1. **App bar:** product identity, live brush/camera/SAM3 status chips and a light/dark theme toggle.
2. **Workspace toolbar:** responsive layout mode, common view presets, dock visibility and reset controls.
3. **View selector:** checkable Material-style filter chips for the eight existing view panels.
4. **View canvas:** responsive card grid or focused tabs. The original camera, OpenGL and calibration widgets remain persistent during reflow.
5. **Side docks:** device/vision controls on the right and coordinate-mapping tools on the left.
6. **Status bar:** local-workspace context and data-retention hint.

## Interaction and behavior

- Device state is shown in compact status chips with neutral, success, warning and error states.
- The view chips continue to control the same existing panels; no capture, calibration or pose-mapping command has been replaced.
- Grid, two-column, single-column and tab-focus layouts are retained.
- Theme changes update Qt widgets, PyQtGraph plots and the 3D scene.
- No new runtime dependency or network asset is required for the UI theme.

## Implementation files

- `studio/material_theme.py` — Material 3 tonal tokens, Qt palette and global component styles.
- `studio/main.py` — app bar, device status chips, theme persistence and plot theme propagation.
- `studio/responsive_workspace.py` — toolbar, filter chips and persistent responsive view cards.
- `studio/scene3d.py` — theme-aware OpenGL viewport and orientation overlay.
- `studio/pose_calibration_ui.py`, `studio/hall_extrinsic_ui.py`, `studio/hall_panel.py` — semantic component styling.

## Runtime note

The source archive documents PySide6 and PyQtGraph as runtime dependencies. This build environment does not have those packages installed, so the iteration is syntax-checked and source-reviewed here; final native-window, DPI and camera/OpenGL validation must be performed on the Windows workstation.
