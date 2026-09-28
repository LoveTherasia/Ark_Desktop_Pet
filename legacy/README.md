# legacy —— 旧 Vue 3 + Electron + pixi-spine 实现（已弃用）

这里的代码是 0.6.x 及更早版本的桌面宠物实现，**已不再维护**，仅作存档、回滚与对照参考。

- 当前实现是仓库根目录的 Python 版，见 [../README.md](../README.md)
- 要运行这里的老版本，请切回 `main` 分支——本目录下的构建配置已不在原来的相对位置上：
  - `vite.config.ts`、`tsconfig.json`、`index.html`、`settings.html` 原本在仓库根目录
  - `electron-builder.json5` 原本在仓库根目录
  - 模型资源仍保留在仓库根的 `src/assets/`（Python 版与旧版共用）
- `AmiyaModel.vue` 里的 `../assets/...` 导入路径是相对 `src/components/` 的，因此在
  当前目录结构下也无法解析

## 目录

```
src/            Vue 界面（App.vue、SettingsApp.vue、components/、composables/、style.css）
electron/       Electron 主进程与 preload（activity.ts 含前台应用检测）
index.html      桌宠窗口入口
settings.html   设置窗口入口
vite.config.ts  Vite 配置
tsconfig*.json  TypeScript 配置
electron-builder.json5  Windows 打包配置
.vscode/        编辑器配置
```

如果需要一个可构建的旧版副本，最稳妥的做法是从 `main` 分支另建工作树，而不是在这里就地恢复：

```powershell
git worktree add ../ArkPet-legacy main
```
