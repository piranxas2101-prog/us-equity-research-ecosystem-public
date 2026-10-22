from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ecosystem_security as security


class EcosystemSecurityTests(unittest.TestCase):
    def test_source_selection_is_bounded_and_ignores_local_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for name in ("src/app.py", ".env.example", "data/private.json",
                         "output/result.json", ".env"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("dummy", encoding="utf-8")
            selected = [path.as_posix() for path in security._source_paths(root)]
            self.assertEqual(selected, [".env.example", "src/app.py"])

    def test_secret_scan_returns_counts_without_secret_material(self) -> None:
        raw = json.dumps([{"RuleID": "generic-api-key", "Secret": "should-never-leave-memory",
                           "Match": "should-never-leave-memory", "File": "src/app.py"}])
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout=raw, stderr="")
        with patch.object(security, "_tool_path", return_value="gitleaks"), \
             patch.object(security, "_source_paths", return_value=[]), \
             patch.object(security, "_run", return_value=result):
            summary = security.scan_secrets(Path("."))
        self.assertEqual(summary["status"], "findings")
        self.assertEqual(summary["history_finding_count"], 1)
        self.assertEqual(summary["working_tree_finding_count"], 1)
        self.assertEqual(summary["finding_rules"], {"generic-api-key": 2})
        self.assertNotIn("should-never-leave-memory", json.dumps(summary))

    def test_preflight_rejects_wildcard_listener_even_with_agent_account(self) -> None:
        host = {"available": True, "isolated_agent_account_present": True,
                "isolated_agent_is_standard": True, "node_version": "v26.9.0",
                "defender_enabled": True, "realtime_protection": True,
                "firewall_profiles": [{"name": "Private", "enabled": True}],
                "selected_listeners": [{"address": "0.0.0.0", "port": 11434}]}
        with patch.object(security, "_windows_state", return_value=host), \
             patch.object(security, "_tracked_sensitive_count", return_value=0), \
             patch.object(security, "_tool_path", return_value=None):
            report = security.preflight(Path("."))
        self.assertFalse(report["openclaw_isolation_ready"])
        self.assertEqual(len(report["wildcard_local_agent_listeners"]), 1)

    def test_preflight_does_not_claim_isolation_without_acl_review(self) -> None:
        host = {"available": True, "isolated_agent_account_present": True,
                "isolated_agent_is_standard": True, "node_version": "v26.9.0",
                "defender_enabled": True, "realtime_protection": True,
                "firewall_profiles": [{"name": "Private", "enabled": True}],
                "selected_listeners": []}
        with patch.object(security, "_windows_state", return_value=host), \
             patch.object(security, "_tracked_sensitive_count", return_value=0), \
             patch.object(security, "_tool_path", return_value=None):
            report = security.preflight(Path("."))
        self.assertTrue(report["openclaw_prerequisites_present"])
        self.assertFalse(report["openclaw_isolation_ready"])

    def test_wsl_path_does_not_require_native_account_or_node(self) -> None:
        host = {"available": True, "isolated_agent_account_present": False,
                "defender_enabled": True, "realtime_protection": True,
                "firewall_profiles": [{"name": "Private", "enabled": True}],
                "selected_listeners": [], "wsl_distro_available": True,
                "node_version": "v24.14.0"}
        with patch.object(security, "_windows_state", return_value=host), \
             patch.object(security, "_tracked_sensitive_count", return_value=0), \
             patch.object(security, "_tool_path", return_value=None):
            report = security.preflight(Path("."))
        self.assertTrue(report["wsl_gateway_prerequisites_present"])
        self.assertFalse(report["native_cli_prerequisites_present"])
        self.assertFalse(report["openclaw_isolation_ready"])

    def test_node_requirement_is_version_specific(self) -> None:
        self.assertFalse(security._node_compatible("v24.14.0"))
        self.assertTrue(security._node_compatible("v24.21.0"))
        self.assertTrue(security._node_compatible("v26.9.0"))
        self.assertFalse(security._node_compatible("v25.0.0"))

    def test_python_runtime_audit_summarizes_without_advisory_text(self) -> None:
        raw = json.dumps({"dependencies": [
            {"name": "example", "version": "1.0", "vulns": [
                {"id": "PYSEC-1", "description": "sensitive prose"}]},
            {"name": "clean", "version": "2.0", "vulns": []}]})
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout=raw, stderr="")
        with tempfile.TemporaryDirectory() as directory:
            package_path = Path(directory) / "site-packages"
            package_path.mkdir()
            with patch.object(security, "_pip_audit_path", return_value="pip-audit"), \
                 patch.object(security.site, "getsitepackages", return_value=[str(package_path)]), \
                 patch.object(security, "_run", return_value=result):
                summary = security.scan_python_runtime(Path(directory))
        self.assertEqual(summary["affected_package_count"], 1)
        self.assertEqual(summary["vulnerability_count"], 1)
        self.assertNotIn("sensitive prose", json.dumps(summary))

    def test_cli_fails_closed_when_scan_has_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "audit.json"
            with patch.object(security, "scan_python_runtime", return_value={"status": "findings"}):
                exit_code = security.main(["python-runtime", "--root", str(root),
                                           "--report", str(report)])
            self.assertEqual(exit_code, 1)
            self.assertTrue(report.is_file())

    def test_workflow_scan_keeps_only_rule_and_severity_counts(self) -> None:
        raw = json.dumps([{"ident": "unpinned-uses", "desc": "secret-looking-details",
                           "determinations": {"severity": "High"}}])
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=raw, stderr="")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".github" / "workflows").mkdir(parents=True)
            with patch.object(security, "_tool_path", return_value="zizmor"), \
                 patch.object(security, "_run", return_value=result):
                summary = security.scan_workflows(root)
        self.assertEqual(summary["by_rule"], {"unpinned-uses": 1})
        self.assertEqual(summary["by_severity"], {"High": 1})
        self.assertNotIn("secret-looking-details", json.dumps(summary))


if __name__ == "__main__":
    unittest.main()
