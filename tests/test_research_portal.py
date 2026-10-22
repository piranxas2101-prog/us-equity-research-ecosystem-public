import json
import tempfile
import unittest
from pathlib import Path
import research_portal

class ResearchPortalTests(unittest.TestCase):
    def test_builds_static_index_without_server(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / "output" / "runs" / "run_1"
            run.mkdir(parents=True)
            (run / "manifest.json").write_text(json.dumps({"run_id":"run_1","artifacts":[]}), encoding="utf-8")
            output = research_portal.build(root)
            self.assertTrue(output.exists())
            self.assertIn("run_1", output.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
