# Ark Desktop Pet

明日方舟人物桌宠项目，基于 Electron、Vue 3、TypeScript 和 Vite。

当前版本：`0.6.2`。版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。

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

`src/assets` 已纳入版本管理，模型随仓库提供，克隆后即可直接切换。

## 设置页

在人物上点击右键，选择“设置”，会打开一个**独立的设置窗口**：它与桌宠是两个分开的窗口，拖动它不会带动桌宠，桌宠的尺寸与位置也不受它影响。在设置窗口里可以：

- 看到**当前模型预览**（实时播放待机动画）与当前人物的基本信息：角色名、英文名、皮肤、皮肤组、类型、样式、稀有度、资源文件名。
- 在“更换人物”分区里按角色名筛选（支持中文，共 425 个角色），选中后确认：会切换到该角色的默认皮肤。
- 在“更换皮肤”分区里切换当前角色的皮肤（每个角色 1～8 套）。
- 在“偏好”分区里开关桌面活动气泡。

窗口标题栏是拖动区域（右侧 × 按钮不参与拖动，可直接点关闭）；窗口高度按屏幕工作区自适应，**“确认更换”固定在窗口底部**，模型列表再长也只在列表区滚动，不会把确认按钮顶出可视区。

两个更换分区确认后都会自动修改 [AmiyaModel.vue](src/components/AmiyaModel.vue) 顶部的三行资源路径，并重新加载**桌宠窗口与设置窗口**（导入路径是编译期常量，必须整页重载才能生效；重载后设置窗口的预览与信息会同步到新模型）。

再次选择“设置”时会复用已打开的设置窗口并刷新其中的数据；关闭设置窗口不影响桌宠，关闭桌宠则会退出整个应用。

人物信息取自 `src/assets/models_data.json`：应用按骨架资源名（忽略大小写、忽略 `#` 等字符的替换差异）反查该索引；索引缺失时只显示目录名等本地信息，不影响切换。

如果需要手动修改，直接将三行路径中的目录与文件名改为目标模型后，重启开发服务即可：

```ts
import skeletonUrl from '../assets/角色/皮肤/模型.skel?url'
import atlasUrl from '../assets/角色/皮肤/模型.atlas?url'
import textureUrl from '../assets/角色/皮肤/模型.png?url'
```

三行路径必须来自同一个模型目录，并且文件名要完全对应。

## 桌面活动气泡

桌宠会感知当前正在使用的前台应用，并在应用切换时在人物旁边弹出一条聊天气泡，约 8 秒后自动收起。

- 只读取前台**进程名**，不读取窗口标题；类别与进程名的对应关系见 [activity.ts](electron/activity.ts) 里的 `categoryDefinitions`。
- 可在「设置 → 偏好」里开启或关闭该功能；关闭时不再检测前台应用（检测用的进程也会结束）。该偏好保存在 `userData/settings.json`，重启后依然生效。
- 轮询间隔为 `activityPollIntervalMs`（默认 3 秒），因此切换应用后气泡最多晚 3 秒出现。
- 显示气泡时窗口会临时横向加宽，**桌宠在屏幕上的位置不会移动**；右侧空间不足时气泡自动出现在人物左侧。
- 气泡是通用消息面板：主进程任意位置调用 `pushBubble({ source, icon, title, detail })` 即可推送，活动检测只是当前唯一的生产者，便于后续扩展提醒、对话等功能。

