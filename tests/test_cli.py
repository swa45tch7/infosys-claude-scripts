from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from lm_infosys.cli import main


class CliTests(unittest.TestCase):
    def test_help_exits_zero(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            main(["--help"])
        self.assertEqual(caught.exception.code, 0)

    def test_missing_config_exits_two(self) -> None:
        env = {"PATH": "/usr/bin"}
        stderr = io.StringIO()
        with patch.dict("os.environ", env, clear=True), redirect_stderr(stderr):
            code = main(["health"])
        self.assertEqual(code, 2)
        self.assertIn("LM_ACCOUNT", stderr.getvalue())
        self.assertIn("LM_ACCESS_ID", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
