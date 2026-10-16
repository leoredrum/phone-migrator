"""iOS 设备支持（实验性，仅 iPhone → 安卓）。

- 照片：AFC 通道直拉 /DCIM → 临时目录 → adb push 到新机
- 通讯录：Mobilebackup2 做一次“只保留 HomeDomain/Library/AddressBook”的过滤备份
  （跳过相机胶卷等大文件），解析 AddressBook.sqlitedb 生成 vCard，
  再经新机 agent 的 contacts.import 写入

依赖 pymobiledevice3：pc/ 下建 venv 安装（见 部署到Windows.md / README），
未安装时整体优雅降级，不影响安卓对安卓迁移。
"""
import asyncio
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile

IOS_PREFIX = "ios:"

# AddressBook.sqlitedb 里电话/邮箱都在 ABMultiValue.value，按形态区分即可
_EMAIL_MARK = "@"

# 写入 iPhone DCIM 时接受的图片扩展名（视频先不支持，重建索引行为未验证）
IOS_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".gif", ".bmp"}
_EXT_MAP = {".jpg": ".JPG", ".jpeg": ".JPG", ".png": ".PNG",
            ".heic": ".HEIC", ".gif": ".GIF", ".bmp": ".BMP"}


def available():
    try:
        import pymobiledevice3  # noqa: F401
        return True
    except ImportError:
        return False


def is_ios(serial):
    return isinstance(serial, str) and serial.startswith(IOS_PREFIX)


def udid_of(serial):
    return serial[len(IOS_PREFIX):]


def list_ios_devices():
    """返回 [(udid, conn_type)]；无设备或 usbmuxd 不可用时返回空表。"""
    if not available():
        return []
    from pymobiledevice3.usbmux import list_devices

    try:
        devs = asyncio.run(list_devices())
    except Exception:
        return []
    out = []
    for d in devs:
        if d.connection_type == "usb":
            out.append((d.serial, d.connection_type))
    return out


def _lockdown(udid):
    from pymobiledevice3.lockdown import create_using_usbmux
    return create_using_usbmux(serial=udid, autopair=True)


# ---------------------------------------------------------------- 照片

