import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_router import provider

TAG = "kimiflow--v0.1.50"


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _norm(text):
    return re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "TS", text)


class ProviderRunCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.env = {"HOME": "/tmp", "KIMIFLOW_OBSIDIAN_URL": "http://127.0.0.1:9/",
                    "PATH": os.environ.get("PATH", "")}

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = provider.run(argv)
        return code, out.getvalue(), err.getvalue()

    def _obj(self, argv):
        code, out, _ = self._run(argv)
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_default_action_is_status(self):
        # Bash `action="${1:-status}"`: NO args -> "status" (run in a temp cwd).
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            o = self._obj([])
        finally:
            os.chdir(cwd)
        self.assertEqual(o["health"]["status"], "not_detected")

    def test_first_arg_becomes_action_quirk(self):
        # Bash `action="${1:-status}"` then shift: `provider --root X` makes action="--root",
        # shifts it off, then X is parsed as an unknown argument -> exit 2 (faithful quirk).
        code, _, err = self._run(["--root", self.root])
        self.assertEqual(code, 2)
        self.assertIn("unknown argument: %s" % self.root, err)

    def test_setup_plan_loopback(self):
        o = self._obj(["setup", "--root", self.root, "--host", "codex"])
        self.assertEqual(o["status"], "setup_plan")
        self.assertEqual(o["mcp"]["url"], "https://127.0.0.1:27124/mcp/")
        self.assertFalse(o["blocked"])
        self.assertTrue(o["hosts"]["codex"]["enabled"])
        self.assertFalse(o["hosts"]["claude"]["enabled"])      # enabled is host-gated
        self.assertIsInstance(o["hosts"]["claude"]["snippet"], dict)  # snippet is blocked-gated, not host
        self.assertIn("mcpServers", o["hosts"]["claude"]["snippet"])

    def test_setup_plan_blocked_non_loopback(self):
        self._run(["configure", "--root", self.root, "--available", "true",
                   "--path", "https://example.com:8080"])
        o = self._obj(["setup", "--root", self.root])
        self.assertEqual(o["status"], "blocked_non_loopback")
        self.assertTrue(o["blocked"])
        self.assertEqual(o["mcp"]["url"], "")
        self.assertEqual(o["reason"], "non_loopback_url")
        self.assertEqual(o["helpers"]["terminal_setup"], "")
        self.assertEqual(o["helpers"]["setup_script"], "hooks/vault-mcp-setup.sh")  # unconditional

    def test_configure_writes_manifest(self):
        o = self._obj(["configure", "--root", self.root, "--available", "true",
                       "--path", "https://127.0.0.1:27124"])
        self.assertTrue(o["available"])
        with open(os.path.join(self.root, ".kimiflow", "project", "VAULT-PROVIDER.json")) as fh:
            m = json.load(fh)
        self.assertEqual(m["available"], True)
        self.assertEqual(m["vault_path"], "https://127.0.0.1:27124")

    def test_configure_bad_available(self):
        code, _, err = self._run(["configure", "--root", self.root, "--available", "maybe"])
        self.assertEqual(code, 2)
        self.assertIn("must be true or false", err)

    def test_unknown_action(self):
        code, _, err = self._run(["bogus", "--root", self.root])
        self.assertEqual(code, 2)
        self.assertIn("provider action must be", err)

    def test_unknown_arg(self):
        code, _, err = self._run(["status", "--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: provider: unknown argument: --bogus\n")


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "git")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", TAG + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


