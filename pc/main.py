#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""换机工作台（店内版）— PC 端入口。

零第三方依赖，Python 3.9+ 自带 tkinter 即可运行。
Mac 上开发调试，Windows 店机部署（见 部署到Windows.md）。
"""
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from migrator import ios as ios_mod
from migrator.adb import Adb, AdbError
from migrator.agent import AGENT_PKG
from migrator.pipeline import (EVT_DONE, EVT_LOG, EVT_PHASE, EVT_PROGRESS,
                               EVT_PROMPT, MigrationWorker, build_report)

APP_TITLE = "换机工作台（店内版）"


def _short_path(p, limit=42):
    """路径过长时缩略显示为 a/…/b。"""
    p = os.path.normpath(p)
    if len(p) <= limit:
        return p
    head, tail = os.path.split(p)
    return os.path.join("…", os.path.basename(head), tail)


def _load_window_icon(root):
    """tkinter 窗口图标（exe/app 图标由 PyInstaller --icon 负责）。"""
    cands = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cands += [os.path.join(exe_dir, "assets", "icon.png"),
                  os.path.join(getattr(sys, "_MEIPASS", exe_dir),
                               "assets", "icon.png")]
    else:
        cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "assets", "icon.png"))
    for c in cands:
        if os.path.isfile(c):
            try:
                root._icon_img = tk.PhotoImage(file=c)
                root.iconphoto(True, root._icon_img)
            except Exception:
                pass
            break


def _default_agent_apk():
    cands = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cands.append(os.path.join(exe_dir, "agent.apk"))       # 解压版
        cands.append(os.path.join(getattr(sys, "_MEIPASS", exe_dir),
                                  "agent.apk"))                 # 单文件版
    else:
        pc_dir = os.path.dirname(os.path.abspath(__file__))
        cands.append(os.path.join(pc_dir, "agent.apk"))
        cands.append(os.path.join(os.path.dirname(pc_dir), "agent-android",
                                  "app", "build", "outputs", "apk", "debug",
                                  "app-debug.apk"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return cands[0]


class DevicePanel(ttk.LabelFrame):
    """一张设备卡片：串号下拉框 + 型号信息。"""

    def __init__(self, master, title):
        super().__init__(master, text=title, padding=8)
        self._infos = []
        self.serial_var = tk.StringVar()
        self.combo = ttk.Combobox(self, textvariable=self.serial_var,
                                  state="readonly")
        self.combo.grid(row=0, column=0, sticky="ew")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_info())
        self.info = ttk.Label(self, text="未选择设备", justify="left")
        self.info.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.columnconfigure(0, weight=1)

    def update_devices(self, infos, prefer_serial=None):
        self._infos = infos
        self.combo["values"] = [d.display for d in infos]
        idx = None
        if prefer_serial:
            idx = next((i for i, d in enumerate(infos)
                        if d.serial == prefer_serial), None)
        if idx is None:
            idx = 0 if infos else -1
        if infos:
            self.combo.current(idx)
        self._refresh_info()

    def selected_serial(self):
        idx = self.combo.current()
        return self._infos[idx].serial if 0 <= idx < len(self._infos) else None

    def _refresh_info(self):
        idx = self.combo.current()
        if 0 <= idx < len(self._infos):
            d = self._infos[idx]
            self.info.config(text=f"{d.product} {d.model}\n{d.serial}")
        else:
            self.info.config(text="未选择设备")


class MainWindow:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("900x700")
        try:
            self.adb = Adb()
        except AdbError as e:
            messagebox.showerror("adb 错误", str(e))
            raise

        self.worker = None
        self._closing = False
        # 所有 adb/usbmuxd 等子进程调用一律在后台线程执行，
        # 结果经 ui_q 送回主线程渲染——主线程绝不阻塞，窗口不会未响应
        self.ui_q = queue.Queue()
        self._build_ui()

        threading.Thread(target=self._device_poller, daemon=True).start()
        self.root.after(120, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        ms = os.environ.get("MIGRATOR_AUTOCLOSE_MS")
        if ms and ms.isdigit():  # 冒烟测试用
            self.root.after(int(ms), root.destroy)

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        self.panel_a = DevicePanel(top, "旧机（数据来源）")
        self.panel_a.pack(side="left", fill="both", expand=True)
        self.swap_btn = ttk.Button(top, text="⇄ 交换", width=6,
                                   command=self._swap)
        self.swap_btn.pack(side="left", padx=8)
        self.panel_b = DevicePanel(top, "新机（写入目标）")
        self.panel_b.pack(side="right", fill="both", expand=True)

        opts = ttk.LabelFrame(self.root, text="迁移内容", padding=10)
        opts.pack(fill="x", padx=10, pady=(0, 6))
        self.vars = {
            "photos": tk.BooleanVar(value=True),
            "contacts": tk.BooleanVar(value=True),
            "calls": tk.BooleanVar(value=True),
            "sms": tk.BooleanVar(value=False),
            "apps": tk.BooleanVar(value=False),
        }
        rows = [
            ("photos", "照片与视频（DCIM / Pictures / Movies，本地缓存中转、结束即删）"),
            ("contacts", "通讯录（自动去重）"),
            ("calls", "通话记录"),
            ("sms", "短信（写入时需在新机屏幕上确认一次）"),
            ("apps", "已装应用（APK 重装，不含应用数据/登录）"),
        ]
        for key, label in rows:
            ttk.Checkbutton(opts, text=label, variable=self.vars[key]).pack(anchor="w")

        apk_row = ttk.Frame(opts)
        apk_row.pack(fill="x", pady=(6, 0))
        self.apk_var = tk.StringVar(value=_default_agent_apk())
        self.agent_status = ttk.Label(apk_row, text="")
        self.agent_status.pack(side="left")
        ttk.Button(apk_row, text="更换 Agent APK…",
                   command=self._pick_apk).pack(side="right")

        run = ttk.Frame(self.root, padding=(10, 4))
        run.pack(fill="x")
        self.start_btn = ttk.Button(run, text="开始迁移", command=self._start,
                                    state="disabled")
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(run, text="取消", command=self._cancel,
                                     state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        self.cleanup_btn = ttk.Button(run, text="清理客户手机",
                                      command=self._cleanup, state="disabled")
        self.cleanup_btn.pack(side="right")
        self.phase_var = tk.StringVar(value="等待设备 …")
        ttk.Label(run, textvariable=self.phase_var).pack(side="left", padx=12)

        self.bar = ttk.Progressbar(self.root, mode="indeterminate")
        self.bar.pack(fill="x", padx=10, pady=6)

        logf = ttk.LabelFrame(self.root, text="日志", padding=4)
        logf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_text = tk.Text(logf, height=12, state="disabled",
                                font=("Menlo" if sys.platform != "win32"
                                      else "Consolas", 11))
        self.log_text.pack(fill="both", expand=True)

        self.status = ttk.Label(self.root, text=f"adb: {self.adb.path}",
                                anchor="w", padding=(10, 2))
        self.status.pack(fill="x")
        self._refresh_agent_status()

    # -- 设备轮询 -----------------------------------------------------------
    # -- 设备轮询（后台线程执行，主线程只渲染结果） -------------------------
    def _device_poller(self):
        while not self._closing:
            err = None
            try:
                infos = self.adb.list_devices()
            except AdbError as e:
                infos, err = [], str(e)
            else:
                # iOS 设备（未装 pymobiledevice3 时自动跳过；usbmux 调用也在线程内）
                if ios_mod.available():
                    for udid, _t in ios_mod.list_ios_devices():
                        infos.append(DeviceInfo(ios_mod.IOS_PREFIX + udid,
                                                model="iPhone", product="Apple"))
            self.ui_q.put(("devices", infos, err))
            time.sleep(2)

    def _apply_devices(self, infos, err):
        if err:
            self.status.config(text=f"adb 错误: {err}")
        else:
            ios_n = sum(1 for i in infos if ios_mod.is_ios(i.serial))
            extra = f"，iPhone {ios_n} 台" if ios_n else ""
            self.status.config(
                text=f"adb: {self.adb.path}   |   安卓 {len(infos) - ios_n} 台{extra}"
                     + ("" if not ios_mod.available()
                        else "   |   iOS 模块已加载"))
        keep_a = self.panel_a.selected_serial()
        keep_b = self.panel_b.selected_serial()
        self.panel_a.update_devices(infos, prefer_serial=keep_a)
        self.panel_b.update_devices(infos, prefer_serial=keep_b)
        self._refresh_start_state()

    def _refresh_start_state(self):
        can = (self.worker is None
               and self.panel_a.selected_serial() is not None
               and self.panel_b.selected_serial() is not None
               and self.panel_a.selected_serial() != self.panel_b.selected_serial()
               and any(v.get() for v in self.vars.values()))
        self.start_btn.config(state="normal" if can else "disabled")

    def _swap(self):
        sa = self.panel_a.selected_serial()
        sb = self.panel_b.selected_serial()
        infos = self.panel_a._infos
        self.panel_a.update_devices(infos, prefer_serial=sb)
        self.panel_b.update_devices(infos, prefer_serial=sa)
        self._refresh_start_state()

    def _on_close(self):
        self._closing = True
        self.root.destroy()

    def _pick_apk(self):
        p = filedialog.askopenfilename(
            title="选择 agent APK", filetypes=[("APK", "*.apk")])
        if p:
            self.apk_var.set(p)
            self._refresh_agent_status()

    def _refresh_agent_status(self):
        """agent APK 默认随程序内置（exe 同级或包内），只在缺失时才需要手动指定。"""
        p = self.apk_var.get()
        if p and os.path.isfile(p):
            size = os.path.getsize(p) / 1024
            self.agent_status.config(
                text=f"Agent APK：已内置（{_short_path(p)}，{size:.0f} KB）")
        else:
            self.agent_status.config(
                text="Agent APK：未找到——通讯录/短信/通话记录将不可用，"
                     f"点右侧指定（找不到：{_short_path(p)}）")

    # -- 迁移控制 -----------------------------------------------------------
    def _start(self):
        src = self.panel_a.selected_serial()
        dst = self.panel_b.selected_serial()
        if not src or not dst or src == dst:
            messagebox.showwarning("提示", "请先选择两台不同的设备（旧机/新机）")
            return
        opts = {k: v.get() for k, v in self.vars.items()}
        if not any(opts.values()):
            messagebox.showwarning("提示", "至少勾选一项迁移内容")
            return
        if ios_mod.is_ios(dst):
            if ios_mod.is_ios(src):
                messagebox.showerror("不支持", "两部设备不能都是 iPhone")
                return
            if not opts.get("photos"):
                messagebox.showwarning(
                    "不支持", "iPhone 作为新机目前只支持导入照片")
                return
            for key in ("contacts", "sms", "calls", "apps"):
                opts[key] = False
            if not messagebox.askyesno(
                    "实验性功能确认",
                    "安卓照片 → iPhone 采用“AFC 写入 + 重建相册索引”实现"
                    "（爱思助手同原理）：\n\n"
                    "· 导入完成后 iPhone 会自动重启\n"
                    "· 重建索引会清空目标 iPhone 已有的相簿/收藏/编辑记录"
                    "（新机无影响，老机慎用）\n"
                    "· 仅支持图片，视频暂不支持\n\n继续？"):
                return
        if ios_mod.is_ios(src):
            skip = [label for key, label in
                    (("sms", "短信"), ("calls", "通话记录"), ("apps", "应用"))
                    if opts.get(key)]
            if skip:
                if not messagebox.askyesno(
                        "iPhone 旧机",
                        f"iPhone 作为旧机时暂不支持：{'、'.join(skip)}。\n"
                        f"本次将只迁移勾选的照片与通讯录，继续？"):
                    return
            for key in ("sms", "calls", "apps"):
                opts[key] = False
        if not messagebox.askyesno(
                "确认",
                f"将从 旧机 {src}\n迁移到 新机 {dst}\n\n"
                f"确认两台手机屏幕已解锁并保持连接？"):
            return
        self.log_line(f"===== 开始迁移：{src} → {dst} =====")
        self.worker = MigrationWorker(self.adb, src, dst, opts,
                                      self.apk_var.get().strip() or None)
        self.worker.start()
        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.cleanup_btn.config(state="disabled")
        self.bar.config(mode="determinate", value=0)
        self.phase_var.set("迁移进行中 …")

    def _cancel(self):
        if self.worker:
            self.worker.cancel()
            self.phase_var.set("正在取消 …")

    def _cleanup(self):
        serials = [s for s in (self.panel_a.selected_serial(),
                               self.panel_b.selected_serial())
                   if s and not ios_mod.is_ios(s)]
        if not serials:
            messagebox.showinfo("提示", "当前没有已连接的设备")
            return
        if not messagebox.askyesno(
                "清理客户手机",
                "将执行：\n"
                f"1. 卸载两台手机上的换机助手 Agent\n"
                f"2. 关闭两台手机的 USB 调试\n\n"
                f"目标设备：{', '.join(serials)}\n\n继续？"):
            return
        self.log_line("===== 清理客户手机 =====")
        self.cleanup_btn.config(state="disabled")
        self.phase_var.set("清理中 …")

        def work():
            for s in serials:
                out = self.adb.run(["-s", s, "uninstall", AGENT_PKG],
                                   timeout=60, ok_fail=True)
                self.ui_q.put(("log", f"[{s}] 卸载 agent："
                                      f"{'成功' if 'Success' in out else '无或失败'}"))
            # adb_enabled=0 会断开 adb 自身，必须放最后且忽略失败
            for s in serials:
                self.adb.run(["-s", s, "shell",
                              "settings put global adb_enabled 0"],
                             timeout=15, ok_fail=True)
                self.ui_q.put(("log", f"[{s}] 已请求关闭 USB 调试"))
            self.ui_q.put(("cleanup_done", None))

        threading.Thread(target=work, daemon=True).start()

    # -- 事件处理 -----------------------------------------------------------
    def _poll_events(self):
        # 1) 界面事件队列（设备轮询 / 清理线程的结果）
        try:
            while True:
                item = self.ui_q.get_nowait()
                kind = item[0]
                if kind == "devices":
                    self._apply_devices(item[1], item[2])
                elif kind == "log":
                    self.log_line(item[1])
                elif kind == "cleanup_done":
                    self.cleanup_btn.config(state="normal")
                    self.phase_var.set("清理完成")
        except queue.Empty:
            pass
        # 2) 迁移 worker 的事件
        if self.worker:
            drained = True
            while drained:
                try:
                    kind, value = self.worker.q.get_nowait()
                except queue.Empty:
                    drained = False
                else:
                    self._handle_event(kind, value)
        self.root.after(120, self._poll_events)

    def _handle_event(self, kind, value):
        if kind == EVT_LOG:
            self.log_line(str(value))
        elif kind == EVT_PHASE:
            self.phase_var.set(str(value))
        elif kind == EVT_PROMPT:
            self.log_line(f"👉 {value}")
            self.phase_var.set("需要手机上的操作，看日志/弹窗")
            self.root.bell()
            messagebox.showinfo("需要手机上的操作", str(value))
        elif kind == EVT_PROGRESS:
            tag, done, total = value
            if tag == "photos":
                self._set_progress(done, total)
            else:  # apps / ios
                self.bar.config(mode="determinate", maximum=max(total, 1),
                                value=done)
        elif kind == EVT_DONE:
            ok, msg, stats = value
            self.bar.stop()
            self.bar.config(mode="determinate", value=100 if ok else 0)
            self.phase_var.set("✅ " + msg if ok else "❌ " + msg)
            self.log_line(f"===== {'✅' if ok else '❌'} {msg} =====")
            self.worker = None
            self.cancel_btn.config(state="disabled")
            self.cleanup_btn.config(state="normal")
            self._refresh_start_state()
            messagebox.showinfo("迁移报告", build_report(stats, ok, msg))

    def _set_progress(self, done, total):
        """只更新进度条；阶段文字由 worker 的 EVT_PHASE 驱动。"""
        if total:
            if str(self.bar.cget("mode")) != "determinate":
                self.bar.stop()
                self.bar.config(mode="determinate")
            self.bar.config(maximum=total, value=min(done, total))
        else:
            if str(self.bar.cget("mode")) != "indeterminate":
                self.bar.config(mode="indeterminate")
                self.bar.start(40)

    def log_line(self, line):
        self.log_text.config(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")


def main():
    root = tk.Tk()
    _load_window_icon(root)
    try:
        MainWindow(root)
    except AdbError:
        return
    root.mainloop()


if __name__ == "__main__":
    main()
