from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kimiflow_core import render


class RenderTests(unittest.TestCase):
    def make_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "docs/render/kimiflow/claude").mkdir(parents=True)
        (root / "docs/render/kimiflow/codex").mkdir(parents=True)
        (root / "docs/render/kimiflow/claude/SKILL.md").write_text("claude\n", encoding="utf-8")
        (root / "docs/render/kimiflow/codex/SKILL.md").write_text("codex\n", encoding="utf-8")
        return root

    def test_render_writes_both_host_outputs(self) -> None:
        root = self.make_root()

        changed = render.render(root)

        self.assertEqual(["SKILL.md", "skills/kimiflow/SKILL.md"], changed)
        self.assertEqual("claude\n", (root / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual("codex\n", (root / "skills/kimiflow/SKILL.md").read_text(encoding="utf-8"))

    def test_check_reports_drift_without_writing(self) -> None:
        root = self.make_root()
        (root / "SKILL.md").write_text("manual\n", encoding="utf-8")

        changed = render.render(root, check=True)

        self.assertEqual(["SKILL.md", "skills/kimiflow/SKILL.md"], changed)
        self.assertEqual("manual\n", (root / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
