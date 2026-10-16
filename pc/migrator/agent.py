"""与手机 agent APK 的通信通道（adb forward + TCP + 行式 JSON 协议）。

协议见 agent-android/README.md：
  请求：一行 JSON
  应答：一行 JSON 头；导出类命令后跟 `len` 字节载荷
"""
import json
import os
import socket
import time

AGENT_PKG = "com.shop.migrator.agent"
AGENT_ACTIVITY = f"{AGENT_PKG}/.MainActivity"
AGENT_SERVICE = f"{AGENT_PKG}/.AgentService"
AGENT_PORT = 7780


class AgentError(RuntimeError):
    pass


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class AgentChannel:
    """与一台手机上的 agent 建立的通道。用法：with AgentChannel(...) as ch: ...

    每个命令使用独立的短连接，避免长连接被各 ROM 的省电策略掐断。
    """

    def __init__(self, adb, serial, apk_path=None, log=print):
        self.adb = adb
        self.serial = serial
        self.apk = apk_path
        self.log = log
        self.port = None
        self.sock = None

    # -- 生命周期 ----------------------------------------------------------
    def __enter__(self):
        self.port = _free_port()
        self.adb.forward(self.serial, self.port, AGENT_PORT)
        if not self._ping():
            self._install_and_start()
            deadline = time.time() + 10
            while time.time() < deadline:
                if self._ping():
                    break
                time.sleep(1)
            else:
                raise AgentError(
                    f"[{self.serial}] agent 无响应：请确认手机屏幕已解锁、"
                    f"agent 已被允许安装并启动（MIUI 需打开“USB 安装”开关）")
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        finally:
            self.sock = None
            if self.port:
                self.adb.forward_remove(self.serial, self.port)

    # -- agent 安装与启动 --------------------------------------------------
    def _install_and_start(self):
        if self.apk and os.path.isfile(self.apk):
            self.log(f"[{self.serial}] 安装 agent APK …")
            out = self.adb.run(["-s", self.serial, "install", "-r", "-g", self.apk],
                               timeout=180, ok_fail=True)
            if "Success" not in out:
                self.log(f"[{self.serial}] agent 安装未成功（手机上可能弹出了确认框，"
                         f"请点允许后重试）: {out.strip()[:200]}")
        self.adb.run(["-s", self.serial, "shell",
                      f"am start-foreground-service -n {AGENT_SERVICE}"],
                     timeout=15, ok_fail=True)

    def open_activity(self):
        """在手机上打开 agent 界面（短信迁移引导时用）。"""
        self.adb.run(["-s", self.serial, "shell", f"am start -n {AGENT_ACTIVITY}"],
                     timeout=15, ok_fail=True)

    # -- 协议底层 ----------------------------------------------------------
    def _connect(self):
        self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=15)
        self.sock.settimeout(300)

    def _close_sock(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def _ping(self):
        try:
            self._connect()
            self._send_line({"cmd": "ping"})
            return bool(self._read_header())
        except Exception:
            return False
        finally:
            self._close_sock()

    def _send_line(self, obj):
        self.sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def _readline(self):
        buf = bytearray()
        while True:
            b = self.sock.recv(1)
            if not b:
                raise AgentError("agent 连接中断")
            if b == b"\n":
                break
            if b != b"\r":
                buf += b
                if len(buf) > 65536:
                    raise AgentError("应答行过长")
        return buf.decode("utf-8")

    def _read_header(self):
        line = self._readline()
        try:
            hdr = json.loads(line)
        except ValueError:
            raise AgentError(f"agent 应答异常: {line[:200]}")
        if not hdr.get("ok"):
            raise AgentError(f"agent 报错: {hdr.get('error', line[:200])}")
        return hdr

    def _read_exact(self, n):
        out = bytearray()
        while len(out) < n:
            chunk = self.sock.recv(min(262144, n - len(out)))
            if not chunk:
                raise AgentError("agent 数据传输中断")
            out += chunk
        return bytes(out)

    # -- 通用命令 ----------------------------------------------------------
    def call(self, cmd, **extra):
        """纯 JSON 命令，返回应答 dict（已保证 ok=true）。"""
        self._connect()
        try:
            req = {"cmd": cmd}
            req.update(extra)
            self._send_line(req)
            return self._read_header()
        finally:
            self._close_sock()

    def export_bytes(self, cmd):
        """导出类命令：返回字节载荷。"""
        self._connect()
        try:
            self._send_line({"cmd": cmd})
            hdr = self._read_header()
            return self._read_exact(int(hdr["len"]))
        finally:
            self._close_sock()

    def import_bytes(self, cmd, data):
        """导入类命令：发送字节载荷，返回应答 dict。"""
        self._connect()
        try:
            self._send_line({"cmd": cmd, "len": len(data)})
            self.sock.sendall(data)
            return self._read_header()
        finally:
            self._close_sock()
