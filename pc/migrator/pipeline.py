"""迁移任务编排：后台线程执行，事件经队列送回 GUI。

安卓→安卓顺序：通讯录 → 通话记录 → 短信（需新机上一次确认）→ 照片视频 → 应用 → 媒体扫描。
iPhone→安卓：照片（AFC）→ 通讯录（过滤备份）。
"""
import queue
import threading

from . import apps, contacts, photos, sms_calls
from .agent import AgentChannel

EVT_LOG = "log"
EVT_PHASE = "phase"
EVT_PROGRESS = "progress"
EVT_PROMPT = "prompt"
EVT_DONE = "done"

_PROGRESS_EMIT_STEP = 4 * 1024 * 1024


class MigrationWorker(threading.Thread):
    def __init__(self, adb, src, dst, opts, agent_apk):
        """opts: dict(photos, contacts, sms, calls, apps) -> bool"""
        super().__init__(daemon=True)
        self.adb = adb
        self.src = src
        self.dst = dst
        self.opts = opts
        self.agent_apk = agent_apk
        self.q = queue.Queue()
        self._cancel = threading.Event()

    # -- 对外 --------------------------------------------------------------
    def emit(self, kind, value=None):
        self.q.put((kind, value))

    def log(self, msg):
        self.emit(EVT_LOG, msg)

    def prompt(self, msg):
        self.emit(EVT_PROMPT, msg)

    def cancel(self):
        self._cancel.set()

    # -- 主流程 ------------------------------------------------------------
    def run(self):
        from . import Cancelled
        from . import ios as ios_mod
        ok, msg, stats = True, "迁移完成", {}
        try:
            if ios_mod.is_ios(self.src) and ios_mod.is_ios(self.dst):
                raise ValueError("两部设备不能都是 iPhone")
            if ios_mod.is_ios(self.dst):
                stats = self._ios_out_migration()
            elif ios_mod.is_ios(self.src):
                stats = self._ios_migration()
            else:
                stats = self._android_migration()
        except Cancelled:
            ok, msg = False, "已取消"
            self.log("⏹ 已取消（部分数据可能已写入目标机）")
        except Exception as e:
            ok, msg = False, str(e)
            self.log(f"❌ 出错：{e}")
        self.emit(EVT_DONE, (ok, msg, stats))

    def _android_migration(self):
        stats = {}
        if self.opts.get("contacts"):
            self.emit(EVT_PHASE, "通讯录 …")
            stats["contacts"] = contacts.transfer(
                self.adb, self.src, self.dst, self.agent_apk, self.log)
        if self.opts.get("calls"):
            self.emit(EVT_PHASE, "通话记录 …")
            stats["calls"] = sms_calls.transfer_calls(
                self.adb, self.src, self.dst, self.agent_apk, self.log)
        if self.opts.get("sms"):
            self.emit(EVT_PHASE, "短信 …")
            stats["sms"] = sms_calls.transfer_sms(
                self.adb, self.src, self.dst, self.agent_apk,
                self.log, self.prompt)
        if self.opts.get("photos"):
            stats["photos"] = self._photos()
        if self.opts.get("apps"):
            stats["apps"] = self._apps()
        return stats

    def _ios_out_migration(self):
        """安卓 → iPhone：目前只有照片（AFC 写入 + 索引重建 + 重启）。"""
        from . import ios as ios_mod
        stats = {}
        for key, label in (("contacts", "通讯录"), ("sms", "短信"),
                           ("calls", "通话记录"), ("apps", "应用")):
            if self.opts.get(key):
                self.log(f"{label}写入 iPhone 暂不支持（苹果未开放第三方写入口），已跳过")

        if self.opts.get("photos"):
            self.emit(EVT_PHASE, "安卓照片 → iPhone …")
            if not ios_mod.available():
                raise RuntimeError("iOS 支持未安装：请先安装 pymobiledevice3（见部署文档）")
            res = ios_mod.transfer_photos_to_ios(
                self.adb, self.src, self.dst, self.log,
                lambda cur, total: self.emit(
                    EVT_PROGRESS, ("ios", cur, max(total, 1))),
                self._cancel.is_set)
            res["dirs"] = ["安卓 → iPhone"]
            stats["photos"] = res
            self.prompt("照片已写入 iPhone 并请求重启。\n\n"
                        "重启后解锁手机，等几分钟让照片 App 重建索引，"
                        "新照片就会出现（期间相册可能短暂空白属正常）。")
        return stats

    def _ios_migration(self):
        from . import ios as ios_mod
        stats = {}
        for key, label in (("sms", "短信"), ("calls", "通话记录"), ("apps", "应用")):
            if self.opts.get(key):
                self.log(f"{label}迁移暂不支持 iPhone 作为旧机，已跳过")

        if self.opts.get("photos"):
            self.emit(EVT_PHASE, "iPhone 照片：AFC 拉取并写入新机 …")
            if not ios_mod.available():
                raise RuntimeError("iOS 支持未安装：请在 pc/ 下创建 venv 并安装 "
                                   "pymobiledevice3（见 部署到Windows.md）")

            def on_photo_progress(cur, total, done_bytes):
                self.emit(EVT_PROGRESS, ("ios", cur, total))
                if done_bytes is not None:
                    self.emit(EVT_PHASE,
                              f"iPhone 照片：{cur}/{total}"
                              f"（{done_bytes / 1048576:.0f} MB）")

            res = ios_mod.transfer_photos(self.src, self.adb, self.dst,
                                           self.log, on_photo_progress,
                                           self._cancel.is_set)
            res["dirs"] = ["iPhone DCIM"]
            stats["photos"] = res
            self._media_scan(["DCIM"])

        if self.opts.get("contacts"):
            self.emit(EVT_PHASE, "iPhone 通讯录：备份并解析 …")
            if not ios_mod.available():
                raise RuntimeError("iOS 支持未安装：请先安装 pymobiledevice3")
            res = ios_mod.transfer_contacts(
                self.src, self.adb, self.dst, self.agent_apk, self.log,
                lambda cur, total, _extra: self.emit(
                    EVT_PROGRESS, ("ios", cur, total)))
            stats["contacts"] = res
        return stats

    # -- 子步骤 ------------------------------------------------------------
    def _media_scan(self, dirs):
        self.emit(EVT_PHASE, "刷新新机媒体库 …")
        scanned = False
        try:
            with AgentChannel(self.adb, self.dst, self.agent_apk, self.log) as ch:
                res = ch.call("media.scan", paths=dirs)
            self.log(f"媒体库扫描完成：{res.get('files', '?')} 个文件")
            scanned = True
        except Exception as e:
            self.log(f"agent 媒体扫描不可用（{e}），改用广播兜底")
        if not scanned:
            photos.trigger_media_scan(self.adb, self.dst, dirs)

    def _photos(self):
        self.emit(EVT_PHASE, "照片视频：扫描源机目录 …")
        dirs = photos.existing_dirs(self.adb, self.src)
        if not dirs:
            self.log("源机没有 DCIM/Pictures/Movies 目录，跳过照片视频")
            return {"mb": 0, "dirs": []}

        last = [0, 0]

        def on_progress(phase, done, total):
            # 每个阶段各自按 4MB 节流
            if done - last[phase - 1] < _PROGRESS_EMIT_STEP and \
                    not (total and done >= total):
                return
            last[phase - 1] = done
            self.emit(EVT_PROGRESS, ("photos", done, total))
            label = "照片视频 · " + ("读取旧机" if phase == 1 else "写入新机")
            if total:
                label += f"：{done / 1048576:.0f} / {total / 1048576:.0f} MB"
            else:
                label += f"：{done / 1048576:.0f} MB"
            self.emit(EVT_PHASE, label)

        self.emit(EVT_PROGRESS, ("photos", 0, 0))
        n = photos.transfer(self.adb, self.src, self.dst, dirs,
                            on_progress, self._cancel.is_set, self.log)
        self.log(f"照片视频传输完成：{n / 1048576:.1f} MB，本地缓存已删除")
        self._media_scan(dirs)
        return {"mb": round(n / 1048576, 1), "dirs": dirs}

    def _apps(self):
        self.emit(EVT_PHASE, "应用：清点旧机应用 …")

        def on_progress(i, total, pkg):
            self.emit(EVT_PROGRESS, ("apps", i, total))
            self.emit(EVT_PHASE, f"应用 {i}/{total}：{pkg}")

        ok, fails = apps.transfer_apps(self.adb, self.src, self.dst,
                                       self.log, on_progress,
                                       self._cancel.is_set)
        if fails:
            self.log("以下应用未能自动安装：")
            for pkg, reason in fails[:10]:
                self.log(f"  · {pkg} —— {reason}")
            if len(fails) > 10:
                self.log(f"  · …等共 {len(fails)} 个")
        self.log(f"应用迁移完成：成功 {ok}，失败 {len(fails)}")
        return {"ok": ok, "fails": fails}


