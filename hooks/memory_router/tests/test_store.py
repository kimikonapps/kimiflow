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
