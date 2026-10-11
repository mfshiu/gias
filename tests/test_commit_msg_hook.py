"""commit-msg hook（E-02）：擋下不具描述性的 commit 訊息。"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

HOOK = pathlib.Path(__file__).resolve().parents[1] / ".githooks" / "commit-msg"
SH = shutil.which("sh") or next(
    (p for p in ("C:/Program Files/Git/usr/bin/sh.exe", "C:/Program Files/Git/bin/sh.exe") if pathlib.Path(p).exists()),
    None,
)

pytestmark = pytest.mark.skipif(SH is None, reason="sh is not available")


def _run(tmp_path, message: str) -> subprocess.CompletedProcess:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_bytes(message.encode("utf-8"))
    return subprocess.run([SH, str(HOOK), str(msg)], capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("message", [
    "..", "aaa", "Alisan", "Init 2", "'Init'", "Initial commit", "Fix", "update.", "WIP",
    "............", "aaaaaaaaaaaa", "1234567890", "", "# only a comment\n",
])
def test_non_descriptive_messages_are_rejected(tmp_path, message):
    result = _run(tmp_path, message)
    assert result.returncode == 1
    assert "CONTRIBUTING.md" in result.stderr


@pytest.mark.parametrize("message", [
    "Fix replanning after a closed zone: L-01",
    "Record commit hash for S-04 in issue list\n\nCo-Authored-By: someone",
    "修正封閉區域的重規劃",
    "# Please enter the commit message\nAdd blackboard access tokens: S-01\n",
    "Merge branch 'feature'",
    "Revert \"Something\"",
    "fixup! Add tests",
])
def test_descriptive_messages_pass(tmp_path, message):
    assert _run(tmp_path, message).returncode == 0


def test_long_subject_only_warns(tmp_path):
    result = _run(tmp_path, "Explain " + "very " * 30 + "long subject")
    assert result.returncode == 0 and "warning" in result.stderr


def test_hook_uses_lf_line_endings():
    assert b"\r\n" not in HOOK.read_bytes()
