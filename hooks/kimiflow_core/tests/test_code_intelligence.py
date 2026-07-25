import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from kimiflow_core import code_intelligence


class CodeIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "repo")
        os.mkdir(self.root)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Test"], check=True)
        os.makedirs(os.path.join(self.root, "src"))
        Path(self.root, "src", "core.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        Path(self.root, "src", "caller.py").write_text("from .core import main\n", encoding="utf-8")
        subprocess.run(["git", "-C", self.root, "add", "src"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "fixture"], check=True)
        self.log = os.path.join(self.tmp.name, "provider.log")

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self, stale=False, outside=False, many=False):
        path = os.path.join(self.tmp.name, "fixture-provider")
        script = """#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:] == ['capabilities', '--json']:
    print(json.dumps({'schema_version':1,'name':'fixture-provider','version':'1.0','relations':['caller','definition'],'dirty_workspace':True,'snapshot':True}))
    raise SystemExit(0)
request = json.loads(sys.stdin.readline())
with open(os.environ['PROVIDER_LOG'], 'a') as handle: handle.write('query\\n')
facts = []
for index in range(%d):
    facts.append({'path':%r if index == 0 else 'src/caller.py','start_line':index+1,'end_line':index+1,'symbol':'main','relation':'caller','target':'main','confidence':900000-index,'provenance':'scip'})
print(json.dumps({'schema_version':1,'snapshot_id':('sha256:' + '0'*64) if %r else request['snapshot_id'],'facts':facts}))
""" % (20 if many else 2, "../outside.py" if outside else "src/core.py", stale)
        Path(path).write_text(textwrap.dedent(script), encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return path

    def env(self):
        value = dict(os.environ)
        value["PROVIDER_LOG"] = self.log
        return value

    def test_ineligible_run_has_zero_provider_and_context_overhead(self):
        result = code_intelligence.route(
            self.root, "small", ["src/core.py"], ["cross_file"],
            executable=self.provider(), environ=self.env(),
        )
        self.assertEqual(result["status"], "fallback")
        self.assertFalse(result["provider_invoked"])
        self.assertEqual(result["context"], "")
        self.assertFalse(os.path.exists(self.log))

    def test_code_intelligence_rejects_stale_snapshot_and_falls_back(self):
        result = code_intelligence.route(
            self.root, "large", ["src/core.py"], ["cross_file"],
            executable=self.provider(stale=True), environ=self.env(),
        )
        self.assertEqual(result["status"], "fallback")
        self.assertEqual(result["reason"], "snapshot_mismatch")
        self.assertEqual(result["context"], "")

    def test_code_intelligence_enforces_relation_hop_and_context_budgets(self):
        result = code_intelligence.route(
            self.root, "large", ["src/core.py"], ["caller_impact"],
            executable=self.provider(many=True), relation_types=["caller"], mode="canary",
            k=40, hops=1, max_bytes=2048, max_tokens=64, environ=self.env(),
        )
        self.assertEqual(result["status"], "selected")
        self.assertLessEqual(result["metrics"]["selected_count"], 12)
        self.assertLessEqual(result["metrics"]["context_bytes"], 2048)
        self.assertLessEqual(result["metrics"]["context_bytes"], 512)
        self.assertLessEqual(result["metrics"]["estimated_tokens"], 64)
        self.assertTrue(result["metrics"]["truncated"])
        self.assertNotIn("def main", result["context"])
        self.assertIn("src/core.py:1-1", result["context"])

    def test_outside_root_fact_discards_entire_provider_result(self):
        result = code_intelligence.route(
            self.root, "large", ["src/core.py"], ["architecture"],
            executable=self.provider(outside=True), environ=self.env(),
        )
        self.assertEqual(result["status"], "fallback")
        self.assertIn(result["reason"], ("path_invalid", "path_escape"))
        self.assertEqual(result["context"], "")


if __name__ == "__main__":
    unittest.main()
