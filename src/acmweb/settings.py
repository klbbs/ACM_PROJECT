import os
from dataclasses import dataclass, field
from pathlib import Path
from .providers import DEFAULT_HOSTS


def default_data_dir():
    # Preserve the earlier desktop collector's real-history location.
    legacy=Path("data/account-history")
    default=legacy if (legacy/"accounts.sqlite3").is_file() else Path("data")
    return Path(os.getenv("ACM_DATA_DIR",str(default))).resolve()


@dataclass
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    config_path: Path = field(default_factory=lambda: Path(os.getenv("ACM_ACCOUNTS_CONFIG", "account.toml")).resolve())
    static_dir: Path = field(default_factory=lambda: Path(os.getenv("ACM_STATIC_DIR", "frontend/dist")).resolve())
    access_token: str = field(default_factory=lambda: os.getenv("ACM_ACCESS_TOKEN", ""), repr=False)
    production: bool = field(default_factory=lambda: os.getenv("ACM_ENV", "local") == "production")
    enable_codex: bool = field(default_factory=lambda: os.getenv("ACM_ENABLE_CODEX", "true").lower() == "true")
    server_codex: bool = field(default_factory=lambda: os.getenv("ACM_ENABLE_SERVER_CODEX", "false").lower() == "true")
    codex_binary: str = field(default_factory=lambda: os.getenv("ACM_CODEX_BINARY", "codex"))
    multi_user: bool = field(default_factory=lambda: os.getenv("ACM_MULTI_USER", "false").lower() == "true")
    isolated: bool = False
    allow_env_ai_key: bool = True
    ai_allowed_hosts: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip().lower() for x in os.getenv('ACM_AI_ALLOWED_HOSTS',DEFAULT_HOSTS).split(',') if x.strip()))
    allowed_hosts: tuple[str, ...] = field(default_factory=lambda: tuple(x.strip() for x in os.getenv('ACM_ALLOWED_HOSTS','localhost,127.0.0.1,[::1],testserver').split(',') if x.strip()))
    max_users: int = 100
    public_origin: str = field(default_factory=lambda: os.getenv('ACM_PUBLIC_ORIGIN','').rstrip('/'))
    # In multi-user mode this points every isolated workspace at the central
    # moderated translation store while keeping generated translations local.
    translation_database: Path | None = field(default=None, repr=False)
    translation_owner: str = field(default='local', repr=False)

    @property
    def database(self):
        return self.data_dir / "accounts.sqlite3"

    def validate(self):
        if self.public_origin:
            from urllib.parse import urlsplit
            origin = urlsplit(self.public_origin)
            if origin.scheme != 'https' or not origin.hostname or origin.username or origin.password or origin.path or origin.query or origin.fragment or origin.hostname not in self.allowed_hosts:
                raise RuntimeError('ACM_PUBLIC_ORIGIN 必须是 ACM_ALLOWED_HOSTS 中的 HTTPS 站点地址，不含路径或账号')
        if (self.production or self.multi_user) and (len(self.access_token) < 24 or self.access_token == 'replace-with-at-least-24-random-characters'):
            raise RuntimeError("生产或多人环境必须设置至少 24 字符的随机 ACM_ACCESS_TOKEN")
        if self.production:
            self.enable_codex = self.server_codex
        self.data_dir.mkdir(parents=True, exist_ok=True)
