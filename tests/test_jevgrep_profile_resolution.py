"""Pure profile-resolution tests for anchor-relative MCP scopes."""
import pytest

from mcp.jev.grep.config import (default_configuration, dump_configuration_yaml,
                                resolve_scoped_profile)


def _profile(config_home, name, root):
    path = config_home / name
    path.write_text(dump_configuration_yaml(default_configuration(str(root))), encoding="utf-8")
    return path


def _profiles(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "tmp" / "worktrees"
    (nested / "branch" / "src").mkdir(parents=True)
    (repo / "other").mkdir()
    config_home = tmp_path / "config"
    config_home.mkdir()
    outer = _profile(config_home, "outer.yaml", repo)
    inner = _profile(config_home, "inner.yaml", nested)
    env = {
        "JEVGREP_CONFIG_HOME": str(config_home),
        "JEVGREP_CACHE_HOME": str(tmp_path / "cache"),
    }
    return repo, nested, outer, inner, env


def test_resolve_scoped_profile_rebases_nested_scope(tmp_path):
    repo, _, _, inner, env = _profiles(tmp_path)

    result = resolve_scoped_profile(str(repo), ["tmp/worktrees/branch/src"], env=env)

    assert result == (str(inner), ["branch/src"])


def test_resolve_scoped_profile_rejects_entries_from_different_profiles(tmp_path):
    repo, _, _, _, env = _profiles(tmp_path)

    assert resolve_scoped_profile(str(repo), ["tmp/worktrees/branch/src", "other"], env=env) is None


@pytest.mark.parametrize("scope", [None, "tmp/worktrees/branch/src", [], ["ok", 1], [""], ["ok", ""]])
def test_resolve_scoped_profile_rejects_missing_or_malformed_scope(tmp_path, scope):
    repo, _, _, _, env = _profiles(tmp_path)

    assert resolve_scoped_profile(str(repo), scope, env=env) is None


def test_resolve_scoped_profile_rejects_path_outside_selected_root(tmp_path):
    repo, nested, _, _, env = _profiles(tmp_path)
    (repo / "alias").symlink_to(nested, target_is_directory=True)

    assert resolve_scoped_profile(str(repo), ["alias/branch/src"], env=env) is None
