import os, re, tempfile, unittest
from memory_router import paths, text, clock

class TestPaths(unittest.TestCase):
    def test_rel_path_strips_root_prefix(self):
        self.assertEqual(paths.rel_path("/a/b", "/a/b/c/d.txt"), "c/d.txt")
    def test_rel_path_equal_root_is_dot(self):
        self.assertEqual(paths.rel_path("/a/b", "/a/b"), ".")
    def test_rel_path_outside_root_unchanged(self):
        self.assertEqual(paths.rel_path("/a/b", "/x/y"), "/x/y")
    def test_rows_path_user_and_default(self):
        self.assertEqual(paths.rows_path_for_scope("/r", "user"), "/r/.kimiflow/project/USER.jsonl")
        self.assertEqual(paths.rows_path_for_scope("/r", "profile"), "/r/.kimiflow/project/USER.jsonl")
        self.assertEqual(paths.rows_path_for_scope("/r", "project"), "/r/.kimiflow/project/LEARNINGS.jsonl")
        self.assertEqual(paths.rows_path_for_scope("/r", "anything"), "/r/.kimiflow/project/LEARNINGS.jsonl")
    def test_id_prefix(self):
        self.assertEqual(paths.id_prefix_for_scope("user"), "user")
        self.assertEqual(paths.id_prefix_for_scope("profile"), "user")
        self.assertEqual(paths.id_prefix_for_scope("project"), "learn")

class TestText(unittest.TestCase):
    def test_slugify_basic(self):
        self.assertEqual(text.slugify("My Topic!"), "my-topic")
    def test_slugify_collapses_and_trims(self):
        self.assertEqual(text.slugify("  --Hello___World--  "), "hello-world")
    def test_slugify_truncates_40(self):
        self.assertEqual(text.slugify("a" * 50), "a" * 40)
    def test_slugify_all_nonalnum_is_empty(self):
        self.assertEqual(text.slugify("!!!"), "")
    def test_sql_quote_doubles_single_quotes(self):
        self.assertEqual(text.sql_quote("it's a 'test'"), "it''s a ''test''")
    def test_word_count_file(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "f.txt")
        with open(p, "w") as f:
            f.write("one two   three\nfour\n")
        self.assertEqual(text.word_count_file(p), 4)
    def test_word_count_missing_is_zero(self):
        self.assertEqual(text.word_count_file("/no/such/file"), 0)

class TestClock(unittest.TestCase):
    def test_iso_now_format(self):
        self.assertRegex(clock.iso_now(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    def test_date_now_format(self):
        self.assertRegex(clock.date_now(), r"^\d{4}-\d{2}-\d{2}$")

if __name__ == "__main__":
    unittest.main()
