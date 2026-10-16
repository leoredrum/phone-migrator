"""短信与通话记录迁移链路（经 agent）。

短信导入要求目标机上的 agent 是当前默认短信应用——
PC 会引导店员在新机上点一次“设为默认短信应用”，迁完提醒切回。
"""
import json
import time

from . import contacts  # noqa: F401  （保持旧导入兼容）
from .agent import AgentChannel, AgentError

SMS_DEFAULT_WAIT_SEC = 300


def transfer_calls(adb, src, dst, agent_apk, log):
    with AgentChannel(adb, src, agent_apk, log) as ca:
        data = ca.export_bytes("calls.export")
    count = _json_len(data)
    log(f"通话记录导出：{count} 条")
    if count == 0:
        log("源机没有通话记录，跳过")
        return {"inserted": 0, "skipped": 0, "total": 0}

    with AgentChannel(adb, dst, agent_apk, log) as cb:
        res = cb.import_bytes("calls.import", data)
    res["total"] = count
    log(f"通话记录导入完成：写入 {res.get('inserted', '?')} 条，"
        f"跳过 {res.get('skipped', '?')} 条")
    return res


def transfer_sms(adb, src, dst, agent_apk, log, prompt):
    """prompt(msg) 用于向店员弹出手机端需要操作的提示。"""
    with AgentChannel(adb, src, agent_apk, log) as ca:
        data = ca.export_bytes("sms.export")
    count = _json_len(data)
    log(f"短信导出：{count} 条（{len(data) / 1024:.0f} KB）")
    if count == 0:
        log("源机没有短信，跳过")
        return {"inserted": 0, "skipped": 0, "total": 0}

    with AgentChannel(adb, dst, agent_apk, log) as cb:
        st = cb.call("sms.status")
        if not st.get("is_default"):
            prompt("【需要在新机上操作一次】\n\n"
                   "电脑已在新机上打开“换机助手Agent”：\n"
                   "1. 点“设为默认短信应用（迁移短信用）”\n"
                   "2. 系统弹窗里点“确认/更换”\n\n"
                   "设置成功后电脑会自动继续，无需其他操作。")
            cb.open_activity()
            deadline = time.time() + SMS_DEFAULT_WAIT_SEC
            while time.time() < deadline:
                st = cb.call("sms.status")
                if st.get("is_default"):
                    break
                time.sleep(2)
            else:
                raise AgentError(
                    f"等待设为默认短信应用超时（{SMS_DEFAULT_WAIT_SEC // 60} 分钟）。"
                    f"可在新机上手动设置后重试迁移。")

        res = cb.import_bytes("sms.import", data)

    res["total"] = count
    log(f"短信导入完成：写入 {res.get('inserted', '?')} 条，"
        f"跳过 {res.get('skipped', '?')} 条")
    prompt("短信已写入新机。\n\n请在新机的“换机助手Agent”里点"
           "【切回原短信应用】，然后回到微信/短信确认记录显示正常。")
    return res


def _json_len(data):
    try:
        return len(json.loads(data.decode("utf-8")))
    except Exception:
        return 0
