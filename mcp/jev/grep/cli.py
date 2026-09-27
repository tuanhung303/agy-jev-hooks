"""Thin CLI over the shared engine (init, doctor, inspect, search, cache, mcp).

Exit codes: 0 complete, 2 rejected or invalid request/config, 3 partial,
4 fatal runtime failure, 130 interrupted. Results to stdout, diagnostics to
stderr. This CLI validates input and presents output; it contains no search
logic.
"""
import argparse
import json
import os
import sys
from typing import Optional

from . import __version__
from .config import (ConfigurationError, config_directory, default_configuration, doctor_report,
                     dump_configuration_yaml, find_profile_for, load_configuration,
                     render_doctor_report, resolve_credential, resolve_scoped_profile)


def _load(args):
    return load_configuration(args.config or find_profile_for(env=dict(os.environ)))
from .contracts import ContractValidationError
from .engine import SearchEngine
from .inventory import InventoryOptions
from .prepare import PrepareOptions, exclusion_counts, prepare_scope
from .search_response import to_cli_search_response

JEVGREPIGNORE_SKELETON = """\
# Repository-local exclusions for JevGrep, narrowing only: a listed pattern can
# only exclude further, never re-include a file excluded elsewhere.
# Examples:
# internal/proprietary/**
# docs/private/**
"""


