# Ark Desktop Pet

明日方舟人物桌宠项目，基于 Electron、Vue 3、TypeScript 和 Vite。

当前版本：`0.3.1`。版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。

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
- `public/`：静态资源
- `electron-builder.json5`：Windows 打包配置

## 构建

```powershell
npm run build
```

## 获取模型

在桌宠人物上点击右键，选择“获取模型”。输入模型文件夹名称，例如 `amiya`，应用会从 Ark-Models 仓库中列出匹配的模型文件夹。

选择模型后点击“下载到 assets”，文件会保存到 `src/assets`。保存目录会使用搜索名称；如果目录已存在，会自动使用 `#2`、`#3` 等后缀避免覆盖已有文件。

## 更换当前人物

在人物上点击右键，选择“更换人物”，选中本地模型后点击“确认更换”。应用会自动修改 [AmiyaModel.vue](src/components/AmiyaModel.vue) 顶部的三行资源路径，并重新加载窗口。

如果需要手动修改，直接将三行路径中的文件夹名和文件名改为目标模型后，重启开发服务即可：

```ts
import skeletonUrl from '../assets/目标文件夹/目标模型.skel?url'
import atlasUrl from '../assets/目标文件夹/目标模型.atlas?url'
import textureUrl from '../assets/目标文件夹/目标模型.png?url'
```

三行路径必须来自同一个模型文件夹，并且文件名要完全对应。由于 `src/assets` 已被 Git 忽略，新下载的模型只会保存在本地，不会被提交到仓库。
