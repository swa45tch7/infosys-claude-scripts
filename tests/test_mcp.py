from __future__ import annotations

import json
import unittest

from lm_infosys.mcp_server import _handle


class McpTests(unittest.TestCase):
    def test_initialize_and_list_tools(self) -> None:
        init = _handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert init is not None
        self.assertEqual(init["result"]["serverInfo"]["name"], "lm-infosys")
        listed = _handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert listed is not None
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(
            names,
            {"lm_health", "lm_alerts", "lm_devices", "lm_collectors", "lm_device"},
        )
        self.assertTrue(json.dumps(listed))


if __name__ == "__main__":
    unittest.main()
