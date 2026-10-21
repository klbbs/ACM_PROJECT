"""Private writable Chromium configuration for read-only server containers."""
import os
from pathlib import Path


def browser_environment(directory):
    """Keep Chromium/Crashpad data out of the service user's shared home.

    A Playwright user-data-dir alone does not relocate Linux's XDG crash
    configuration. The caller owns this 0700 directory until Chromium stops.
    """
    root = Path(directory)
    root.chmod(0o700)
    config, cache = root / 'config', root / 'cache'
    for path in (config, cache):
        path.mkdir(mode=0o700, exist_ok=True)
    return dict(os.environ, XDG_CONFIG_HOME=str(config),
                CHROME_CONFIG_HOME=str(config), XDG_CACHE_HOME=str(cache))
