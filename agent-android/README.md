# migrator-agent（换机助手 Agent APK）

装在手机上的数据通道组件。前台服务监听 `127.0.0.1:7780`（仅本机回环，
不对外暴露），PC 端「换机工作台」通过 `adb forward tcp:<port> tcp:7780` 接入。

## 构建

- 命令行：`JAVA_HOME=<JDK17> gradle assembleDebug`
  （Gradle 9.6+ 需 AGP 9.x，本工程已固定 AGP 9.4.1）
- 或用 Android Studio 打开本目录直接 Build

产物：`app/build/outputs/apk/debug/app-debug.apk`（debug 签名，店内使用足够）。
拷贝为 `../pc/agent.apk` 供 PC 端自动安装。

## 代码结构

| 文件 | 职责 |
|---|---|
| `AgentService.kt` | 前台服务 + ServerSocket + 命令分发 |
| `ContactsIO.kt` | 通讯录 ⇄ vCard 3.0（导出姓名/电话/邮箱，导入按号码末 11 位去重） |
| `SmsIO.kt` | 短信导出/导入（导入需临时持默认短信应用身份） |
| `CallsIO.kt` | 通话记录导出/导入（批量事务） |
| `Proto.kt` | 行式 JSON 协议读写（按字节读行，避免二进制流被 Reader 预读污染） |
| `MainActivity.kt` | 状态页 + 手动启停 + 短信默认应用引导（设为默认/切回） |
| `MmsReceiver.kt` / `HeadlessSmsService.kt` | “默认短信应用”资格要求的空组件（AOSP 官方要求） |

## 协议

```
请求：一行 JSON（UTF-8，\n 结尾）
应答：一行 JSON 头；导出类命令后跟 `len` 字节载荷

{"cmd":"ping"}                              -> {"ok":true,"name":"migrator-agent","ver":2}
{"cmd":"contacts.export"}                   -> {"ok":true,"len":N}\n + N 字节 vCard
{"cmd":"contacts.import","len":N} + N 字节   -> {"ok":true,"added":X,"skipped":Y}
{"cmd":"sms.status"}                        -> {"ok":true,"is_default":bool,"default_pkg":pkg}
{"cmd":"sms.export"}                        -> {"ok":true,"len":N}\n + N 字节 JSON 数组
{"cmd":"sms.import","len":N} + N 字节        -> {"ok":true,"inserted":X,"skipped":Y}
{"cmd":"calls.export"} / {"cmd":"calls.import"} 同上
{"cmd":"media.scan","paths":["DCIM",...]}    -> {"ok":true,"files":N}
{"cmd":"stop"}                              -> {"ok":true}（服务自停）
```

PC 端有配套的 mock 测试：`../pc/test_protocol.py`。

## 扩展点（按路线图）

- 短信：`sms.export` / `sms.import`——导入端需引导用户把 agent 临时设为
  默认短信应用（`Telephony.Sms` 写入仅默认应用可用）
- 通话记录：`calls.export` / `calls.import`
- APK：`apks.list`（包名+版本+split 路径清单），提取仍走 PC 的 `pm path` + `exec-out cat`
- 媒体扫描：`media.scan`（照片直传后精确刷新 MediaStore）
- QR/WiFi 模式：同一 APK 第二入口，`AgentService` 改绑 0.0.0.0 或另起数据端口
