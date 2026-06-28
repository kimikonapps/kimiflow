import io, os, tempfile, unittest, contextlib
from memory_router import classify
from memory_router.__main__ import main

class TestClassifyText(unittest.TestCase):
    def c(self, text):
        return classify.classify_text(text)["classification"]

    def test_security_sensitive_forces_project_memory_high(self):
        r = self.c("found an sql injection and a leaked api token in the env")
        self.assertEqual(r["sensitivity"], "security")
        self.assertEqual(r["target"], "project_memory")
        self.assertEqual(r["confidence"], "high")
        self.assertFalse(r["vault_allowed"])
        self.assertTrue(r["sanitized_required"])
        self.assertIn("security_sensitive", r["reasons"])

    def test_private_detail(self):
        r = self.c("the file lives under /Users/sr and is customer specific data here")
        self.assertEqual(r["sensitivity"], "private")
        self.assertTrue(r["vault_allowed"])
        self.assertTrue(r["sanitized_required"])
        self.assertIn("private_or_local_detail", r["reasons"])

    def test_too_small_or_trivial_by_wordcount(self):
        r = self.c("tiny note")  # < 4 words
        self.assertEqual(r["target"], "skip")
        self.assertEqual(r["confidence"], "high")
        self.assertIn("too_small_or_trivial", r["reasons"])

    def test_trivial_keyword_single_line(self):
        r = self.c("done")
        self.assertEqual(r["target"], "skip")

    def test_repo_doc_candidate_allowed_when_normal(self):
        r = self.c("update the README and the onboarding documentation for new devs")
        self.assertEqual(r["target"], "repo_doc_candidate")
        self.assertTrue(r["repo_doc_allowed"])
        self.assertIn("documentation_candidate", r["reasons"])

    def test_vault_long_term(self):
        r = self.c("a cross-project preference to always remember this lesson going forward")
        self.assertEqual(r["target"], "vault")
        self.assertIn("long_term_or_cross_project", r["reasons"])

    def test_project_memory_reusable(self):
        r = self.c("the build and release convention for this kimiflow hook is important")
        self.assertEqual(r["target"], "project_memory")
        self.assertIn("project_reusable", r["reasons"])

    def test_security_override_preserves_target_block_reason(self):
        r = self.c("found an sql injection in the readme documentation for devs")
        self.assertEqual(r["sensitivity"], "security")
        self.assertEqual(r["target"], "project_memory")  # overridden from repo_doc_candidate
        self.assertIn("security_sensitive", r["reasons"])
        self.assertIn("documentation_candidate", r["reasons"])  # target-block reason survives

    def test_reasons_order_sensitivity_then_target(self):
        # private + documentation: private reason appended first, then target reason
        r = self.c("publish-safe documentation about a customer onboarding flow under /home/x")
        self.assertEqual(r["reasons"][0], "private_or_local_detail")
        self.assertIn("documentation_candidate", r["reasons"][1:])

    def test_schema_shape_and_key_order(self):
        obj = classify.classify_text("the build convention for this project is important here")
        self.assertEqual(obj["schema_version"], 1)
        self.assertEqual(
            list(obj["classification"].keys()),
            ["target", "sensitivity", "confidence", "reasons",
             "vault_allowed", "repo_doc_allowed", "sanitized_required"],
        )

    def test_default_no_pattern_run_only(self):
        # ≥4 words, no pattern matches → default branch: run_only / normal / []
        r = classify.classify_text("the quick brown fox jumped over")["classification"]
        self.assertEqual(r["target"], "run_only")
        self.assertEqual(r["sensitivity"], "normal")
        self.assertEqual(r["reasons"], [])

class TestClassifyRun(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = classify.run(argv)
        return code, out.getvalue(), err.getvalue()

    def test_text_outputs_compact_json_newline(self):
        code, out, err = self._run(["--text", "the build convention for this kimiflow project matters"])
        self.assertEqual(code, 0)
        self.assertTrue(out.endswith("\n"))
        self.assertIn('"schema_version":1', out)   # compact: no space after colon
        self.assertEqual(err, "")

    def test_pretty_indents(self):
        code, out, _ = self._run(["--pretty", "--text", "the build convention here matters a lot"])
        self.assertEqual(code, 0)
        self.assertIn('"schema_version": 1', out)  # pretty: space after colon

    def test_unknown_arg_dies_exit_2(self):
        code, out, err = self._run(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: classify: unknown argument: --bogus\n")
        self.assertEqual(out, "")

    def test_missing_text_and_input_dies(self):
        code, _, err = self._run([])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: classify requires --input or --text\n")

    def test_input_not_found_dies(self):
        code, _, err = self._run(["--input", "/no/such/file/here.md"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: input not found: /no/such/file/here.md\n")

    def test_input_file_first_160_lines(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "in.md")
        with open(p, "w") as f:
            f.write("the build convention for this kimiflow project is important\n" + "x\n" * 400)
        code, out, _ = self._run(["--input", p])
        self.assertEqual(code, 0)
        self.assertIn('"target":"project_memory"', out)

    def test_dispatch_registration(self):
        # main() routes "classify" into classify.run
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["classify", "--text", "the build convention here matters a lot"])
        self.assertEqual(code, 0)
        self.assertIn('"schema_version":1', out.getvalue())

    def test_default_compact_reasons_empty(self):
        # compact JSON must render "reasons":[] (not omitted, not "reasons": [])
        import json
        code, out, err = self._run(["--text", "the quick brown fox jumped over"])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["classification"]["target"], "run_only")
        self.assertEqual(result["classification"]["sensitivity"], "normal")
        self.assertEqual(result["classification"]["reasons"], [])
        self.assertIn('"reasons":[]', out)

    def test_crlf_newline_preserved(self):
        # _read_input_head must NOT strip \r (Bash sed keeps raw bytes).
        # A file with CRLF lines: if \r were stripped, 'done' on its own line would
        # match _TRIVIAL → target=="skip". With \r preserved, 'done\r' does not match
        # ^done$ in MULTILINE ($ does not anchor before \r), so target!="skip".
        import json
        d = tempfile.mkdtemp()
        p = os.path.join(d, "crlf.md")
        with open(p, "wb") as f:
            f.write(b"four words here present\r\ndone\r\n")
        code, out, _ = self._run(["--input", p])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertNotEqual(
            result["classification"]["target"], "skip",
            r"\r was stripped — CRLF not preserved; _TRIVIAL incorrectly matched 'done'",
        )

if __name__ == "__main__":
    unittest.main()
