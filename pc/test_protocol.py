#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC 端 ⇄ agent 协议模拟测试。

按 agent-android 里的 Proto.kt / AgentService.kt 规范实现一个 mock 服务器，
验证 AgentChannel 的成帧、二进制载荷（中文/内嵌换行）、UTF-8、错误路径。
不需要真机。运行：python3 test_protocol.py
"""
import json
import socket
import threading

from migrator.agent import AgentChannel, AgentError

VCF = ("BEGIN:VCARD\nVERSION:3.0\nFN:张三\nN:张三;;;;\n"
       "TEL;TYPE=CELL:+8613800138000\nEMAIL;TYPE=INTERNET:zs@example.com\n"
       "END:VCARD\n").encode("utf-8")


class MockAgent:
    """按 AgentService 的协议实现（对照 Proto.kt）。"""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.srv.listen(4)
        self.received = {}
        self.echo_seen = None
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            c, _ = self.srv.accept()
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()

    def _handle(self, c):
        f = c.makefile("rb")
        while True:
            line = f.readline()
            if not line:
                break
            req = json.loads(line)
            cmd = req.get("cmd")
            if cmd == "ping":
                self._json(c, {"ok": True, "ver": 2})
            elif cmd == "echo":
                self.echo_seen = req.get("x")
                self._json(c, {"ok": True, "x": req.get("x")})
            elif cmd == "contacts.export":
                self._json(c, {"ok": True, "len": len(VCF)})
                c.sendall(VCF)
            elif cmd in ("contacts.import", "sms.import", "calls.import"):
                data = f.read(req["len"])
                self.received[cmd] = data
                self._json(c, {"ok": True, "added": 1, "skipped": 0,
                               "inserted": len(data)})
            elif cmd == "sms.status":
                self._json(c, {"ok": True, "is_default": False,
                               "default_pkg": "com.android.mms"})
            elif cmd == "media.scan":
                self.received["media.scan.paths"] = list(req.get("paths", []))
                self._json(c, {"ok": True, "files": 42})
            else:
                self._json(c, {"ok": False, "error": "未知命令: " + str(cmd)})
        c.close()

    @staticmethod
    def _json(c, obj):
        c.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


class FakeAdb:
    def forward(self, serial, local, remote):
        pass

    def forward_remove(self, serial, local):
        pass


def main():
    mock = MockAgent()
    # 让通道直接连到 mock 端口（跳过 _free_port 的随机分配）
    orig_free = __import__("migrator.agent", fromlist=["_free_port"])._free_port
    import migrator.agent as agent_mod
    agent_mod._free_port = lambda: mock.port

    with AgentChannel(FakeAdb(), "TEST_SERIAL", None, print) as ch:
        # 1. ping（__enter__ 内部已隐式验证）+ 纯 JSON 命令
        st = ch.call("sms.status")
        assert st["is_default"] is False and st["default_pkg"] == "com.android.mms"

        # 2. UTF-8 JSON 行往返（中文）
        res = ch.call("echo", x="换机助手→测试")
        assert res["x"] == "换机助手→测试" and mock.echo_seen == "换机助手→测试"

        # 3. 导出：二进制载荷含中文和内嵌换行，必须原样到达
        data = ch.export_bytes("contacts.export")
        assert data == VCF, "导出载荷被破坏"
        assert "张三".encode() in data

        # 4. 导入：载荷原样送达服务端（含换行不破坏成帧）
        res = ch.import_bytes("contacts.import", data)
        assert res["added"] == 1
        assert mock.received["contacts.import"] == VCF, "导入载荷被破坏"

        # 5. 带额外字段的命令（media.scan 的 paths 数组）
        res = ch.call("media.scan", paths=["DCIM", "Pictures", "Movies"])
        assert res["files"] == 42
        assert mock.received["media.scan.paths"] == ["DCIM", "Pictures", "Movies"]

        # 6. 错误路径：服务端回 ok=false 时要抛 AgentError
        try:
            ch.call("no.such.command")
            raise AssertionError("应当抛出 AgentError")
        except AgentError as e:
            assert "未知命令" in str(e)

    agent_mod._free_port = orig_free
    print("✅ 全部 6 组协议断言通过")


if __name__ == "__main__":
    main()
