"""adb 命令封装：设备枚举、shell 执行、端口转发、子进程管道。"""
import os
import shutil
import subprocess
import sys

from . import __version__  # noqa: F401


class AdbError(RuntimeError):
    pass


class DeviceInfo:
    def __init__(self, serial, model="", product=""):
        self.serial = serial
        self.model = model
        self.product = product

    @property
    def display(self):
        name = self.model or self.product or "未知型号"
        return f"{self.serial}  ({name})"

    def __repr__(self):
        return f"DeviceInfo({self.serial!r}, {self.model!r})"


class Adb:
    def __init__(self, adb_path=None):
        self.path = adb_path or self._find()

    @staticmethod
    def _find():
        if getattr(sys, "frozen", False):
            # PyInstaller 打包后：优先 exe 同级目录（解压版，可手动替换 adb），
            # 其次包内资源（onefile 单文件版，启动时自解压到 _MEIPASS）
            base = os.path.dirname(os.path.abspath(sys.executable))
            inner = getattr(sys, "_MEIPASS", base)
            cands = [os.path.join(base, "tools"), os.path.join(inner, "tools")]
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cands = [os.path.join(base, "tools")]
        found = []
        for tools_dir in cands:
            if sys.platform == "win32":
                found += [
                    os.path.join(tools_dir, "adb-windows", "adb.exe"),
                    os.path.join(tools_dir, "adb", "adb.exe"),
                ]
            else:
                found += [
                    os.path.join(tools_dir, "adb-darwin", "adb"),
                    os.path.join(tools_dir, "adb", "adb"),
                ]
        found.append(os.environ.get("MIGRATOR_ADB"))
        found.append(shutil.which("adb"))
        for c in found:
            if c and os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        raise AdbError("找不到 adb。请把 platform-tools 解压到 pc/tools/ 下，"
                       "或设置环境变量 MIGRATOR_ADB 指向 adb 可执行文件。")

    def run(self, args, timeout=30, ok_fail=False):
        """执行 adb 命令，返回 stdout 文本。"""
        try:
            p = subprocess.run([self.path, *args], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            if ok_fail:
                return ""
            raise AdbError(f"adb {args[:3]} 超时（{timeout}s）")
        if p.returncode != 0 and not ok_fail:
            err = p.stderr.decode(errors="replace").strip()
            raise AdbError(f"adb {' '.join(args[:3])} 失败: {err or p.returncode}")
        return p.stdout.decode(errors="replace")

    def run_bytes(self, args, timeout=60, ok_fail=False):
        """执行 adb 命令，返回原始 stdout 字节（用于二进制载荷，如 exec-out cat）。"""
        try:
            p = subprocess.run([self.path, *args], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            if ok_fail:
                return b""
            raise AdbError(f"adb {args[:3]} 超时（{timeout}s）")
        if p.returncode != 0 and not ok_fail:
            err = p.stderr.decode(errors="replace").strip()
            raise AdbError(f"adb {' '.join(args[:3])} 失败: {err or p.returncode}")
        return p.stdout

    def shell(self, serial, cmd, **kw):
        return self.run(["-s", serial, "shell", cmd], **kw)

    def popen(self, serial, args, **kw):
        return subprocess.Popen([self.path, "-s", serial, *args], **kw)

    def list_devices(self):
        """返回处于 device 状态的设备列表。"""
        out = self.run(["devices", "-l"], timeout=15)
        infos = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2 or parts[1] != "device":
                continue
            serial, model, product = parts[0], "", ""
            for tok in parts[2:]:
                if tok.startswith("model:"):
                    model = tok[6:]
                elif tok.startswith("product:"):
                    product = tok[8:]
            infos.append(DeviceInfo(serial, model, product))
        return infos

    def forward(self, serial, local_port, remote_port):
        self.run(["-s", serial, "forward", f"tcp:{local_port}", f"tcp:{remote_port}"], timeout=15)

    def forward_remove(self, serial, local_port):
        self.run(["-s", serial, "forward", "--remove", f"tcp:{local_port}"],
                 timeout=15, ok_fail=True)