def build_report(stats, ok, msg):
    """迁移完成报告文本（GUI 弹窗 + 可抄给客户）。"""
    lines = ["✅ 换机迁移报告" if ok else f"⚠ {msg}", ""]
    if "photos" in stats:
        s = stats["photos"]
        part = f"照片视频：{s['mb']} MB（{'/'.join(s['dirs']) or '无'}）"
        if s.get("files"):
            part += f"，共 {s['files']} 个文件"
        lines.append(part)
    if "contacts" in stats:
        s = stats["contacts"]
        lines.append("通讯录：新增 {} / 跳过 {}".format(
            s.get("added", "?"), s.get("skipped", "?")))
    if "calls" in stats:
        s = stats["calls"]
        lines.append("通话记录：写入 {} / 共 {} 条".format(
            s.get("inserted", "?"), s.get("total", "?")))
    if "sms" in stats:
        s = stats["sms"]
        lines.append("短信：写入 {} / 共 {} 条".format(
            s.get("inserted", "?"), s.get("total", "?")))
    if "apps" in stats:
        s = stats["apps"]
        lines.append(f"应用：成功 {s['ok']} / 失败 {len(s['fails'])}")
        for pkg, reason in s["fails"][:5]:
            lines.append(f"    · {pkg}（{reason}）")
    lines += ["", "提醒：",
              "· 应用数据（登录、聊天记录）不在迁移范围，微信请用自带的“聊天记录迁移”",
              "· 如迁移了短信，请确认已在新机 Agent 里切回原短信应用",
              "· 建议最后点“清理客户手机”（卸载 agent、关闭 USB 调试）"]
    return "\n".join(lines)
