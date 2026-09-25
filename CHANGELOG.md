# Changelog

## 0.1.0 - 2026-09-25

### Added

- 初始化 Electron + Vue 3 + TypeScript + Vite 桌宠工程。
- 接入阿米娅 Spine 3.8 模型资源（`.skel`、`.atlas`、`.png`）。
- 支持 `Sit`、`Interact`、`Move`、`Relax` 等模型动画。
- 支持点击桌宠触发互动动画。
- 支持透明、无边框、置顶的桌宠窗口。
- 支持通过顶部和底部区域拖动窗口。
- 增加模型自动缩放、居中和垂直偏移配置。
- 删除模型数据中所有 `type: "Enemy"` 的记录。

### Technical

- 使用 `pixi.js@6.5.10` 和 `pixi-spine@3.1.2` 渲染 Spine 3.8 模型。
- 使用 Pixi ticker 显式推进 Spine 动画。
- 增加 Electron renderer 浏览器兼容配置。
