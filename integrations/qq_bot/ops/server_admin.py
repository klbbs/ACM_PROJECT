#!/usr/bin/env python3
"""Dispatch owner commands under the server's unprivileged operator account."""
import grp
import json
import os
import pwd
import socket
import struct
import subprocess
import sys
import uuid

SOCKET = "/run/hermesbot-admin/control.sock"
BASIC = {
    "status": ["/usr/bin/systemctl", "is-active", "hermesbot-gateway",
               "hermesbot-nonebot", "hermesbot-napcat", "acm-lab"],
    "memory": ["/usr/bin/free", "-h"],
    "disk": ["/usr/bin/df", "-h", "/"],
    "identity": ["/usr/bin/id", "-un"],
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


def execute(operation, unit, command=None):
    if operation in BASIC and command is None:
        argv = BASIC[operation]
    elif (operation == "exec" and unit == "hermesbot-nonebot.service"
          and isinstance(command, str) and command.strip()
          and len(command.encode()) <= 4096 and "\x00" not in command):
        argv = ["/bin/bash", "--noprofile", "--norc", "-c", command]
    else:
        return {"ok": False, "error": "operation_denied"}

    unit_name = "hermesbot-command-" + uuid.uuid4().hex + ".service"
    launch = [
        "/usr/bin/systemd-run", "--quiet", "--collect", "--wait", "--pipe",
        "--unit=" + unit_name, "--uid=kendas", "--gid=kendas",
        "--working-directory=/opt/acm-lab",
        "--setenv=HOME=/home/kendas", "--setenv=USER=kendas",
        "--setenv=LOGNAME=kendas", "--setenv=LANG=C.UTF-8",
        "--setenv=PATH=/usr/local/bin:/usr/bin:/bin",
        "--property=UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy ACM_CODEX_PROXY_URL",
        "--property=NoNewPrivileges=yes", "--property=RuntimeMaxSec=40s",
        "--property=TimeoutStopSec=3s", "--property=TasksMax=64",
        "--property=MemoryMax=512M", "--property=ProtectSystem=full",
        "--property=InaccessiblePaths=-/home/kendas/.ssh -/home/kendas/.gnupg -/run/docker.sock -/srv/hermesbot",
        "--", *argv,
    ]
    print(f"operation={operation} caller={unit} account=kendas unit={unit_name}", flush=True)
    data = bytearray()
    total = 0
    with subprocess.Popen(launch, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, cwd="/", env=ENV) as process:
        for chunk in iter(lambda: process.stdout.read1(4096), b""):
            total += len(chunk)
            data.extend(chunk[:max(0, 1600 - len(data))])
        code = process.wait()
    output = data.decode(errors="replace").strip()
    if operation == "status":
        names = BASIC[operation][2:]
        lines = output.splitlines()
        if len(lines) == len(names):
            output = "\n".join(f"{name}: {state}" for name, state in zip(names, lines))
    return {"ok": code == 0, "run_as": "kendas", "exit_code": code,
            "output": output, "truncated": total > len(data)}


def handle(peer, bot_uid):
    peer.settimeout(3)
    unit = source(peer, bot_uid)
    if unit is None:
        return {"ok": False, "error": "caller_denied"}
    with peer.makefile("rb") as request:
        raw = request.readline(24577)
    if len(raw) > 24576:
        return {"ok": False, "error": "invalid_request"}
    body = json.loads(raw)
    if not isinstance(body, dict) or not isinstance(body.get("operation"), str):
        return {"ok": False, "error": "invalid_request"}
    return execute(body["operation"], unit, body.get("command"))


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
                    peer.sendall(json.dumps(response, ensure_ascii=False).encode() + b"\n")
                except OSError:
                    pass


if __name__ == "__main__":
    main()
