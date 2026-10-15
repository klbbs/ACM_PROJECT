import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("ACM_DATA_DIR", "data")).resolve())
    config_path: Path = field(default_factory=lambda: Path(os.getenv("ACM_ACCOUNTS_CONFIG", "account.toml")).resolve())
    static_dir: Path = field(default_factory=lambda: Path(os.getenv("ACM_STATIC_DIR", "frontend/dist")).resolve())
    access_token: str = field(default_factory=lambda: os.getenv("ACM_ACCESS_TOKEN", ""), repr=False)
    production: bool = field(default_factory=lambda: os.getenv("ACM_ENV", "local") == "production")
    enable_codex: bool = field(default_factory=lambda: os.getenv("ACM_ENABLE_CODEX", "true").lower() == "true")
    codex_binary: str = field(default_factory=lambda: os.getenv("ACM_CODEX_BINARY", "codex"))

    @property
    def database(self):
        return self.data_dir / "accounts.sqlite3"

    def validate(self):
        if self.production and len(self.access_token) < 24:
            raise RuntimeError("生产环境必须设置至少 24 字符的 ACM_ACCESS_TOKEN")
        if self.production:
            self.enable_codex = False
        self.data_dir.mkdir(parents=True, exist_ok=True)
