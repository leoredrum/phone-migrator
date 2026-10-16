"""通讯录迁移链路（vCard，经 agent）。"""
from .agent import AgentChannel


def transfer(adb, src, dst, agent_apk, log):
    """返回 dict(added, skipped)。异常向上抛。"""
    with AgentChannel(adb, src, agent_apk, log) as ca:
        data = ca.export_bytes("contacts.export")
    n = data.count(b"END:VCARD")
    log(f"通讯录导出：{n} 位联系人（{len(data) / 1024:.0f} KB）")

    with AgentChannel(adb, dst, agent_apk, log) as cb:
        res = cb.import_bytes("contacts.import", data)
    log(f"通讯录导入完成：新增 {res.get('added', '?')}，"
        f"跳过已存在 {res.get('skipped', '?')}")
    return res
