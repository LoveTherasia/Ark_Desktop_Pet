# ArkPet 桌宠

明日方舟人物桌宠。**0.7.0 起改用 Python 实现**：Python + PySide6 + 官方 Spine 3.8 C Runtime。

当前版本：`0.7.1`（`py` 分支）。版本变更记录见 [CHANGELOG.md](CHANGELOG.md)。

> 旧的 Vue 3 + Electron + pixi-spine 实现已移入 [`legacy/`](legacy/) 目录存档，**不再维护**，
> 仅保留用于回滚与对照。下文描述的都是当前的 Python 实现。

## 为什么换成 Python

原先用 Electron + pixi.js（WebGL）渲染，运行时体积大、启动慢，且桌宠这类"常驻小工具"用
整个浏览器内核偏重。现在改为直接调用官方 Spine C Runtime，用 OpenGL 绘制：

- 不需要 Electron / Node 运行时
- Spine 解析与应用动画在 C 层完成，Python 只负责窗口与上传顶点
- 桌宠窗口与设置窗口共用同一套渲染代码（`spine_view.SpineGLView`）

## 环境要求

- Windows 10/11、Python 3.10 或更新（开发环境为 3.13.7）
- **GCC 与 G++** 在 `PATH` 上（用于编译 Spine C Runtime 与桥接层，例如 MinGW-w64）
- **Git** 在 `PATH` 上（首次构建会拉取 Spine Runtime 源码）
- 模型资源放在 `src/assets/<角色>/<皮肤>/`，同名 `.skel` + `.atlas` + `.png` 三件套

## 快速开始

```powershell
# 1) 建立虚拟环境并装依赖
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 2) 编译原生桥接（首次会 clone Spine Runtime 3.8 到 vendor/，需要几分钟）
.venv\Scripts\python build_runtime.py

# 3) 运行桌宠
.venv\Scripts\python pet.py
```

第 2 步只在首次或 `spine_bridge.cpp` 改动后需要执行；DLL 按源码哈希命名，改过就会自动重编。

## 目录结构

```
pet.py                 桌宠窗口、右键菜单、命令行入口
spine_view.py          Spine 封装 + 模型扫描 + 适配计算 + 可复用 GL 控件
settings_window.py     独立设置窗口
spine_bridge.cpp       C 桥接层（调 Spine C Runtime，导出顶点流与诊断接口）
build_runtime.py       拉取并编译 Spine C Runtime，链接出桥接 DLL
test_prototype.py      自动化测试
requirements.txt       PySide6-Essentials
src/assets/            模型资源（共享数据，随仓库提供）
legacy/                旧 Vue/Electron 实现（存档，不维护）
.build/  vendor/  .venv/   生成物与依赖，不入库
```

## 操作说明

- **左键拖动**移动窗口
- **右键**打开菜单：`设置` / `关闭桌宠`

## 设置窗口

右键 → `设置`。窗口结构对齐旧版 `SettingsPanel.vue`，配色也沿用（`#fffdf5` 面板、`#c47758` 强调色）：

```
SETTINGS / 设置                                              ×
┌──────────────────┐  当前人物
│   模型预览        │  艾雅法拉
│  （实时动画）      │  三丽鸥家族_II
└──────────────────┘  皮肤 / 图集 / 骨骼 / 类型 / 资源
当前模型预览

[更换人物] [更换皮肤] [偏好]
─────────────────────────────
 (可滚动的内容区)
─────────────────────────────
确认更换为                        [ 确认更换 ]
艾雅法拉 / 默认服装
```

- **更换人物**：424 个角色全部列在可滚动列表里，支持按名称搜索；选中后确认会切到该角色的
  「默认服装」。
- **更换皮肤**：当前角色的皮肤列表，同样可搜索；重名皮肤会用目录名区分。
- **偏好**：**人物大小**滑块（0.5×–2.0×），实时生效；另有「桌面活动气泡」占位说明。

模型切换是**热切换**：桌宠窗口与预览会即时换掉骨骼、图集和贴图，无需重启，旧实例会被释放。
标题栏可拖动窗口（等价于旧版的 `-webkit-app-region: drag`）。

## 调整大小

### 画布会自动贴合人物

桌宠窗口**默认按人物自动定尺寸**：窗口比人物实际占的像素大约 `PET_CANVAS_MARGIN_PX`
（默认 50 物理像素，四周均分即每边约 25px）。人物变了，画布跟着变：

