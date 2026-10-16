"""应用迁移：旧机提取第三方 APK（含 split）→ 临时目录 → 新机批量安装。

不迁移应用数据（登录态/存档），数据在应用私有沙箱内，系统限制无法导出。
"""
import os
import shutil
import tempfile

from . import Cancelled
from .agent import AGENT_PKG

_FAIL_HINTS = {
    "INSTALL_FAILED_VERSION_DOWNGRADE": "新机已装更高版本",
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "签名不一致，需先卸载新机上的同名应用",
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "新机存储空间不足",
    "INSTALL_FAILED_NO_MATCHING_ABIS": "CPU 架构不兼容",
    "INSTALL_FAILED_OLDER_SDK": "新机系统版本过低",
}


def _hint(output):
    for key, hint in _FAIL_HINTS.items():
        if key in output:
            return hint
    return output.strip().splitlines()[-1][:80] if output.strip() else "未知原因"


def transfer_apps(adb, src, dst, log, on_progress, should_cancel):
    """返回 (ok_count, fail_list[(pkg, reason)])。"""
    out = adb.run(["-s", src, "shell", "pm list packages -3"], timeout=60)
    pkgs = sorted(l.replace("package:", "").strip()
                  for l in out.splitlines() if l.strip())
    pkgs = [p for p in pkgs if p != AGENT_PKG]
    total = len(pkgs)
    log(f"检测到 {total} 个第三方应用")

    tmp = tempfile.mkdtemp(prefix="migrator_apk_")
    ok, fails = 0, []
    try:
        for i, pkg in enumerate(pkgs):
            if should_cancel():
                raise Cancelled()
            on_progress(i, total, pkg)

            paths = [l.replace("package:", "").strip()
                     for l in adb.run(["-s", src, "shell", f"pm path {pkg}"],
                                      timeout=30, ok_fail=True).splitlines()
                     if l.strip().startswith("package:")]
            if not paths:
                fails.append((pkg, "无法获取 APK 路径（系统应用或已卸载）"))
                continue

            files = []
            try:
                for j, p in enumerate(paths):
                    f = os.path.join(tmp, f"{pkg}__{j}.apk")
                    with open(f, "wb") as fh:
                        fh.write(adb.run_bytes(["-s", src, "exec-out", "cat", p],
                                               timeout=300))
                    files.append(f)
                if len(files) == 1:
                    args = ["-s", dst, "install", "-r", files[0]]
                else:
                    args = ["-s", dst, "install-multiple", "-r", *files]
                res = adb.run(args, timeout=300, ok_fail=True)
            except Exception as e:
                fails.append((pkg, str(e)[:80]))
                continue
            finally:
                for f in files:
                    try:
                        os.remove(f)
                    except OSError:
                        pass

            if "Success" in res:
                ok += 1
            else:
                fails.append((pkg, _hint(res)))
        return ok, fails
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
