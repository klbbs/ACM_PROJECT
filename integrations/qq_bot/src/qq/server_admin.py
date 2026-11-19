"""Local transport for the owner's server commands."""
import json
import socket

SOCKET = "/run/hermesbot-admin/control.sock"
READ = ("status", "memory", "disk", "identity")
HELP = ("用法：/server exec 命令（以 kendas 执行）；"
        "/server status | memory | disk | identity")


def request(operation, command=None):
    if operation not in READ and operation != "exec":
        return {"ok": False, "error": "operation_denied"}
    payload = {"operation": operation}
    if command is not None:
        if len(command.encode()) > 4096:
            return {"ok": False, "error": "command_too_long"}
        payload["command"] = command
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(55)
            client.connect(SOCKET)
            client.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
            with client.makefile("rb") as stream:
                result = json.loads(stream.readline(12000))
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid_reply"}
    except (OSError, ValueError):
        return {"ok": False, "error": "broker_unavailable"}


def reply(argument):
    operation, _, command = argument.partition(" ")
    if operation == "exec" and command.strip():
        result = request(operation, command)
    elif argument in READ:
        result = request(argument)
    else:
        return HELP
    output = str(result.get("output") or "").replace("[CQ:", "［CQ:")
    if "exit_code" in result:
        suffix = "\n输出过长，已截断。" if result.get("truncated") else ""
        return f"kendas · 退出码 {result['exit_code']}\n{output}".rstrip() + suffix
    return f"命令未执行（{result.get('error', '检查服务状态')}）。"
