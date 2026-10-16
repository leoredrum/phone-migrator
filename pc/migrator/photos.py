"""照片/视频迁移：A 机 tar 打包 → PC 内存中转 → B 机解包，全程不落盘。"""
import re
import subprocess

from . import Cancelled

SRC_DIRS = ("DCIM", "Pictures", "Movies")
CHUNK = 262144


def existing_dirs(adb, serial):
    """返回源机上存在的待迁移目录名列表。"""
    out = adb.shell(serial, 'for d in DCIM Pictures Movies; do '
                            '[ -d "/sdcard/$d" ] && echo "$d"; done', timeout=15)
    return [l.strip() for l in out.splitlines() if l.strip() in SRC_DIRS]


def total_bytes(adb, serial, dirs):
    """du 统计总字节数，用于进度条；失败返回 0（进度条转不确定模式）。"""
    try:
        out = adb.shell(serial, "du -sk " + " ".join(f"/sdcard/{d}" for d in dirs),
                        timeout=900)
    except Exception:
        return 0
    total_kb = 0
    for line in out.splitlines():
        m = re.match(r"^\s*(\d+)", line)
        if m:
            total_kb += int(m.group(1))
    return total_kb * 1024


def transfer(adb, a_serial, b_serial, dirs, on_progress, should_cancel):
    """tar 管道直传。返回已传输字节数。

    A 侧用 exec-out 保证输出是原始二进制流；
    B 侧用 shell -T 关掉 pty，避免二进制数据经终端驱动被改写。
    """
    total = total_bytes(adb, a_serial, dirs)
    pa = adb.popen(a_serial,
                   ["exec-out", "tar", "-cf", "-", "-C", "/sdcard", *dirs],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pb = adb.popen(b_serial,
                   ["shell", "-T", "tar -xf - -C /sdcard"],
                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE)
    done = 0
    try:
        while True:
            if should_cancel():
                raise Cancelled()
            chunk = pa.stdout.read(CHUNK)
            if not chunk:
                break
            pb.stdin.write(chunk)
            done += len(chunk)
            on_progress(done, total)
        pb.stdin.close()
        rc_b = pb.wait(timeout=120)
        rc_a = pa.wait(timeout=120)
        if rc_a != 0 or rc_b != 0:
            err_a = pa.stderr.read().decode(errors="replace")[:300]
            err_b = pb.stderr.read().decode(errors="replace")[:300]
            raise RuntimeError(f"tar 管道异常退出 (A rc={rc_a}, B rc={rc_b}) "
                               f"A: {err_a} B: {err_b}")
        return done
    finally:
        for p in (pa, pb):
            if p.poll() is None:
                p.kill()
            for s in (p.stdout, p.stderr):
                try:
                    s.close()
                except Exception:
                    pass


def trigger_media_scan(adb, serial, dirs):
    """尝试触发媒体库扫描。Android 11+ 的 adb 写入一般会自动入库，
    此处对旧系统兜底广播一次；agent 版会改为精确的 MediaScanner 扫描。"""
    for d in dirs:
        adb.shell(serial,
                  f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN "
                  f"-d file:///sdcard/{d}", timeout=15, ok_fail=True)