| 触发 | 行为 |
| --- | --- |
| 启动 | 按当前动画的可见范围算窗口尺寸 |
| 切换动画 | 按新动画的可见范围重算（各动画胖瘦差别很大：Relax 286×472、Sleep 466×233） |
| 拖动「人物大小」滑块 / `--scale` | 人物与画布按同一比例一起变大，留白随之同比例增长 |
| 在设置窗口换模型 | 按新模型的包围盒重算 |

绘制比例由"当前窗口尺寸"反解得到（不动点迭代，见 `PetWindow.current_fit_scale`）：
要满足"窗口 = 人物像素 + 留白"，比例必须与窗口尺寸自洽，所以不能单独写死。
因为走的是 `fit_scale_override` 通道，窗口尺寸与绘制比例不会互相追着放大。

默认该模型下窗口从原来的 320×400 缩到约 **119×165 逻辑像素**（149×206 物理），
人物四周留白 26–36px，符合 50px 的目标。

### 相关常量

| 常量 | 位置 | 含义 |
| --- | --- | --- |
| `PET_CANVAS_MARGIN_PX` | `pet.py` | 窗口比人物大多少（**物理像素**，默认 50） |
| `PET_CANVAS_SAFETY` | `pet.py` | 安全系数（默认 1.15），吸收"人物中心与包围盒中心不一致"的偏差 |
| `PET_CANVAS_MAX_WIDTH` / `MAX_HEIGHT` | `pet.py` | 画布上限，避免大屏或高倍率下窗口过大 |
| `DEFAULT_WINDOW_WIDTH` / `DEFAULT_WINDOW_HEIGHT` | `pet.py` | 仅固定窗口模式（`--size`）下的默认值 |
| `DEFAULT_DISPLAY_SCALE` | `spine_view.py` | 人物大小基准（1.0） |
| `DISPLAY_SCALE_MIN` / `MAX` / `STEP` | `spine_view.py` | 大小滑块的范围与步进 |
| `SETTINGS_WINDOW_WIDTH` / `SETTINGS_WINDOW_HEIGHT` | `settings_window.py` | 设置窗口尺寸（默认 620×620） |

### 改人物大小的三种方式

1. **设置窗口** → 偏好 → 人物大小（实时，推荐；人物与画布同比放大）
2. **命令行**：
   ```powershell
   # 人物比默认大 30%，画布同比放大（留白也从 50 变成约 65）
   .venv\Scripts\python pet.py --scale 1.3
   # 想要固定的窗口尺寸而不是自动画布：
   .venv\Scripts\python pet.py --size 360x450
   ```
3. **改常量**：编辑上表中的值

注意取舍：**留白调大 = 透明区域变大**，会挡住更多桌面点击。想让人物大而不占地方，
调 `--scale`（人物与画布一起变大）而不是单独加 `PET_CANVAS_MARGIN_PX`。

### 已知限制

`PET_CANVAS_SAFETY = 1.15` 是为了兜住"人物实际中心与包围盒中心不一致"的偏差。
即便如此，`Sit` 与 `Sleep` 这两个动作的姿态比整段并集更靠外，底部仍可能压到窗口边缘
（各约 1 行像素）。这是"按整段并集定尺寸"的固有取舍；要完全避免只能改用逐姿态定尺寸，
代价是动画播放时窗口会不断变化。

### 命令行参数

```
--skeleton PATH     指定模型（.skel，需同名 .atlas/.png）
--fit idle|all      idle：只用待机动画定标（默认，人物更大）
                    all ：所有动画取并集（任何动作都不越界，但人物更小）
--size WxH          使用固定窗口尺寸（逻辑像素），关闭自动画布
--scale N           人物显示比例（画布同比放大）
--margin N          覆盖 PET_CANVAS_MARGIN_PX（物理像素）
--animation NAME    启动时播放的动画
--version           显示版本号
```

## 自适应缩放（为什么人物会显得小）

Spine 模型的"顶点流范围"和"实际看得见的范围"不是一回事。真实工程里普遍存在隐藏附件
（alpha 为 0 的配件、被停用的分支、缩放到 0 的占位图形），它们的顶点仍会出现在顶点流里。
若把它们当成"画得出来"的几何来测量，包围盒会被拉宽、中心会偏移，自适应缩放就会**同时算错
比例和位置**——人物又小又偏。远处的裁剪/特效附件也有同样效果。

因此渲染层只统计可见几何（alpha > 0 且非退化三角形），并按待机动画整段采样取并集定标，
这样人物既不会随动画摆动而抖动，也不会被自己的大动作裁掉。

以 `build_char_180_amgoat_sanrio_2` 为例（`Relax` 姿态）：

