"""Local transport for the server's fixed maintenance commands."""
import json
import socket

SOCKET = "/run/hermesbot-admin/control.sock"
READ = ("status", "memory", "disk", "identity")
WRITE = ("restart-gateway", "check-nginx", "reload-nginx")
HELP = ("用法：/server status | memory | disk | identity | "
        "check-nginx | reload-nginx | restart-gateway")


def request(operation):
    if operation not in READ + WRITE:
        return {"ok": False, "error": "operation_denied"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(95)
            client.connect(SOCKET)
            client.sendall(json.dumps({"operation": operation}).encode() + b"\n")
            with client.makefile("rb") as stream:
                result = json.loads(stream.readline(4096))
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid_reply"}
    except (OSError, ValueError):
        return {"ok": False, "error": "broker_unavailable"}


def reply(operation):
    if operation not in READ + WRITE:
        return HELP
    result = request(operation)
    if result.get("ok"):
        return result.get("output") or f"已完成 {operation}（{result['run_as']}）。"
    return result.get("output") or f"{operation} 未完成（{result.get('error', '检查服务状态')}）。"