def _print_json(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def command_init(args) -> int:
    root = os.path.abspath(args.root)
    profile = os.path.abspath(args.profile) if args.profile else os.path.join(
        config_directory(), os.path.basename(root.rstrip(os.sep)) + ".yaml")
    config = default_configuration(root, args.model)
    if args.provider:
        config["provider"]["adapter"] = args.provider
        if args.provider == "vercel-ai-gateway":
            config["provider"].update(base_url="https://ai-gateway.vercel.sh", model="typesafe-ai/jev",
                                      api_key_env="AI_GATEWAY_API_KEY")
        elif args.provider == "openrouter":
            config["provider"].update(base_url="https://openrouter.ai", model="typesafe/jev-1.13",
                                      api_key_env="OPENROUTER_API_KEY")
    if args.enable_remote:
        config["remote_evaluation_enabled"] = True

    from .config import validate_configuration
    validate_configuration(config)
    if os.path.commonpath([profile, root]) == root:
        print("the trusted configuration must live outside the repository it authorizes", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(profile), exist_ok=True)
    with open(profile, "w", encoding="utf-8") as handle:
        handle.write("# Trusted JevGrep profile; never store credentials here.\n" + dump_configuration_yaml(config))
    print(f"configuration profile written to {profile}")

    ignore_path = os.path.join(root, ".jevgrepignore")
    if not os.path.exists(ignore_path):
        with open(ignore_path, "w", encoding="utf-8") as handle:
            handle.write(JEVGREPIGNORE_SKELETON)
        print(f"created {ignore_path}")
    if not config["remote_evaluation_enabled"]:
        print("remote evaluation stays disabled; edit the profile to enable it")
    return 0


def command_doctor(args) -> int:
    try:
        loaded = _load(args)
    except ConfigurationError as cause:
        print(cause.detail, file=sys.stderr)
        return 2
    report = doctor_report(loaded)
    if args.json:
        _print_json(report)
    else:
        print("\n".join(render_doctor_report(report)))
    return 0


def command_inspect(args) -> int:
    try:
        loaded = _load(args)
    except ConfigurationError as cause:
        print(cause.detail, file=sys.stderr)
        return 2
    config = loaded["config"]
    scope = tuple(args.scope or ["."])
    prepared = prepare_scope(loaded["source_root"], scope, PrepareOptions(
        inventory=InventoryOptions(
            respect_gitignore=config["source"]["respect_gitignore"],
            max_file_bytes=config["source"]["max_file_bytes"],
            extra_deny_globs=tuple(config["source"]["extra_deny_globs"]),
        ),
    ))
    _print_json({
        "scope": list(scope),
        "files": {
            "discovered": prepared.inventory.discovered,
            "eligible": len(prepared.files),
            "excluded_by_reason": exclusion_counts(prepared),
            "unreadable": prepared.unreadable,
        },
        "fragments": len(prepared.fragments),
        "prepared_bytes": prepared.prepared_bytes,
        "complete": prepared.complete,
        "excluded_directories": [
            {"path": entry.relative_path, "reason": entry.reason}
            for entry in prepared.inventory.excluded_directories
        ],
    })
    return 0


def command_search(args) -> int:
    scope = sorted(set(args.scope)) if args.scope else None
    try:
        # Same routing as the MCP server: a scope under a nested, more specific
        # profile (for example tmp/worktrees) searches with that profile.
        scoped = None if args.config or not scope else resolve_scoped_profile(
            os.getcwd(), scope, env=dict(os.environ))
        if scoped is not None:
            profile_path, scope = scoped
            loaded = load_configuration(profile_path)
        else:
            loaded = _load(args)
    except ConfigurationError as cause:
        _emit_error(cause.code)
        return 2
    request = {"query": args.query}
    if scope:
        request["scope"] = scope
    if args.max_context_tokens:
        request["max_context_tokens"] = args.max_context_tokens
    if args.allow_partial_scan:
        request["allow_partial_scan"] = True
    engine = SearchEngine(loaded, env=dict(os.environ))
    result = engine.search(request)
    rendered = to_cli_search_response(result["outcome"])
    print(rendered["stdout"])
    return rendered["exit_code"]


def command_cache(args) -> int:
    try:
        loaded = _load(args)
    except ConfigurationError as cause:
        print(cause.detail, file=sys.stderr)
        return 2
    config = loaded["config"]
    from .cache import ScoreCache
    cache = ScoreCache(loaded["cache_directory"], config["cache"]["enabled"],
                       config["cache"]["ttl_seconds"], config["cache"]["max_bytes"])
    removed = cache.clear()
    print(json.dumps({"removed": removed}))
    return 0


def command_mcp(args) -> int:
    from .mcp_server import run_mcp_server
    engine = None
    try:
        loaded = _load(args)
        engine = SearchEngine(loaded, env=dict(os.environ))
    except ConfigurationError:
        pass

    def profile_for(path: str) -> Optional[str]:
        try:
            return find_profile_for(cwd=path, env=dict(os.environ))
        except ConfigurationError:
            return None

    def engine_for(profile_path: str) -> Optional[SearchEngine]:
        try:
            return SearchEngine(load_configuration(profile_path), env=dict(os.environ))
        except ConfigurationError:
            return None

    def resolve_engine(context: dict, arguments: dict):
        """Pick the engine for one call: a request scope may name a subtree
        with its own, more specific authorization (for example a nested
        worktree profile carved out from a broader deny rule at the anchor),
        so this re-resolves per call instead of caching one engine for the
        life of the process. An explicit --config always wins: it names one
        profile for the whole process, never subject to auto-discovery."""
        if args.config:
            return SearchEngine(_load(args), env=dict(os.environ)), arguments

        from urllib.parse import unquote, urlparse

        anchor = None
        root_uri = context.get("root_uri")
        if root_uri and isinstance(root_uri, str):
            parsed_path = unquote(urlparse(root_uri).path)
            if parsed_path and os.path.isdir(parsed_path):
                anchor = parsed_path
        if anchor is None:
            for folder in context.get("workspace_folders") or []:
                uri = folder.get("uri") if isinstance(folder, dict) else None
                if uri:
                    folder_path = unquote(urlparse(uri).path)
                    if folder_path and os.path.isdir(folder_path):
                        anchor = folder_path
                        break
        if anchor is None:
            anchor = context.get("startup_cwd") or os.getcwd()

        scope = arguments.get("scope") if isinstance(arguments, dict) else None
        scoped = resolve_scoped_profile(anchor, scope, env=dict(os.environ))
        if scoped is not None:
            profile_path, rebased_scope = scoped
            scoped_engine = engine_for(profile_path)
            if scoped_engine is not None:
                return scoped_engine, {**arguments, "scope": rebased_scope}
            raise ConfigurationError(
                "INVALID_CONFIG",
                f"the authorized profile for this scope ({profile_path}) failed to load")

        anchor_profile = profile_for(anchor)
        if anchor_profile is not None:
            anchor_engine = engine_for(anchor_profile)
            if anchor_engine is not None:
                return anchor_engine, arguments

        return SearchEngine(_load(args), env=dict(os.environ)), arguments

    run_mcp_server(engine, sys.stdin, sys.stdout, sys.stderr, __version__, engine_resolver=resolve_engine)
    return 0


def _emit_error(code: str) -> None:
    from .contracts import create_search_error
    from .search_response import to_cli_search_response
    rendered = to_cli_search_response(create_search_error(code, "cli"))
    print(rendered["stdout"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jevgrep", description="Jev-powered semantic code search (Python port)")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="authorize a repository and write a trusted profile")
    init.add_argument("--root", default=os.getcwd())
    init.add_argument("--profile", help="profile path, must live outside the repository")
    init.add_argument("--provider", choices=["typesafe-direct", "vercel-ai-gateway", "openrouter"])
    init.add_argument("--model", default="jev-1.13.0")
    init.add_argument("--enable-remote", action="store_true",
                      help="enable remote evaluation in the written profile")
    init.set_defaults(func=command_init)

    doctor = sub.add_parser("doctor", help="offline configuration report")
    doctor.add_argument("--config")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=command_doctor)

    inspect = sub.add_parser("inspect", help="offline eligibility and workload report")
    inspect.add_argument("--config")
    inspect.add_argument("--scope", action="append")
    inspect.set_defaults(func=command_inspect)

    search = sub.add_parser("search", help="search by behaviour")
    search.add_argument("--config")
    search.add_argument("--query", required=True)
    search.add_argument("--scope", action="append")
    search.add_argument("--max-context-tokens", type=int)
    search.add_argument("--allow-partial-scan", action="store_true")
    search.set_defaults(func=command_search)

    cache = sub.add_parser("cache", help="cache maintenance")
    cache.add_argument("action", choices=["clear"])
    cache.add_argument("--config")
    cache.set_defaults(func=command_cache)

    mcp = sub.add_parser("mcp", help="run the stdio MCP server")
    mcp.add_argument("--config")
    mcp.set_defaults(func=command_mcp)
    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ContractValidationError as cause:
        print(f"contract violation: {cause}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