def transfer_photos(serial, adb, dst_serial, log, on_progress, should_cancel):
    """AFC 拉取 iPhone /DCIM → 临时目录 → adb push 到安卓新机。"""
    from . import Cancelled

    ld = _lockdown(udid_of(serial))
    tmp = tempfile.mkdtemp(prefix="migrator_ios_")
    try:
        from pymobiledevice3.services.afc import AfcService

        async def pull_all():
            async with AfcService(ld) as afc:
                await afc.pull("/DCIM", tmp, progress_bar=False)

        log("正在通过 AFC 从 iPhone 拉取 DCIM（首次连接需在手机上点“信任”）…")
        asyncio.run(pull_all())

        files = []
        for root, _dirs, names in os.walk(tmp):
            for n in names:
                files.append(os.path.join(root, n))
        total = len(files)
        log(f"iPhone 照片/视频共 {total} 个文件，开始写入安卓新机 …")
        done = 0
        for i, f in enumerate(files):
            if should_cancel():
                raise Cancelled()
            rel = os.path.relpath(f, tmp)          # DCIM/1xxAPPLE/IMG_xxxx.JPG
            remote_dir = os.path.dirname(f"/sdcard/{rel}")
            adb.run(["-s", dst_serial, "shell", f"mkdir -p '{remote_dir}'"],
                    timeout=30, ok_fail=True)
            adb.run(["-s", dst_serial, "push", f, f"/sdcard/{rel}"],
                    timeout=600, ok_fail=True)
            done += os.path.getsize(f)
            on_progress(i + 1, total, done)
        mb = sum(os.path.getsize(f) for f in files) / 1048576
        return {"files": total, "mb": round(mb, 1)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            asyncio.run(ld.close())
        except Exception:
            pass


# ---------------------------------------------------------------- 通讯录

def _keep_address_book_only(backup_file):
    return (backup_file.device_name == "HomeDomain"
            and backup_file.file_name.startswith("Library/AddressBook/"))


def transfer_contacts(serial, adb, dst_serial, agent_apk, log, on_progress):
    """过滤备份取 AddressBook.sqlitedb → vCard → 新机 agent 写入。"""
    from .agent import AgentChannel

    ld = _lockdown(udid_of(serial))
    tmp = tempfile.mkdtemp(prefix="migrator_ios_backup_")
    try:
        from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service

        log("正在做通讯录备份（已过滤，跳过照片等大文件；如手机弹出“信任此电脑”请点信任）…")

        async def run_backup():
            svc = Mobilebackup2Service(ld)
            try:
                await svc.backup(
                    full=True,
                    backup_directory=tmp,
                    progress_callback=lambda pct: on_progress(pct, 100, None),
                    filter_callback=_keep_address_book_only,
                    patch_manifest=True,
                    unback=True,
                )
            finally:
                try:
                    await svc.close()
                except Exception:
                    pass

        asyncio.run(run_backup())

        db = _find(tmp, "AddressBook.sqlitedb")
        if not db:
            raise RuntimeError("备份里找不到通讯录数据库（手机可能开启了加密备份，"
                               "或系统版本把通讯录移出了该域）")
        vcf = _addressbook_to_vcard(db, log)
        n = vcf.count(b"END:VCARD")
        log(f"iPhone 通讯录解析完成：{n} 位联系人")
        if n == 0:
            return {"added": 0, "skipped": 0, "total": 0}

        with AgentChannel(adb, dst_serial, agent_apk, log) as ch:
            res = ch.import_bytes("contacts.import", vcf)
        res["total"] = n
        log("通讯录写入安卓完成：新增 {}，跳过 {}".format(
            res.get("added", "?"), res.get("skipped", "?")))
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            asyncio.run(ld.close())
        except Exception:
            pass


def _find(root, name):
    for r, _d, names in os.walk(root):
        if name in names:
            return os.path.join(r, name)
    return None


def _addressbook_to_vcard(db_path, log):
    """解析 AddressBook.sqlitedb（ABPerson + ABMultiValue）生成 vCard 字节。"""
    def esc(v):
        return (v.replace("\\", "\\\\").replace(";", "\\;")
                 .replace(",", "\\,").replace("\n", "\\n"))

    persons = {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for rid, first, middle, last, org in conn.execute(
                "SELECT ROWID, First, Middle, Last, Organization FROM ABPerson"):
            name = " ".join(p for p in (first, middle, last) if p) or org or ""
            persons[rid] = {"name": name, "phones": [], "emails": []}
        for rid, value in conn.execute(
                "SELECT record_id, value FROM ABMultiValue"):
            p = persons.get(rid)
            if not p or not value:
                continue
            if _EMAIL_MARK in value:
                p["emails"].append(value)
            else:
                p["phones"].append(value)
    except sqlite3.DatabaseError as e:
        raise RuntimeError(f"通讯录数据库解析失败：{e}")
    finally:
        conn.close()

    out = []
    for p in persons.values():
        if not p["phones"] and not p["emails"]:
            continue
        name = p["name"] or "未命名"
        lines = ["BEGIN:VCARD", "VERSION:3.0",
                 f"FN:{esc(name)}", f"N:{esc(name)};;;;"]
        for ph in p["phones"]:
            lines.append(f"TEL;TYPE=CELL:{esc(ph)}")
        for em in p["emails"]:
            lines.append(f"EMAIL;TYPE=INTERNET:{esc(em)}")
        lines.append("END:VCARD")
        out.append("\n".join(lines) + "\n")
    return "".join(out).encode("utf-8")


# ------------------------------------------------- 安卓 → iPhone（照片）

def count_android_images(adb, serial, dirs):
    """统计安卓侧可导入的图片数，用于进度条。"""
    pats = " -o ".join(f"-iname '*{e}'" for e in IOS_IMAGE_EXTS)
    out = adb.shell(serial,
                    f"find {' '.join('/sdcard/' + d for d in dirs)} "
                    f"-type f \\( {pats} \\) 2>/dev/null | wc -l",
                    timeout=120, ok_fail=True)
    try:
        return int(out.strip())
    except ValueError:
        return 0


def transfer_photos_to_ios(adb, src_serial, ios_serial, log, on_progress,
                           should_cancel):
    """安卓照片 → iPhone 相机胶卷（爱思助手同款思路，实验性）：

    1. 安卓 DCIM/Pictures 里的图片经 tar 流拉到 PC（每次只落一个临时文件）
    2. AFC 推入 iPhone /DCIM/1xxAPPLE/，按 IMG_XXXX 规范命名
    3. 删除 /PhotoData/Photos.sqlite(-wal/-shm) 触发系统重建索引
    4. 诊断服务重启 iPhone；解锁后照片 App 重建并显示新照片

    ⚠ 重建会丢失目标 iPhone 上已有的相簿/收藏/编辑记录——只适合新机或
    客户确认不在乎的机器。返回 dict(files, mb)。
    """
    from . import Cancelled
    from . import photos as photos_mod

    dirs = photos_mod.existing_dirs(adb, src_serial)
    if not dirs:
        log("安卓侧没有 DCIM/Pictures/Movies，跳过")
        return {"files": 0, "mb": 0}
    total = count_android_images(adb, src_serial, dirs)
    log(f"待导入图片：{total} 个（视频暂不支持导入 iPhone）")

    ld = _lockdown(udid_of(ios_serial))
    tmp = tempfile.mkdtemp(prefix="migrator_ios_out_")

    state = {"n": 0, "bytes": 0, "skipped": 0}

    async def run():
        from pymobiledevice3.services.afc import AfcService

        async with AfcService(ld) as afc:
            # PhotoData 必须可访问，否则无法触发索引重建
            try:
                await afc.listdir("/PhotoData")
            except Exception:
                raise RuntimeError(
                    "此 iPhone 的 AFC 通道不允许访问 /PhotoData（较新系统可能收紧了"
                    "权限），无法完成相册索引重建，已中止。已写入的文件不影响手机使用。")

            # 选择/创建 DCIM 目标目录
            try:
                dcim_entries = await afc.listdir("/DCIM")
            except Exception:
                await afc.makedirs("/DCIM")
                dcim_entries = []
            apple_dirs = sorted(d for d in dcim_entries
                                if re.fullmatch(r"1\d{2}APPLE", d))
            remote_dir = f"/DCIM/{apple_dirs[0]}" if apple_dirs else "/DCIM/100APPLE"
            await afc.makedirs(remote_dir)

            # 命名从现有最大 IMG_XXXX 之后继续，避免覆盖
            start = 1
            try:
                existing = await afc.listdir(remote_dir)
                nums = [int(m.group(1)) for f in existing
                        if (m := re.fullmatch(r"IMG_(\d{4})\.\w+", f))]
                if nums:
                    start = min(max(nums) + 1, 9990)
            except Exception:
                pass

            # 安卓 tar 流 → 逐张推入 iPhone
            proc = adb.popen(src_serial,
                             ["exec-out", "tar", "-cf", "-", "-C", "/sdcard", *dirs],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            tf = tarfile.open(fileobj=proc.stdout, mode="r|",
                              encoding="utf-8", errors="surrogateescape")
            try:
                for member in tf:
                    if not member.isfile():
                        continue
                    ext = os.path.splitext(member.name)[1].lower()
                    if ext not in IOS_IMAGE_EXTS:
                        state["skipped"] += 1
                        continue
                    if should_cancel():
                        raise Cancelled()
                    fobj = tf.extractfile(member)
                    if fobj is None:
                        continue
                    data = fobj.read()
                    idx = start + state["n"]
                    if idx > 9999:
                        log("已达 IMG_9999 命名上限，剩余图片跳过")
                        break
                    name = f"IMG_{idx:04d}{_EXT_MAP[ext]}"
                    local = os.path.join(tmp, name)
                    with open(local, "wb") as fh:
                        fh.write(data)
                    await afc.push(local, f"{remote_dir}/{name}",
                                   progress_bar=False)
                    os.remove(local)
                    state["n"] += 1
                    state["bytes"] += len(data)
                    on_progress(state["n"], total)
                rc = proc.wait(timeout=30)
                if rc != 0:
                    log(f"⚠ 安卓侧 tar 退出码 {rc}，可能部分目录未读取")
            finally:
                if proc.poll() is None:
                    proc.kill()
                tf.close()

            log(f"已写入 {state['n']} 张图片到 {remote_dir}，"
                f"准备重建相册索引 …")

            # 删除索引，触发系统重建
            removed = []
            for fname in ("Photos.sqlite", "Photos.sqlite-wal",
                          "Photos.sqlite-shm"):
                p = f"/PhotoData/{fname}"
                try:
                    if await afc.rm_single(p, force=True):
                        removed.append(fname)
                except Exception as e:
                    log(f"删除 {fname} 失败：{e}")
            if "Photos.sqlite" not in removed:
                raise RuntimeError(
                    "无法删除 Photos.sqlite（文件可能被占用），索引不会重建，"
                    "照片暂不会显示。请手动重启 iPhone 后再试一次。")
            log(f"已删除索引：{', '.join(removed)}")

        # 重启 iPhone（诊断服务）；失败则引导手动重启
        try:
            from pymobiledevice3.services.diagnostics import DiagnosticsService
            async with DiagnosticsService(ld) as diag:
                await diag.restart()
            log("已请求 iPhone 重启。解锁后等待几分钟，照片 App 会重建索引并显示新照片。")
        except Exception as e:
            log(f"自动重启失败（{e}）。请手动重启 iPhone，"
                f"解锁后等待几分钟照片即会出现。")

    try:
        asyncio.run(run())
        return {"files": state["n"], "mb": round(state["bytes"] / 1048576, 1)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            asyncio.run(ld.close())
        except Exception:
            pass
