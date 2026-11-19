"""Exercise the deployed CSV format from a relocated QQ service directory."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


VENDOR = Path(__file__).resolve().parents[1] / "vendor"


@unittest.skipUnless(importlib.util.find_spec("nonebot"), "requires the QQ bridge runtime")
class GroupTriggerConfigTests(unittest.TestCase):
    def test_relocated_config_and_single_group(self):
        for setting, expected in (("12001, 12002", {"12001", "12002"}), ("12003", {"12003"})):
            with self.subTest(setting=setting), tempfile.TemporaryDirectory() as directory:
                Path(directory, ".env").write_text(f"HERMES_AT_ONLY_GROUPS={setting}\n")
                env = dict(os.environ, HOME=directory, PYTHONPATH=str(VENDOR))
                env.pop("HERMES_AT_ONLY_GROUPS", None)
                code = (
                    "import nonebot; nonebot.init(); "
                    "assert nonebot.load_plugin('nonebot_plugin_hermes'); "
                    "from nonebot_plugin_hermes.handlers.message import _AT_ONLY_GROUPS; "
                    f"assert _AT_ONLY_GROUPS == {expected!r}, _AT_ONLY_GROUPS"
                )
                result = subprocess.run([sys.executable, "-c", code], cwd=directory,
                                        env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
