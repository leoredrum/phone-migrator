# 换机工作台（店内版）

手机店内部使用的数据迁移工具。一台 Windows PC + 两根数据线，把旧机数据迁到新机；
照片视频走两段式传输（旧机 → 本地缓存 → 新机，防数据抖动、断流可重试，结束即删）。

## 下载安装（两平台发行版）

到本仓库 **Releases** 页下载（由 GitHub Actions 自动构建）：

- **migrator-windows-x64.zip** —— 店机 Windows：解压 → 双击 `MigratorDesk.exe`
- **migrator-macos-arm64.zip** —— macOS（Apple 芯片）：解压 → 右键 `MigratorDesk.app`
  → 打开（首次绕过 Gatekeeper；或终端执行
  `xattr -d com.apple.quarantine MigratorDesk.app`）

两个包都已内置 adb 与 agent.apk。未签名，首次运行被 SmartScreen/Gatekeeper
拦一次属正常，选“仍要运行 / 打开”。

想从源码跑：`cd pc && python3 main.py`（零第三方依赖；iOS 功能另装 pymobiledevice3）。
打 tag（`v*`）即触发自动构建发布：`.github/workflows/release.yml`。

## 架构

```
┌─────────────────── PC：换机工作台（pc/main.py，tkinter 零依赖）───────────────────┐
│  旧机卡片(串号/型号)     ⇄交换      新机卡片(串号/型号)                            │
│  ☑照片视频(DCIM/Pictures/Movies)  ☑通讯录(vCard,自动去重)                        │
│            [开始迁移]   ████████░░ 62%   日志 …                                   │
└──────┬────────────────────────────────────────────────┬──────────────────────────┘
       │ adb（USB）                                     │ adb（USB）
   ┌───▼────┐  阶段1: A →exec-out→ 本地缓存(tar)   ┌───▼────┐
   │ 旧手机  │ ─────────────────────────────────► │ 新手机  │
   │ agent  │  阶段2: 缓存 →shell -T→ B(断流重试)  │ agent  │
   └────────┘  通讯录：vCard ←agent导出 / →agent导入└────────┘
```

- **PC 端** `pc/`：Python + tkinter，无第三方依赖。Mac 可开发调试，Windows 店机部署。
- **Agent APK** `agent-android/`：前台服务监听 `127.0.0.1:7780`，PC 通过
  `adb forward` 接入。负责通讯录导出/导入（vCard 3.0，按号码去重）。
  PC 端会通过 adb 自动安装（`install -r -g`，自动授予联系人权限）并启动它。

## 快速开始（Mac 开发机）

```bash
cd phone-migrator/pc
python3 main.py          # 需要 Homebrew 的 python-tk（brew install python-tk）
python3 test_protocol.py # 无真机时的协议自测（mock agent）
```

两台手机开 USB 调试插上，等左侧出现两台设备 → 选旧机/新机 → 开始迁移。

## 构建 Agent APK

本仓库已附带构建产物 `pc/agent.apk`（debug 签名，店内使用足够）。
重新构建（需 Android SDK + JDK 17 + Gradle）：

```bash
cd agent-android
JAVA_HOME=<jdk17路径> gradle assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk ../pc/agent.apk
```

或直接用 Android Studio 打开 `agent-android/` 构建。

## Windows 店机部署

见 [pc/部署到Windows.md](pc/部署到Windows.md)。要点：装 Python → 拷整个 `pc/` 文件夹
（`tools/adb-windows/` 已带 Windows 版 adb）→ 双击 `运行.bat`。

## 店员操作 SOP

1. 旧机、新机都打开 **开发者选项 → USB 调试**，解锁屏幕插线；
   首次连接手机上弹“允许 USB 调试”，勾选“一律允许”后确定。
2. 工作台出现两台设备，确认左边是旧机、右边是新机（不对就点 ⇄）。
3. 勾选要迁的内容 → 开始迁移 → 等进度条走完，日志出现 ✅。
4. **勾了短信时**：中途会弹提示“需要在新机上操作一次”——在旧机/新机上
   打开“换机助手Agent”→ 点“设为默认短信应用（迁移短信用）”→ 系统弹窗确认，
   电脑自动继续；迁完按提示点“切回原短信应用”。
5. 迁移完成会弹出**迁移报告**（各类数据计数 + 失败应用清单）。
6. 最后点 **“清理客户手机”**：自动卸载两台手机上的 agent 并关闭 USB 调试。
7. **MIUI/HyperOS 额外开关**：还需打开“USB 调试（安全设置）”和“USB 安装”
   （设置 → 开发者选项，部分版本要求插着 SIM 卡才能开）。

## 当前能力与边界

| 数据 | 状态 |
|---|---|
| 照片/视频/公共媒体（DCIM/Pictures/Movies） | ✅ 两段式传输：本地缓存中转（系统临时目录，结束即删），新机断流自动整段重试，agent 精确刷新媒体库 |
| 通讯录 | ✅ vCard 导出导入，按号码去重 |
| 通话记录 | ✅ agent 通道，批量事务写入 |
| 短信 | ✅ 需在新机上确认一次“设为默认短信应用”（系统安全要求），迁完切回 |
| 已装应用 | ✅ APK（含 split）提取后批量静默装回；失败原因会标注 |
| iPhone → 安卓（照片/通讯录） | ✅ 实验性：AFC 直读照片 + 过滤备份解析通讯录；需装 pymobiledevice3（见部署文档） |
| 安卓 → iPhone（照片） | ✅ 实验性：AFC 写入 DCIM + 删除 Photos.sqlite 触发索引重建 + 自动重启（爱思助手同原理）；会重建目标机相册索引（新机适用，老机慎用）；仅图片 |
| 安卓 → iPhone（通讯录等） | ❌ 苹果未开放第三方写入口；新机开箱阶段走官方“转移到 iOS” |
| 应用数据（登录态、微信聊天记录） | ❌ 系统沙箱限制，无解；用各 App 官方迁移功能 |
| 彩信（MMS） | ❌ 暂未支持（格式复杂，后续版本） |
| QR/WiFi 无线模式 | 🔜 路线图（agent 同一 APK 第二入口） |
| HarmonyOS NEXT（纯血鸿蒙）机型 | ❌ 不跑 APK；走华为自带“手机克隆” |
| 安卓 → iPhone（通讯录） | ❌ 无官方写入口；新机开箱阶段走官方“转移到 iOS” |

## 目录结构

```
phone-migrator/
├── pc/                 # PC 端（Python + tkinter）
│   ├── main.py         # GUI 入口
│   ├── migrator/
│   │   ├── adb.py      # adb 封装
│   │   ├── photos.py   # tar 直传管道
│   │   ├── contacts.py # agent 通道（forward + TCP + 行式JSON）
│   │   └── pipeline.py # 后台任务编排
│   ├── agent.apk       # 手机端 agent（构建产物）
│   ├── tools/adb-windows/  # Windows 版 platform-tools
│   ├── 运行.bat
│   └── 部署到Windows.md
└── agent-android/      # agent APK 源码（Kotlin）
```
