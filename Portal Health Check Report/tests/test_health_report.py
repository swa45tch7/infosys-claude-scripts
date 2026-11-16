#!/usr/bin/env python3
"""Tests against the in-memory mock portal. No real portal is contacted.

Run from the package folder:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import io
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import docx  # noqa: E402

import lm_health_report  # noqa: E402
from analysis import AMBER, RED, UNKNOWN, assess  # noqa: E402
from collect import collect_facts  # noqa: E402
from lm_client import (  # noqa: E402
    MAX_PAGE_SIZE,
    MAX_RETRIES,
    ApiError,
    ConfigError,
    LmClient,
    LmConfig,
    load_dotenv,
    sign,
)
from mock_portal import DEMO_ACCESS_ID, DEMO_ACCESS_KEY, FakeSession  # noqa: E402
from report_docx import build_report, report_filename  # noqa: E402

SECRET = "Sup3r-Secret-Key-Value=="
ENV_KEYS = ("LM_ACCOUNT", "LM_ACCESS_ID", "LM_ACCESS_KEY", "LM_DOMAIN")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(session: FakeSession, clock: FakeClock = None) -> LmClient:
    clock = clock or FakeClock()
    config = LmConfig("contoso-demo", DEMO_ACCESS_ID, DEMO_ACCESS_KEY)
    return LmClient(config, session=session, sleep=clock.sleep, clock=clock.time)


def doc_text(path: Path) -> str:
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    for section in document.sections:
        parts.extend(p.text for p in section.header.paragraphs)
    return "\n".join(parts)


class SigningTests(unittest.TestCase):
    def test_lmv1_signature_matches_documented_algorithm(self) -> None:
        epoch, path = "1700000000000", "/device/devices"
        digest = hmac.new(b"key", f"GET{epoch}{path}".encode(), hashlib.sha256).hexdigest()
        expected = base64.b64encode(digest.encode()).decode()
        self.assertEqual(sign("key", "GET", epoch, "", path), expected)

    def test_signature_excludes_query_string_and_mock_accepts_it(self) -> None:
        session = FakeSession()
        client = make_client(session)
        client.get("/device/devices", {"size": 1, "filter": 'displayName~"x"'})
        auth = session.calls[-1]["headers"]["Authorization"]
        self.assertTrue(auth.startswith(f"LMv1 {DEMO_ACCESS_ID}:"))
        self.assertEqual(session.calls[-1]["headers"]["X-Version"], "3")

    def test_wrong_key_is_rejected_with_401(self) -> None:
        session = FakeSession(access_key="different")
        with self.assertRaises(ApiError) as ctx:
            make_client(session).get("/metrics/usage")
        self.assertEqual(ctx.exception.status, 401)


class ReadOnlyTests(unittest.TestCase):
    def test_non_get_verbs_are_refused_before_sending(self) -> None:
        session = FakeSession()
        client = make_client(session)
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            with self.assertRaises(ApiError):
                client._request(verb, "/device/devices", None)
        self.assertEqual(session.calls, [])

    def test_full_run_issues_only_get(self) -> None:
        session = FakeSession()
        collect_facts(make_client(session), "Contoso")
        self.assertTrue(session.calls)
        self.assertTrue(all(call["method"] == "GET" for call in session.calls))


class PaginationTests(unittest.TestCase):
    def test_uses_max_page_size_and_collects_everything(self) -> None:
        session = FakeSession()
        items = make_client(session).collect("/device/devices")
        self.assertEqual(len(items), 1240)
        device_calls = [c for c in session.calls if c["path"] == "/device/devices"]
        self.assertEqual([int(c["params"]["size"]) for c in device_calls], [MAX_PAGE_SIZE, MAX_PAGE_SIZE])
        self.assertEqual([int(c["params"]["offset"]) for c in device_calls], [0, 1000])

    def test_no_request_exceeds_page_limit(self) -> None:
        session = FakeSession()
        collect_facts(make_client(session), "Contoso")
        self.assertTrue(all(int(c["params"].get("size", 0)) <= MAX_PAGE_SIZE for c in session.calls))


class RateLimitTests(unittest.TestCase):
    def test_429_backs_off_using_retry_after_then_succeeds(self) -> None:
        session = FakeSession(throttle={"/setting/collector/collectors": 2}, retry_after="5")
        clock = FakeClock()
        client = make_client(session, clock)
        items = client.collect("/setting/collector/collectors")
        self.assertEqual(len(items), 14)
        self.assertEqual(client.limiter.throttle_events, 2)
        self.assertEqual(len(clock.sleeps), 2)
        self.assertTrue(all(s >= 5 for s in clock.sleeps), clock.sleeps)
        self.assertTrue(all(s <= 61 for s in clock.sleeps), clock.sleeps)

    def test_429_without_retry_after_uses_rate_limit_window(self) -> None:
        session = FakeSession(throttle={"/metrics/usage": 1}, retry_after=None, window=12)
        clock = FakeClock()
        make_client(session, clock).get("/metrics/usage")
        self.assertEqual(len(clock.sleeps), 1)
        self.assertGreaterEqual(clock.sleeps[0], 12)

    def test_429_backoff_grows_and_is_capped(self) -> None:
        session = FakeSession(throttle={"/metrics/usage": MAX_RETRIES - 1}, retry_after="0")
        clock = FakeClock()
        make_client(session, clock).get("/metrics/usage")
        self.assertEqual(len(clock.sleeps), MAX_RETRIES - 1)
        self.assertLess(clock.sleeps[0], clock.sleeps[-1])
        self.assertTrue(all(s <= 61 for s in clock.sleeps))

    def test_persistent_429_becomes_a_reported_gap_not_a_crash(self) -> None:
        session = FakeSession(throttle={"/setting/admins": 99})
        facts = collect_facts(make_client(session), "Contoso")
        self.assertIn("users", facts.gaps)
        self.assertIn("rate-limiting", facts.gaps["users"])
        admin_calls = [c for c in session.calls if c["path"] == "/setting/admins"]
        self.assertEqual(len(admin_calls), MAX_RETRIES)
        check = assess(facts).check("users")
        self.assertEqual(check.status, UNKNOWN)

    def test_paces_requests_when_remaining_quota_is_low(self) -> None:
        session = FakeSession(rate_limit=4, window=8)
        clock = FakeClock()
        client = make_client(session, clock)
        for _ in range(4):
            client.get("/metrics/usage")
        self.assertTrue(clock.sleeps, "expected pacing sleeps once remaining dropped")
        self.assertEqual(client.limiter.throttle_events, 0)

    def test_transient_5xx_is_retried(self) -> None:
        session = FakeSession(errors={"/report/reports": [503, 502]})
        clock = FakeClock()
        items = make_client(session, clock).collect("/report/reports")
        self.assertEqual(len(items), 45)
        self.assertEqual(len(clock.sleeps), 2)


class DegradationTests(unittest.TestCase):
    def test_403_on_one_area_is_explained_and_run_continues(self) -> None:
        session = FakeSession(errors={"/setting/accesslogs": [403]})
        facts = collect_facts(make_client(session), "Contoso")
        self.assertIn("audit", facts.gaps)
        self.assertIn("permission", facts.gaps["audit"])
        self.assertIn("devices", facts.values)
        with tempfile.TemporaryDirectory() as tmp:
            path = build_report(assess(facts), Path(tmp))
            text = doc_text(path)
        self.assertIn("View permission", text)
        self.assertIn("Audit log and discovery churn", text)

    def test_missing_endpoint_404_is_explained(self) -> None:
        session = FakeSession()
        del session.data["/setting/integrations"]
        facts = collect_facts(make_client(session), "Contoso")
        self.assertIn("not available", facts.gaps["integrations"])


class ScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assessment = assess(collect_facts(make_client(FakeSession()), "Contoso", {"devices": 1500}))

    def test_overall_and_key_statuses(self) -> None:
        a = self.assessment
        self.assertEqual(a.overall, AMBER)
        self.assertEqual(a.check("alerting").status, RED)
        self.assertEqual(a.check("collectors").status, AMBER)
        self.assertEqual(a.check("datapoint_collection").status, UNKNOWN)
        self.assertEqual(a.check("logs").status, UNKNOWN)

    def test_contract_headroom(self) -> None:
        self.assertIn("278", self.assessment.check("core_adoption").evidence)

    def test_without_contract_core_adoption_says_so(self) -> None:
        a = assess(collect_facts(make_client(FakeSession()), "Contoso"))
        self.assertEqual(a.check("core_adoption").status, UNKNOWN)
        self.assertIn("--contract-devices", a.check("core_adoption").evidence)

    def test_top_alert_sources(self) -> None:
        self.assertEqual(self.assessment.top_alert_sources[0], ("Wireless AP Status", 260))


class CredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.pop(k) for k in ENV_KEYS if k in os.environ}
        self._cwd = os.getcwd()

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self._saved)

    def test_dotenv_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text(
                "LM_ACCOUNT=https://acme.logicmonitor.com\nLM_ACCESS_ID=abc\nLM_ACCESS_KEY='k=='\n", encoding="utf-8")
            os.chdir(tmp)
            load_dotenv()
            config = LmConfig.from_env(interactive=False)
        self.assertEqual(config.account, "acme")
        self.assertEqual(config.access_key, "k==")
        self.assertNotIn("k==", repr(config))

    def test_missing_credentials_lists_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            with self.assertRaises(ConfigError) as ctx:
                LmConfig.from_env(interactive=False)
        for key in ("LM_ACCOUNT", "LM_ACCESS_ID", "LM_ACCESS_KEY"):
            self.assertIn(key, str(ctx.exception))

    def test_interactive_prompt_fallback(self) -> None:
        with mock.patch("sys.stdin") as stdin, \
                mock.patch("builtins.input", side_effect=["acme", "abc"]), \
                mock.patch("getpass.getpass", return_value="hidden-key"), \
                contextlib.redirect_stdout(io.StringIO()):
            stdin.isatty.return_value = True
            config = LmConfig.from_env(interactive=True)
        self.assertEqual((config.account, config.access_id, config.access_key), ("acme", "abc", "hidden-key"))


class EndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.pop(k) for k in ENV_KEYS if k in os.environ}

    def tearDown(self) -> None:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self._saved)

    def _run(self, argv, session):
        out, err, logs = io.StringIO(), io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(logs)
        logging.getLogger().addHandler(handler)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = lm_health_report.main(argv, session=session)
        finally:
            logging.getLogger().removeHandler(handler)
        return code, out.getvalue(), err.getvalue(), logs.getvalue()

    def test_real_mode_with_env_credentials_writes_report_and_never_leaks_secret(self) -> None:
        os.environ.update({"LM_ACCOUNT": "contoso-demo", "LM_ACCESS_ID": "id-123", "LM_ACCESS_KEY": SECRET})
        session = FakeSession(access_id="id-123", access_key=SECRET)
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err, logs = self._run(
                ["--no-prompt", "--verbose", "--customer", "Contoso Manufacturing", "--out-dir", tmp], session)
            self.assertEqual(code, 0, err)
            files = list(Path(tmp).glob("*.docx"))
            self.assertEqual(len(files), 1)
            text = doc_text(files[0])
        expected = report_filename("Contoso Manufacturing", datetime.now())
        self.assertEqual(files[0].name, expected)
        for blob in (out, err, logs, text):
            self.assertNotIn(SECRET, blob)
        for heading in ("Executive summary", "Portal adoption and health scorecard",
                        "What is good, what is weak and what is missing", "Module and capability usage",
                        "Edwin AI readiness", "30 / 60 / 90-day action plan",
                        "Sources, assumptions and validation points", "Appendix A - Portal evidence",
                        "Appendix B - Check coverage"):
            self.assertIn(heading, text)

    def test_bad_credentials_exit_2_with_guidance(self) -> None:
        os.environ.update({"LM_ACCOUNT": "contoso-demo", "LM_ACCESS_ID": "id-123", "LM_ACCESS_KEY": SECRET})
        session = FakeSession(access_id="id-123", access_key="something-else")
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err, logs = self._run(["--no-prompt", "--out-dir", tmp], session)
            self.assertEqual(list(Path(tmp).glob("*.docx")), [])
        self.assertEqual(code, 2)
        self.assertIn("401", err)
        self.assertNotIn(SECRET, out + err + logs)

    def test_missing_credentials_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(lm_health_report, "load_dotenv"):
            code, out, err, _ = self._run(["--no-prompt", "--out-dir", tmp], FakeSession())
        self.assertEqual(code, 2)
        self.assertIn("LM_ACCESS_KEY", err)

    def test_demo_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err, _ = self._run(["--demo", "--out-dir", tmp], None)
            self.assertEqual(code, 0, err)
            files = list(Path(tmp).glob("Contoso_Manufacturing_LM_Envision_Health_Check_*.docx"))
            self.assertEqual(len(files), 1)
            docx.Document(str(files[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
