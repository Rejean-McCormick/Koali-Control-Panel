"""Branch 08: Control Panel consumes Koali-owned readiness, never authorization."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from koali_control.spaces_integration import KoaliSpacesIntegrationController


class Response:
    def __init__(self, data: bytes = b"", *, status: int = 200, final_url: str | None = None):
        self.data = data
        self.status = status
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, count: int = -1) -> bytes:
        return self.data if count < 0 else self.data[:count]

    def geturl(self):
        return self.final_url or "http://127.0.0.1:4173/apps/konnaxion"


def shell(*, shell_state="ready", module_state="ready", include_health=True, extra=None):
    data = {
        "state": shell_state,
        "modules": [{"module_id": "konnaxion"}],
        "active_space": {"module_instances": [{"module_id": "konnaxion", "enabled": True}]},
    }
    if include_health:
        data["module_health"] = [{"module_id": "konnaxion", "state": module_state,
                                  "reason": None, "affects_shell_state": True}]
    if extra:
        data.update(extra)
    return json.dumps(data).encode("utf8")


def controller(mode="delegated", required=True, log=None):
    config = {
        "products": {"koali-spaces": {"open_url": "http://127.0.0.1:4173/"}},
        "dev_stack": {"koali_spaces_integration": {
            "enabled": True,
            "mode": mode,
            "verify": {"modules": [{"module_id": "konnaxion", "required": required,
                                    "route": "/apps/konnaxion"}]},
        }},
    }
    return KoaliSpacesIntegrationController(config, MagicMock(), log or (lambda _: None))


class ShellReadinessTests(unittest.TestCase):
    def _verify(self, payload, *, mode="delegated", required=True, route_status=200,
                final_url=None):
        observations = []
        c = controller(mode=mode, required=required, log=observations.append)
        responses = [Response(payload), Response(status=route_status, final_url=final_url)]
        with patch("koali_control.spaces_integration.urllib.request.urlopen", side_effect=responses) as urlopen:
            result = c.verify()
        return result, urlopen.call_count, observations

    def test_ready_shell_and_required_runtime_passes(self):
        ok, count, _ = self._verify(shell())
        self.assertTrue(ok)
        self.assertEqual(count, 2)

    def test_http_route_may_not_override_degraded_shell(self):
        ok, count, logs = self._verify(shell(shell_state="degraded"))
        self.assertFalse(ok)
        self.assertEqual(count, 1)
        self.assertTrue(any("shell is not ready" in line for line in logs))

    def test_http_route_may_not_override_failed_required_module(self):
        ok, count, logs = self._verify(shell(module_state="degraded"))
        self.assertFalse(ok)
        self.assertEqual(count, 1)
        self.assertTrue(any("required module not ready" in line for line in logs))

    def test_missing_health_in_delegated_mode_is_not_success(self):
        ok, count, _ = self._verify(shell(include_health=False))
        self.assertFalse(ok)
        self.assertEqual(count, 1)

    def test_explicit_legacy_compatibility_does_not_require_health(self):
        ok, count, _ = self._verify(shell(include_health=False), mode="legacy_projection")
        self.assertTrue(ok)
        self.assertEqual(count, 2)

    def test_optional_unhealthy_module_does_not_block_healthy_shell(self):
        ok, count, _ = self._verify(shell(module_state="degraded"), required=False)
        self.assertTrue(ok)
        self.assertEqual(count, 1)

    def test_bad_or_conflicting_health_cannot_be_used_as_readiness(self):
        payload = shell(extra={"module_health": [
            {"module_id": "konnaxion", "state": "ready"},
            {"module_id": "konnaxion", "state": "degraded"},
        ]})
        self.assertFalse(self._verify(payload)[0])
        self.assertFalse(self._verify(shell(extra={"module_health": "ready"}))[0])
        self.assertFalse(self._verify(b"[]")[0])
        self.assertFalse(self._verify(b"{invalid")[0])

    def test_oversized_state_is_rejected(self):
        oversized = b"{" + b" " * (2 * 1024 * 1024 + 3) + b"}"
        ok, count, logs = self._verify(oversized)
        self.assertFalse(ok)
        self.assertEqual(count, 1)
        self.assertTrue(any("oversized" in item for item in logs))

    def test_route_redirect_to_different_origin_is_not_accepted(self):
        ok, count, logs = self._verify(shell(), final_url="https://example.invalid/welcome")
        self.assertFalse(ok)
        self.assertEqual(count, 2)
        self.assertTrue(any("escaped" in line for line in logs))

    def test_producer_projection_from_actual_koali_spaces(self):
        """Opt-in bilateral test. KOALI_SPACES_SOURCE points to a separate checkout."""
        src = os.environ.get("KOALI_SPACES_SOURCE", "")
        if not src or not shutil.which("node"):
            self.skipTest("Set KOALI_SPACES_SOURCE to run the real producer fixture")
        repo = Path(src)
        if not (repo / "server/ecosystem/shell-state-compiler.mjs").is_file():
            self.skipTest("Koali Spaces source compiler is not available")
        script = '''import { buildDevelopmentShellState } from "./server/ecosystem/shell-state-compiler.mjs";
const discovery = {products: [{id: "konnaxion", moduleId: "konnaxion", publicName: "Konnaxion",
  repoFound:true, integrationReady:true, affectsShellState:true, order:10}], sources: []};
const runtimeStates = new Map([["konnaxion", {state:process.env.KOALI_TEST_STATUS, reason:"test"}]]);
const data = await buildDevelopmentShellState({appRoot:process.cwd(), discovery, runtimeStates});
console.log(JSON.stringify(data));'''
        for runtime_state, expected in (("ready", True), ("degraded", False)):
            with self.subTest(runtime_state=runtime_state):
                env = dict(os.environ, KOALI_TEST_STATUS=runtime_state)
                proc = subprocess.run(["node", "--input-type=module", "-e", script],
                                      cwd=repo, env=env, capture_output=True, text=True,
                                      check=True, timeout=12)
                projection = proc.stdout.strip().encode("utf8")
                ok, count, _ = self._verify(projection)
                self.assertEqual(ok, expected)
                self.assertEqual(count, 2 if expected else 1)


if __name__ == "__main__":
    unittest.main()