| 测量项 | 数值 |
| --- | --- |
| 全部三角形的顶点流范围 | 499.0 × 465.1 单位 |
| 仅可见几何 | 215.1 × 465.1 单位 |
| 隐藏附件导致的中心偏移 | +142.0 单位 |
| 默认定标用的待机动画并集 | 313.6 × 509.6 单位 |
| `--fit all` 的全动画并集 | 903.5 × 747.1 单位 |

该模型 1574 个三角形里有 139 个不可见，且集中在角色左侧——这就是以前人物偏右的原因。

## 渲染保真度

渲染结果与旧版 pixi-spine 参考帧做过逐像素比对（`antialias: true`、同样的 `fitModel()` 数学、
`Relax` 在 t = 1.000s、512×640）。套用相同变换后，两者的几何完全一致：轮廓同为 144×312、
包围盒相同、没有亚像素偏移（dx = dy = 0 为最优，偏移 1px 误差约 3 倍）。纹理过滤双方均为
`LINEAR`/`LINEAR`；mipmap 与抗锯齿都实测过，都不会让匹配更好。

**区域附件 UV（已修复）**：区域附件的 UV 曾被多做一个 `{2,3,0,1}` 重排，而顶点与 UV 的角点
顺序本来就一致，导致每个区域附件的贴图错开一个角点。普通区域恰好无害，但图集里
`rotate: true` 打包的区域（该模型 160 个区域中有 70 个）会取到相邻角点的 UV，表现为鞋子只剩
深色块、手部细节丢失。修复后：

| | 平均通道差 | 偏差 >64 的像素 | 偏差 >160 的像素 |
| --- | --- | --- | --- |
| 修复前 | 15.02 | 1946 | 587 |
| 修复后 | 7.68 | 407 | **0** |

`test_prototype.py` 里有对应的回归测试（遍历输出三角形流，校验区域四边形的 `u` 随屏幕 x 递增）。

当前支持：单页图集、normal/additive/multiply/screen 混合模式、区域/网格附件、Spine 裁剪附件。
逐顶点着色与特殊图集布局仍需单独验证。

渲染层**刻意不画任何叠加层**：没有 FPS 或动画名标签（透明窗口上文字会留下可见背景块），
帧率与 GL 错误只写入 `stderr`。

## 本地模型库

模型来自 [Ark-Models](https://github.com/isHarryh/Ark-Models) 的 `models/` 目录，按
[models_data.json](src/assets/models_data.json) 的映射整理成两级目录：

```
src/assets/<角色名>/<皮肤名>/<模型文件>
src/assets/阿米娅/默认服装/build_char_002_amiya.skel | .atlas | .png
```

命名规则：

- 角色目录取 `models_data.json` 中该条目的 `name`，皮肤目录取 `skinGroupName`。
- 文件名沿用 `assetList` 中的资源名（`assetId`），但会把 `\ / : * ? " < > | # %` 等文件系统与
  URL 不安全字符替换为 `_`（例如 `build_char_263_skadi_summer#3.png` →
  `build_char_263_skadi_summer_3.png`）。桥接层读取贴图时也会把 `#` 退回 `_`，以兼容图集里
  仍写着原始名字的情况。
- 同一角色内皮肤重名时追加 `_2`、`_3` 后缀；骨架缺扩展名的文件按内容补全为 `.skel` 或 `.json`。

`src/assets` 已纳入版本管理（约 618MB / 2809 个文件），克隆后即可直接使用。

## 测试

```powershell
.venv\Scripts\python test_prototype.py
```

覆盖模型加载、动画推进、裁剪附件、备用模型、批绘制顺序、透明 GL 表面，以及区域 UV 采样的
回归测试。

## Spine Runtime 授权

Spine Runtime 源码来自 Esoteric Software 官方 3.8 分支，**不随本仓库分发**，由
`build_runtime.py` 在首次构建时拉取到 `vendor/`（该目录不入库）。分发集成了 Spine Runtime 的
应用前，请阅读上游 `spine-c/LICENSE` 与 Spine Runtime 授权条款。集成 Spine Runtime 的产品
使用者需要满足 Esoteric Software 的授权要求。

## legacy：旧 Vue/Electron 实现

`legacy/` 存放 0.6.x 的 Vue 3 + Electron + pixi-spine 实现（`legacy/src`、`legacy/electron`、
`legacy/vite.config.ts` 等）。**它已不再维护**，且由于构建配置被移动到子目录、模型资源仍位于
根目录 `src/assets`，它不能直接构建，仅作代码参考；如需运行旧版请切回 `main` 分支。

旧版实现过的、Python 版**尚未迁移**的功能：桌面活动气泡（前台应用检测）、自动走动与休息、
系统空闲睡眠、鼠标穿透。
