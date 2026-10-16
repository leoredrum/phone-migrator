# 部署到 Windows 店机

## 方式 A：装 Python 直接跑（推荐，最简单）

1. 店机装 **Python 3.11+**：https://www.python.org/downloads/windows/
   安装时务必勾选 **"Add python.exe to PATH"**（python.org 安装包自带 tkinter，无需另装）。
2. 把整个 `pc/` 文件夹拷到店机（U盘/微信传均可）。
   里面的 `tools/adb-windows/` 已带 Windows 版 adb，无需另装。
3. 双击 **运行.bat**。

## 方式 B：打包成单个 exe（可选）

在一台 Windows 电脑上执行一次：

```bat
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name 换机工作台 main.py
```

产物在 `dist/换机工作台.exe`。注意 exe 不包含 adb 和 agent.apk，
要把 `tools/adb-windows/`、`agent.apk` 和 exe 放同一目录再拷走。

## iOS 支持（可选，iPhone → 安卓：照片 + 通讯录）

PC 工具本体零依赖；要支持 iPhone 需额外装 pymobiledevice3：

1. 店机装 **Apple Devices**（Microsoft Store）或 iTunes——提供 iPhone 的
   USB 驱动和 usbmuxd 服务，装一次即可。
2. 在 `pc/` 目录开 CMD 执行：

   ```bat
   python -m venv .venv-ios
   .venv-ios\Scripts\pip install pymobiledevice3
   ```

3. 以后启动用 venv 里的 Python：`.venv-ios\Scripts\python main.py`
   （不装也能跑，只是不识别 iPhone，安卓迁移不受影响）。

使用注意：
- iPhone 首次连接会弹“信任此电脑”，需客户输密码确认；
- **iPhone → 安卓**：照片走 AFC 直读；通讯录走“只取通讯录域”的过滤备份（已跳过
  相机胶卷）。若手机开启了**加密备份**且无法提供密码，通讯录取不到（照片不受影响）；
- **安卓 → iPhone（照片）**：写入 DCIM 后删除 Photos.sqlite 触发系统重建索引并自动
  重启（爱思助手同原理）。**只建议用于新机**——重建会清空目标 iPhone 已有的
  相簿/收藏/编辑记录；仅支持图片，视频暂不支持。第一次使用请先用备用机验证；
- 通讯录等其他数据写入 iPhone：苹果未开放第三方写入口，新机开箱阶段走官方
  “转移到 iOS”。
- Mac 开发机等价命令：`python3 -m venv .venv-ios && .venv-ios/bin/pip install pymobiledevice3`，
  然后 `.venv-ios/bin/python main.py`。

## USB 驱动

插上手机后如果工作台一直识别不到设备（手机上也没弹“允许 USB 调试”），
多为缺驱动，按品牌装一次即可：

| 品牌 | 驱动来源 |
|---|---|
| 小米/Redmi | 装“小米助手”或小米官方 USB 驱动 |
| OPPO/一加/realme | 一般免驱；不行就装官方驱动包 |
| vivo/iQOO | 一般免驱；不行就装“vivo 助手” |
| 华为/荣耀 | 装“华为手机助手”（注意：仅限仍用安卓的机型） |
| 三星 | Samsung USB Driver for Mobile Phones |
| 通用兜底 | 装 Google USB Driver（Android SDK 里带）或 Ubiquitous 通用的 adb 驱动 |

装好驱动后重插手机、在手机上确认“允许 USB 调试”。

## 各品牌开关速查（店员贴墙版）

- 所有安卓：设置 → 关于手机 → 连点“版本号”7 次开启开发者模式 → 开发者选项 → USB 调试
- 小米/HyperOS：**USB 调试** + **USB 调试（安全设置）** + **USB 安装**（后两个可能要求插 SIM 卡）
- OPPO/ColorOS：USB 调试 + （部分版本）关闭“权限监控”里的 USB 安装监控
- 首次连接每台手机弹 RSA 指纹框 → 勾“一律允许这台电脑” → 允许

## 常见问题

- **设备显示 unauthorized**：手机上没点允许弹窗，解锁屏幕重新确认。
- **agent 装不上（MIUI）**：检查“USB 安装”开关；手机上会弹安装确认框，点允许。
- **照片传完相册里看不到**：Android 11+ 一般自动入库；个别旧系统重启一次手机即可
  （后续版本会用 agent 做精确媒体扫描）。
- **杀毒软件拦截 adb.exe**：加入信任白名单（本 adb 来自 Google 官方 platform-tools）。
