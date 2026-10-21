"""Run the real login UI locally with temporary, empty account storage."""
import argparse
import os
import tempfile
from pathlib import Path

import uvicorn

from acmweb.app import create_app
from acmweb.settings import Settings


def main():
    parser = argparse.ArgumentParser(description="启动仅本机可访问、退出后清除数据的平台登录验收页面")
    parser.add_argument("--port", type=int, default=18770)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口必须在 1024—65535 之间")
    root = Path(__file__).resolve().parent.parent
    if not (root / "frontend/dist/index.html").is_file():
        parser.error("请先在 frontend 目录执行 npm run build")
    with tempfile.TemporaryDirectory(prefix="acm-human-login-") as directory:
        os.chmod(directory, 0o700)
        data = Path(directory)
        # `isolated` denotes an authenticated multi-user child, not a standalone
        # temporary workspace. Such a child requires its parent's owner context.
        settings = Settings(
            data_dir=data,
            config_path=data / "absent.toml",
            static_dir=root / "frontend/dist",
            access_token="",
            production=False,
            multi_user=False,
            isolated=False,
            enable_codex=False,
            server_codex=False,
            allow_env_ai_key=False,
        )
        print(f"本地临时登录验收：http://127.0.0.1:{args.port}/#platform-guide", flush=True)
        print("使用空账号配置；Ctrl+C 退出后清除本次数据。", flush=True)
        uvicorn.run(
            create_app(settings), host="127.0.0.1", port=args.port,
            log_level="warning", access_log=False,
        )


if __name__ == "__main__":
    main()