@unittest.skipUnless(_tools_present(), "bash/jq/git or pinned tag unavailable")
class ProviderParityCase(unittest.TestCase):
    """Grounds the provider subcommand byte-for-byte vs the pinned bash, with a dead detect
    port so detection is deterministic (not_detected) and no token leaves the host."""

    @classmethod
    def setUpClass(cls):
        src = subprocess.run(
            ["git", "-C", _repo_root(), "show", TAG + ":hooks/memory-router.sh"],
            stdout=subprocess.PIPE, check=True,
        ).stdout
        fd, cls.script = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(fd, "wb") as fh:
            fh.write(src)
        # dispatch-free library copy for direct helper calls (everything before dispatch).
        lib = src.decode("utf-8").split('\ncmd="${1:-}"', 1)[0] + "\n"
        fd, cls.lib = tempfile.mkstemp(suffix=".lib.sh")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(lib)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.script)
        os.unlink(cls.lib)

    def _env(self):
        return {"HOME": "/tmp", "KIMIFLOW_OBSIDIAN_URL": "http://127.0.0.1:9/",
                "PATH": os.environ.get("PATH", "")}

    def _bash(self, root, action, tail):
        proc = subprocess.run(["bash", self.script, "provider", action, "--root", root] + tail,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env=self._env())
        return proc.returncode, proc.stdout, proc.stderr

    def _py(self, root, action, tail):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self._env(), clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = provider.run([action, "--root", root] + tail)
        return code, out.getvalue(), err.getvalue()

    def _fresh(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _compare(self, action, tail, files=()):
        rb, rp = self._fresh(), self._fresh()
        bc, bo, be = self._bash(rb, action, tail)
        pc, po, pe = self._py(rp, action, tail)
        label = action + " " + " ".join(tail)
        self.assertEqual(bc, pc, "exit: " + label)
        self.assertEqual(_norm(bo), _norm(po), "stdout: " + label)
        self.assertEqual(_norm(be), _norm(pe), "stderr: " + label)
        for rel in files:
            with self.subTest(file=rel):
                self.assertEqual(_norm(self._read(rb, rel)), _norm(self._read(rp, rel)), rel)

    def _read(self, root, rel):
        try:
            with open(os.path.join(root, rel), "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def test_status(self):
        self._compare("status", [])

    def test_status_pretty(self):
        self._compare("status", ["--pretty"])

    def test_health(self):
        self._compare("health", [])

    def test_setup_all(self):
        self._compare("setup", ["--host", "all"])

    def test_setup_codex(self):
        self._compare("setup", ["--host", "codex"])

    def test_setup_claude(self):
        self._compare("setup", ["--host", "claude"])

    def test_detect(self):
        self._compare("detect", [])

    def test_connect(self):
        self._compare("connect", [])

    def test_configure_true(self):
        self._compare("configure", ["--available", "true", "--path", "https://127.0.0.1:27124"],
                      files=(".kimiflow/project/VAULT-PROVIDER.json",))

    def test_configure_false(self):
        self._compare("configure", ["--available", "false"],
                      files=(".kimiflow/project/VAULT-PROVIDER.json",))

    def test_configure_then_setup_blocked(self):
        # configure a non-loopback path on each root via its own runtime, then setup must
        # block identically (base_url is the non-loopback manifest vault_path).
        rb, rp = self._fresh(), self._fresh()
        cfg = ["--available", "true", "--path", "https://example.com:8443"]
        self._bash(rb, "configure", cfg)
        self._py(rp, "configure", cfg)
        bc, bo, _ = self._bash(rb, "setup", [])
        pc, po, _ = self._py(rp, "setup", [])
        self.assertEqual(bc, pc)
        self.assertEqual(_norm(bo), _norm(po), "blocked setup stdout")

    def test_prefetch_skipped(self):
        self._compare("prefetch", [])

    def test_sync_skipped(self):
        self._compare("sync", [])

    def test_bad_available(self):
        self._compare("configure", ["--available", "maybe"])

    def test_unknown_action(self):
        rb, rp = self._fresh(), self._fresh()
        bc, bo, be = self._bash(rb, "bogus", [])
        pc, po, pe = self._py(rp, "bogus", [])
        self.assertEqual((bc, _norm(be)), (pc, _norm(pe)))

    def _seed_configured(self, root):
        proj = os.path.join(root, ".kimiflow", "project")
        os.makedirs(proj, exist_ok=True)
        with open(os.path.join(proj, "VAULT-PROVIDER.json"), "w") as fh:
            json.dump({"schema_version": 1, "type": "obsidian", "available": True,
                       "mode": "local-first", "vault_path": "https://127.0.0.1:27124",
                       "last_prefetch_at": None, "last_write_at": None,
                       "synced_learning_ids": [], "updated_at": "2026-01-01T00:00:00Z"}, fh)

    def test_prefetch_write(self):
        rb, rp = self._fresh(), self._fresh()
        self._seed_configured(rb)
        self._seed_configured(rp)
        bc, bo, be = self._bash(rb, "prefetch", ["--write"])
        pc, po, pe = self._py(rp, "prefetch", ["--write"])
        self.assertEqual(bc, pc)
        self.assertEqual(_norm(bo), _norm(po), "prefetch --write stdout")
        for rel in (".kimiflow/project/VAULT-PREFETCH.md", ".kimiflow/project/VAULT-PROVIDER.json"):
            self.assertEqual(_norm(self._read(rb, rel)), _norm(self._read(rp, rel)), rel)

    def test_sync_write_empty(self):
        rb, rp = self._fresh(), self._fresh()
        self._seed_configured(rb)
        self._seed_configured(rp)
        bc, bo, be = self._bash(rb, "sync", ["--write"])
        pc, po, pe = self._py(rp, "sync", ["--write"])
        self.assertEqual(bc, pc)
        bash_obj, python_obj = json.loads(_norm(bo)), json.loads(_norm(po))
        self.assertEqual(python_obj["candidates"]["reason_counts"], {})
        del python_obj["candidates"]["reason_counts"]
        self.assertEqual(bash_obj, python_obj, "sync --write stdout except privacy metadata")
        self.assertIn("six-field portable Capsule projection",
                      self._read(rp, ".kimiflow/project/VAULT-SYNC.md"))
        self.assertEqual(_norm(self._read(rb, ".kimiflow/project/VAULT-PROVIDER.json")),
                         _norm(self._read(rp, ".kimiflow/project/VAULT-PROVIDER.json")))

    # markdown writers grounded directly via the dispatch-free lib (controlled handoff JSON),
    # so the non-empty candidates branch is exercised without candidate-survival fixtures.
    def _bash_writer(self, fn, path, handoff):
        shim = 'source "%s"\n%s "$1" "$2"\n' % (self.lib, fn)
        fd, sp = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(fd, "w") as fh:
            fh.write(shim)
        try:
            subprocess.run(["bash", sp, path, json.dumps(handoff)],
                           check=True, env=self._env())
        finally:
            os.unlink(sp)

    def test_sync_markdown_writer_candidates(self):
        long_summary = "bounded " * 40
        handoff = {
            "provider": {"type": "obsidian", "available": True,
                         "health": {"status": "connected_local_only"},
                         "auth": {"status": "unconfigured"}},
            "direct_write_ready": False,
            "candidates": {"count": 3, "exported_count": 2, "omitted_count": 1,
                           "ids": ["cap_a", "cap_b"],
                           "rows": [
                               {"topic": "router", "kind": "learning", "capsule_id": "cap_a",
                                "summary": long_summary, "confidence": "high",
                                "last_verified": "2026-07-20"},
                               {"topic": "general", "kind": "learned", "capsule_id": "cap_b",
                                "summary": "x", "confidence": "medium",
                                "last_verified": "2026-07-20"}]},
        }
        rp = self._fresh()
        ppath = os.path.join(rp, "SYNC.md")
        provider.write_provider_sync_markdown(ppath, handoff)
        rendered = self._read(rp, "SYNC.md")
        self.assertIn("six-field portable Capsule projection", rendered)
        self.assertIn("router · learning · cap_a", rendered)
        self.assertIn("general · learned · cap_b", rendered)
        self.assertIn(long_summary, rendered)
        self.assertIn("confidence: high; last_verified: 2026-07-20", rendered)
        self.assertIn("confidence: medium; last_verified: 2026-07-20", rendered)
        self.assertNotIn("(evidence:", rendered.casefold())
        self.assertNotIn("learn_a", rendered)

    def test_prefetch_markdown_writer(self):
        handoff = {
            "provider": {"type": "obsidian", "available": True,
                         "health": {"status": "connected_local_only"},
                         "auth": {"status": "unconfigured"}},
            "direct_search_ready": False,
            "query": "project memory recall",
        }
        rb, rp = self._fresh(), self._fresh()
        self._bash_writer("write_provider_prefetch_markdown", os.path.join(rb, "PRE.md"), handoff)
        provider.write_provider_prefetch_markdown(os.path.join(rp, "PRE.md"), handoff)
        self.assertEqual(_norm(self._read(rb, "PRE.md")), _norm(self._read(rp, "PRE.md")))


if __name__ == "__main__":
    unittest.main()
