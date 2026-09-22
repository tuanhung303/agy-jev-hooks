"""Offline tests for the JevGrep port source layer: ignore rules, chunking,
credential quarantine, inventory and preparation."""
import os

import pytest

from mcp.jev.grep.authorization import AuthorizedRoot, UnauthorizedPathError, assert_safe_relative_path
from mcp.jev.grep.chunker import chunk_snapshot, uncovered_non_blank_lines
from mcp.jev.grep.ignore_rules import is_ignored, parse_ignore_file
from mcp.jev.grep.inventory import InventoryOptions, inventory_scope
from mcp.jev.grep.line_windows import UnsupportedLongLine, line_windows
from mcp.jev.grep.prepare import PrepareOptions, find_credential_pattern, prepare_scope
from mcp.jev.grep.snapshot import SnapshotError, create_snapshot


def test_safe_relative_path_rejects_traversal_and_absolute():
    assert assert_safe_relative_path("src/app.ts") == "src/app.ts"
    assert assert_safe_relative_path("./src//app.ts") == "src/app.ts"
    for bad in ("../etc", "/etc", "a/../../b", "nul.txt", "x.", "with space."):
        with pytest.raises(UnauthorizedPathError):
            assert_safe_relative_path(bad)


def test_ignore_rules_narrowing_and_precedence():
    root = parse_ignore_file("*.log\nbuild/\n!keep.log\n", "")
    assert is_ignored((root,), "debug.log", False).ignored
    assert not is_ignored((root,), "keep.log", False).ignored  # negation re-includes
    assert is_ignored((root,), "build", True).ignored
    assert not is_ignored((root,), "build", False).ignored  # directory-only rule

    narrowing = parse_ignore_file("!secret.txt\n", "", narrowing_only=True)
    assert len(narrowing.rules) == 0  # negations dropped: only narrows

    nested = parse_ignore_file("*.tmp\n", "src")
    assert is_ignored((root, nested), "src/notes.tmp", False).ignored
    assert not is_ignored((root, nested), "docs/notes.tmp", False).ignored


def test_line_windows_respect_limits_and_report_oversized_lines():
    text = "\n".join(f"line {index} content" for index in range(300)) + "\n"
    windows = line_windows({"path": "a.txt", "text": text, "sha256": "0" * 64})
    assert isinstance(windows, list) and windows
    assert windows[0].start_line == 1
    # every non-blank line belongs to at least one window
    covered = set()
    for window in windows:
        covered.update(range(window.start_line, window.end_line + 1))
    assert set(range(1, 301)) <= covered

    huge = "x" * 20_000
    result = line_windows({"path": "big.txt", "text": huge, "sha256": "0" * 64})
    assert isinstance(result, UnsupportedLongLine) and result.line == 1


def test_snapshot_refuses_binary_and_invalid_utf8():
    with pytest.raises(SnapshotError) as binary:
        create_snapshot("a.bin", "/tmp/a.bin", b"hello\x00world")
    assert binary.value.refusal == "binary"
    with pytest.raises(SnapshotError) as encoding:
        create_snapshot("a.txt", "/tmp/a.txt", b"\xff\xfe")
    assert encoding.value.refusal == "unsupported_encoding"


def test_snapshot_slice_lines_round_trips_exact_text():
    data = b"alpha\r\nbeta\n\ngamma"
    snapshot = create_snapshot("m.txt", "/tmp/m.txt", data)
    assert snapshot.line_count == 4
    assert snapshot.slice_lines(1, 2).text == "alpha\r\nbeta\n"
    assert uncovered_non_blank_lines(snapshot, []) == [1, 2, 4]


def test_credential_patterns_quarantine():
    assert find_credential_pattern("api_key = \"0123456789abcdef0123\"") == "assigned_secret"
    assert find_credential_pattern("-----BEGIN RSA PRIVATE KEY-----") == "private_key_block"
    assert find_credential_pattern("plain ordinary source code") is None


def _fixture_repo(root):
    (root / "src").mkdir()
    (root / "src" / "handler.py").write_text(
        "def handle_session_expiry(session):\n"
        "    # expire sessions after timeout\n"
        "    if session.age > 3600:\n"
        "        session.expire()\n"
        "    return session\n", encoding="utf-8")
    (root / "src" / "notes.txt").write_text("session expiry rules live here\n", encoding="utf-8")
    (root / "dist").mkdir()
    (root / "dist" / "bundle.js").write_text("generated output\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (root / "leak.py").write_text('password = "0123456789abcdef0123"\n', encoding="utf-8")
    (root / "empty.py").write_text("", encoding="utf-8")
    (root / "weird.py").write_text("x = " + "a" * 20_000 + "\n", encoding="utf-8")
    (root / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
    (root / "skip.ignored").write_text("ignored content\n", encoding="utf-8")


def test_inventory_and_preparation_exclusions(tmp_path):
    _fixture_repo(tmp_path)
    root = AuthorizedRoot.open(str(tmp_path))
    options = InventoryOptions(respect_gitignore=True, max_file_bytes=1_048_576)
    inventory = inventory_scope(root, (".",), options)
    reasons = {entry.relative_path: entry.reason for entry in inventory.excluded}
    dir_reasons = {entry.relative_path: entry.reason for entry in inventory.excluded_directories}
    assert dir_reasons["dist"] == "build_output"  # pruned: its descendants stay unknown
    assert reasons[".env"] == "credential_file"
    assert reasons["empty.py"] == "empty"
    assert reasons["skip.ignored"] == "gitignored"
    assert {entry.relative_path for entry in inventory.files} == {
        ".gitignore", "src/handler.py", "src/notes.txt", "leak.py", "weird.py"}

    prepared = prepare_scope(root, (".",), PrepareOptions(inventory=options))
    content_reasons = {item.relative_path: (item.reason, item.detail) for item in prepared.excluded}
    assert content_reasons["leak.py"][0] == "credential_pattern"
    assert content_reasons["weird.py"][0] == "unsupported_long_line"
    assert [file.snapshot.relative_path for file in prepared.files] == [
        ".gitignore", "src/handler.py", "src/notes.txt"]
    fragments = prepared.fragments
    assert fragments and all(fragment.text for fragment in fragments)
    assert all(fragment.start_line <= fragment.end_line for fragment in fragments)
    assert prepared.complete


def test_authorization_refuses_paths_outside_root(tmp_path):
    (tmp_path / "repo").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("data", encoding="utf-8")
    root = AuthorizedRoot.open(str(tmp_path / "repo"))
    with pytest.raises(UnauthorizedPathError):
        root.resolve_entry("../outside.txt")
    with pytest.raises(UnauthorizedPathError):
        root.relativize(str(outside))


def test_root_behind_symlinked_ancestor_is_authorized_but_inner_links_refused(tmp_path):
    real = tmp_path / "real"
    (real / "repo").mkdir(parents=True)
    (real / "repo" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (real / "outside").mkdir()
    (real / "outside" / "b.py").write_text("y = 2\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    root = AuthorizedRoot.open(str(link / "repo"))  # environmental symlink above the root
    assert root.resolve_entry("a.py").kind == "file"

    os.symlink(os.path.join(str(real), "outside"), os.path.join(str(root.path), "inner-link"),
               target_is_directory=True)
    with pytest.raises(UnauthorizedPathError) as refusal:
        root.resolve_entry("inner-link/b.py")
    assert refusal.value.refusal == "link"
