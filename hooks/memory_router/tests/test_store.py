import json, os, shutil, tempfile, unittest
from memory_router import store

class TestStore(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def test_atomic_write_creates_file_with_content(self):
        p = os.path.join(self.d, "out.txt")
        store.atomic_write(p, "hello\n")
        with open(p) as f:
            self.assertEqual(f.read(), "hello\n")

    def test_atomic_write_leaves_no_tmp_siblings(self):
        p = os.path.join(self.d, "out.txt")
        store.atomic_write(p, "x")
        siblings = [n for n in os.listdir(self.d) if n != "out.txt"]
        self.assertEqual(siblings, [])

    def test_atomic_write_refuses_symlink_target(self):
        real = os.path.join(self.d, "real.txt")
        link = os.path.join(self.d, "link.txt")
        store.atomic_write(real, "orig")
        os.symlink(real, link)
        with self.assertRaises(ValueError):
            store.atomic_write(link, "evil")
        with open(real) as f:
            self.assertEqual(f.read(), "orig")  # untouched

    def test_local_guard_refuses_parent_swap_without_external_write(self):
        project = os.path.join(self.d, "project")
        outside = os.path.join(self.d, "outside")
        os.makedirs(project)
        os.makedirs(outside)
        target = os.path.join(project, "state.json")
        outside_target = os.path.join(outside, "state.json")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("original")
        with open(outside_target, "w", encoding="utf-8") as handle:
            handle.write("outside")
        original_project = project + "-original"
        with store.local_path_guard(self.d, project):
            os.rename(project, original_project)
            os.symlink(outside, project)
            with self.assertRaises(store.ConcurrentWriteError):
                store.atomic_write(target, "changed")
        with open(outside_target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "outside")
        with open(os.path.join(original_project, "state.json"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "original")

    def test_source_generation_rejects_reused_identity_with_new_metadata(self):
        identity = (1, 2)
        before = (identity, b"same", 0o644, (4, 100))
        replacement = (identity, b"same", 0o644, (4, 101))
        self.assertFalse(store._same_source_generation(replacement, before))

    def test_source_generation_allows_in_place_permission_update(self):
        identity = (1, 2)
        before = (identity, b"same", 0o644, (4, 100))
        chmod = (identity, b"same", 0o600, (4, 100))
        self.assertTrue(store._same_source_generation(chmod, before))

    def test_read_text_missing_returns_default(self):
        self.assertEqual(store.read_text(os.path.join(self.d, "nope"), "d"), "d")

    def test_read_jsonl_skips_blank_and_invalid(self):
        p = os.path.join(self.d, "x.jsonl")
        with open(p, "w") as f:
            f.write('{"a":1}\n\n  \nnot json\n{"b":2}\n')
        self.assertEqual(store.read_jsonl(p), [{"a": 1}, {"b": 2}])

    def test_read_jsonl_with_lines_keeps_raw_and_pairs_rows(self):
        p = os.path.join(self.d, "x.jsonl")
        with open(p, "w") as f:
            f.write('{"a":1}\n\nnot json\n5\n{"b":2}\n')
        self.assertEqual(store.read_jsonl_with_lines(p), [
            ('{"a":1}', {"a": 1}),
            ("", None),
            ("not json", None),
            ("5", None),          # non-dict JSON stays raw-only (rewrite keeps it verbatim)
            ('{"b":2}', {"b": 2}),
        ])

    def test_read_jsonl_with_lines_missing_file(self):
        self.assertEqual(store.read_jsonl_with_lines(os.path.join(self.d, "nope")), [])

if __name__ == "__main__":
    unittest.main()
