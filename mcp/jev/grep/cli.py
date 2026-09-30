"""CLI and stdio MCP server for JevGrep; `grep` shares the ranked reply with `jev_grep`.

`grep` exits 0 for results, 1 for no matches, 2 for invalid input or rg errors, and 130 when interrupted.
"""
import argparse
import json
import os
import sys
import time

from .config import (ConfigurationError, config_directory, default_configuration, doctor_report,
                     dump_configuration_yaml, find_profile_for, load_configuration,
                     render_doctor_report, resolve_scoped_profile)


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

    mcp = sub.add_parser("mcp", help="stdio MCP server with the jev_grep tool (rg ranked for a task)")
    mcp.set_defaults(func=command_mcp)

    grep = sub.add_parser("grep", help="jev_grep from the shell: rg, then Jev ranks the files for --task")
    grep.add_argument("pattern", nargs="?", help="regex; with -e, the first positional value is PATH")
    grep.add_argument("path", nargs="?", help="file or folder; default: the current folder")
    grep.add_argument("-e", "--regexp", dest="regexp", help="pattern, including one starting with a dash")
    grep.add_argument("--task", required=True, help="one sentence: what you are looking for and why")
    grep.add_argument("--glob", action="append", help="ripgrep glob filter; may repeat in rg order")
    grep.add_argument("-i", "--ignore-case", action="store_true")
    grep.add_argument("--no-ignore", action="store_true", help="search ignored files and directories")
    grep.set_defaults(func=command_grep)

    return parser


def command_grep(args) -> int:
    """Same code and reply as the jev_grep MCP tool, rooted at the shell's current folder."""
    from .grep_server import log_event
    from .grep_tool import ToolInputError, run
    pattern = args.regexp or args.pattern
    path = args.pattern if args.regexp and args.pattern else args.path
    if not pattern:
        print("jevgrep grep: provide PATTERN or -e PATTERN", file=sys.stderr)
        return 2
    if args.regexp and args.path:
        print("jevgrep grep: use either PATTERN or -e PATTERN", file=sys.stderr)
        return 2
    arguments = {"pattern": pattern, "task": args.task, "path": path, "glob": args.glob,
                 "ignore_case": args.ignore_case, "no_ignore": args.no_ignore}
    base, started = os.getcwd(), time.monotonic()
    try:
        text, event = run({key: value for key, value in arguments.items() if value is not None}, base,
                          started=started, show_root=False)
    except ToolInputError as cause:
        print(f"jevgrep grep: {cause}", file=sys.stderr)
        log_event({"outcome": "bad_input", "elapsed_s": round(time.monotonic() - started, 2),
                   "via": "cli", "base": base})
        return 2
    log_event({**event, "via": "cli", "base": base})
    print(text)
    return 2 if event["outcome"] == "rg_error" else 1 if event["outcome"] == "none" else 0


def command_mcp(args) -> int:
    from .grep_server import serve
    serve()
    return 0


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for index, value in enumerate(argv[:-1]):
        if value == "--":
            break
        if value in {"-e", "--regexp"} and argv[index + 1].startswith("-"):
            argv[index:index + 2] = [value + "=" + argv[index + 1]]
            break
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ContractValidationError as cause:
        print(f"contract violation: {cause}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
