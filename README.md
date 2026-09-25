# Ark Desktop Pet

明日方舟人物桌宠项目，基于 Electron、Vue 3、TypeScript 和 Vite。

当前版本：`0.2.0`。版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。

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
