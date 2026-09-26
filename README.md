# Ark Desktop Pet

明日方舟人物桌宠项目，基于 Electron、Vue 3、TypeScript 和 Vite。

当前版本：`0.4.0`。版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。

## 开发

```powershell
npm install
npm run dev
```

如果 Electron 下载较慢，可以在 Windows PowerShell 中使用镜像：

```powershell
$env:ELECTRON_MIRROR='https://npmmirror.com/mirrors/electron/'
npm install
```

## 目录

- `electron/`：Electron 主进程和 preload 通信
- `src/`：桌宠界面、交互和样式
- `src/assets/<角色>/<皮肤>/`：本地模型库（已被 Git 忽略）
- `public/`：静态资源
- `electron-builder.json5`：Windows 打包配置

## 构建

```powershell
npm run build
```

## 本地模型库

模型来自 [Ark-Models](https://github.com/isHarryh/Ark-Models) 的 `models/` 目录，按 [models_data.json](src/assets/models_data.json) 的映射整理成两级目录：

```
src/assets/<角色名>/<皮肤名>/<模型文件>
src/assets/阿米娅/默认服装/build_char_002_amiya.skel | .atlas | .png
src/assets/斯卡蒂/珊瑚海岸_III/build_char_263_skadi_summer_3.skel | .atlas | .png
```

命名规则：

- 角色目录取 `models_data.json` 中该条目的 `name`，皮肤目录取 `skinGroupName`。
- 文件名沿用 `assetList` 中的资源名（`assetId`），但会把 `\ / : * ? " < > | # %` 等文件系统与 URL 不安全字符替换为 `_`（例如 `build_char_263_skadi_summer#3.png` → `build_char_263_skadi_summer_3.png`）。
- 同一角色内皮肤重名时追加 `_2`、`_3` 后缀；骨架缺扩展名的文件按内容补全为 `.skel` 或 `.json`。

`src/assets` 已被 Git 忽略，模型只保留在本地，不会提交到仓库。

## 更换当前人物

在人物上点击右键，选择“更换人物”，可用筛选框按角色或皮肤名检索（支持中文），选中后点击“确认更换”。应用会自动修改 [AmiyaModel.vue](src/components/AmiyaModel.vue) 顶部的三行资源路径，并重新加载窗口。

如果需要手动修改，直接将三行路径中的目录与文件名改为目标模型后，重启开发服务即可：

```ts
import skeletonUrl from '../assets/角色/皮肤/模型.skel?url'
import atlasUrl from '../assets/角色/皮肤/模型.atlas?url'
import textureUrl from '../assets/角色/皮肤/模型.png?url'
```

三行路径必须来自同一个模型目录，并且文件名要完全对应。

