#!/usr/bin/env python3
"""Root broker for the fixed BOT maintenance operations."""
import grp
import json
import os
import pwd
import socket
import struct
import subprocess
import sys

SOCKET = "/run/hermesbot-admin/control.sock"
BASIC = {
    "status": ["/usr/bin/systemctl", "is-active", "hermesbot-gateway",
               "hermesbot-nonebot", "hermesbot-napcat", "acm-lab"],
    "memory": ["/usr/bin/free", "-h"],
    "disk": ["/usr/bin/df", "-h", "/"],
    "identity": ["/usr/bin/id", "-un"],
}
ROOT = {
    "restart-gateway": ["/usr/bin/systemctl", "restart", "hermesbot-gateway.service"],
    "check-nginx": ["/usr/sbin/nginx", "-t"],
    "reload-nginx": ["/usr/bin/systemctl", "reload", "nginx.service"],
}
SERVICES = {"hermesbot-nonebot.service", "hermesbot-gateway.service"}
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "HOME": "/"}


def source(peer, bot_uid):
    pid, uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != bot_uid or pid <= 1:
        return None
    for unit in SERVICES:
        result = subprocess.run(
            ["/usr/bin/systemctl", "show", unit, "-p", "MainPID", "--value"],
            capture_output=True, text=True, timeout=4, env=ENV, cwd="/",
        )
        if result.returncode == 0 and result.stdout.strip() == str(pid):
            return unit
    return None


def execute(operation, unit):
    if operation in BASIC:
        argv = ["/usr/sbin/runuser", "-u", "kendas", "--", *BASIC[operation]]
        account = "kendas"
    elif unit == "hermesbot-nonebot.service" and operation in ROOT:
        argv = ROOT[operation]
        account = "root"
    else:
        return {"ok": False, "error": "operation_denied"}
    if operation == "reload-nginx":
        check = subprocess.run(ROOT["check-nginx"], capture_output=True, timeout=10, env=ENV, cwd="/")
        if check.returncode:
            return {"ok": False, "error": "nginx_configuration_invalid"}
    result = subprocess.run(argv, capture_output=True, text=True, env=ENV, cwd="/", timeout=90)
    status = {"ok": result.returncode == 0, "run_as": account, "exit_code": result.returncode}
    if operation in BASIC:
        output = result.stdout.strip()
        if operation == "status":
            names = BASIC[operation][2:]
            output = "\n".join(f"{name}: {state}" for name, state in zip(names, output.splitlines()))
        status["output"] = output[:1400]
    print(f"operation={operation} service={unit} account={account} result={result.returncode}", flush=True)
    return status


def handle(peer, bot_uid):
    peer.settimeout(3)
    unit = source(peer, bot_uid)
    if unit is None:
        return {"ok": False, "error": "caller_denied"}
    with peer.makefile("rb") as request:
        raw = request.readline(129)
    if len(raw) > 128:
        return {"ok": False, "error": "invalid_request"}
    body = json.loads(raw)
    operation = body.get("operation") if isinstance(body, dict) else None
    if not isinstance(operation, str):
        return {"ok": False, "error": "invalid_request"}
    return execute(operation, unit)


def main():
    if os.geteuid() != 0:
        sys.exit("server-admin must run as root")
    bot_gid = grp.getgrnam("hermesbot").gr_gid
    bot_uid = pwd.getpwnam("hermesbot").pw_uid
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        if os.path.exists(SOCKET):
            os.unlink(SOCKET)
        listener.bind(SOCKET)
        os.chown(SOCKET, 0, bot_gid)
        os.chmod(SOCKET, 0o660)
        listener.listen(8)
        while True:
            peer, _ = listener.accept()
            with peer:
                try:
                    response = handle(peer, bot_uid)
                except (OSError, ValueError, subprocess.SubprocessError):
                    response = {"ok": False, "error": "operation_failed"}
                try:
                    peer.sendall(json.dumps(response).encode() + b"\n")
                except OSError:
                    pass


if __name__ == "__main__":
    main()
