"""照片/视频迁移：两段式传输，防数据抖动。

阶段1：旧机 tar 流快速读入 PC 本地缓存（系统临时目录，单个 tar 文件）——
        旧机尽早"完事"，不受新机写入速度拖累。
阶段2：从本地缓存稳定写入新机；若新机侧断流（USB 抖动/手机卡顿），
        从缓存整段重试，无需再碰旧机。

缓存在迁移结束后（无论成败）立即删除。
"""
import os
import re
import subprocess
import tempfile
import time

from . import Cancelled

SRC_DIRS = ("DCIM", "Pictures", "Movies")
CHUNK = 262144
PUSH_RETRIES = 3
RETRY_DELAY_SEC = 2


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


def transfer(adb, a_serial, b_serial, dirs, on_progress, should_cancel, log=print):
    """两段式直传。on_progress(phase, done, total)，phase: 1=读旧机 2=写新机。

    返回成功写入的字节数。
    """
    total = total_bytes(adb, a_serial, dirs)
    fd, spool = tempfile.mkstemp(prefix="migrator_spool_", suffix=".tar")
    os.close(fd)
    try:
        _pull_to_spool(adb, a_serial, dirs, spool, total, on_progress,
                       should_cancel)
        size = os.path.getsize(spool)
        log(f"旧机读取完成：{size / 1048576:.1f} MB（已进本地缓存，"
            f"写入新机后立即删除）")
        _push_from_spool(adb, b_serial, spool, size, on_progress,
                         should_cancel, log)
        return size
    finally:
        try:
            os.remove(spool)
        except OSError:
            pass


def _pull_to_spool(adb, a_serial, dirs, spool, total, on_progress, should_cancel):
    """阶段1：A 机 tar 流 → 本地缓存文件。"""
    pa = adb.popen(a_serial,
                   ["exec-out", "tar", "-cf", "-", "-C", "/sdcard", *dirs],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    done = 0
    try:
        with open(spool, "wb") as f:
            while True:
                if should_cancel():
                    raise Cancelled()
                chunk = pa.stdout.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                on_progress(1, done, total)
        rc = pa.wait(timeout=60)
        if rc != 0:
            err = pa.stderr.read().decode(errors="replace")[:300]
            raise RuntimeError(f"旧机 tar 打包异常退出 (rc={rc}): {err}")
    finally:
        if pa.poll() is None:
            pa.kill()
        for s in (pa.stdout, pa.stderr):
            try:
                s.close()
            except Exception:
                pass


def _push_from_spool(adb, b_serial, spool, size, on_progress, should_cancel, log):
    """阶段2：本地缓存 → B 机解包。断流整段重试（tar 覆盖写，幂等）。"""
    last_err = ""
    for attempt in range(1, PUSH_RETRIES + 1):
        pb = adb.popen(b_serial,
                       ["shell", "-T", "tar -xf - -C /sdcard"],
                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
        done = 0
        try:
            with open(spool, "rb") as f:
                while True:
                    if should_cancel():
                        raise Cancelled()
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    pb.stdin.write(chunk)
                    done += len(chunk)
                    on_progress(2, done, size)
            pb.stdin.close()
            rc = pb.wait(timeout=120)
            if rc == 0:
                return
            last_err = pb.stderr.read().decode(errors="replace")[:300]
        except (BrokenPipeError, OSError) as e:
            last_err = str(e)
            rc = pb.poll()
            if should_cancel():
                raise
        finally:
            if pb.poll() is None:
                pb.kill()
            for s in (pb.stdin, pb.stderr):
                try:
                    s.close()
                except Exception:
                    pass
        if attempt < PUSH_RETRIES:
            log(f"写入新机中断（第 {attempt} 次：rc={rc} {last_err}），"
                f"{RETRY_DELAY_SEC} 秒后从本地缓存重试 …")
            time.sleep(RETRY_DELAY_SEC)
    raise RuntimeError(f"写入新机连续 {PUSH_RETRIES} 次失败：{last_err}")


def trigger_media_scan(adb, serial, dirs):
    """尝试触发媒体库扫描。Android 11+ 的 adb 写入一般会自动入库，
    agent 的 media.scan 会做精确扫描，此处对旧系统兜底广播一次。"""
    for d in dirs:
        adb.shell(serial,
                  f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN "
                  f"-d file:///sdcard/{d}", timeout=15, ok_fail=True)
