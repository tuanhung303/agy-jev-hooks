"""Trusted configuration loading and local doctor state (port of upstream config.ts).

The operator, not the searched repository, decides the authorized root, remote
disclosure, the provider destination and resource ceilings. The configuration
file is YAML (parsed with sage.jev.config.yaml_lite), must live outside the repository
it authorizes, and the provider secret comes from the environment, never the
file. Nothing here contacts a provider: doctor works offline without a key.
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .authorization import AuthorizedRoot, assert_safe_relative_path
from .contracts import CONFIG_SCHEMA_VERSION, CONTRACT_LIMITS, SCAN_CAP_KEYS
from .policy import DEFAULT_DIRECT_MODEL, score_cache_policy
from .tokens import REFERENCE_COUNTER_ID

_BASE_URL_ALLOWED = re.compile(r"^https://(?:api\.typesafe\.ai|ai-gateway\.vercel\.sh|openrouter\.ai)/?$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

ADAPTER_BASE_URLS = {
    "typesafe-direct": re.compile(r"^https://api\.typesafe\.ai/?$"),
    "vercel-ai-gateway": re.compile(r"^https://ai-gateway\.vercel\.sh/?$"),
    "openrouter": re.compile(r"^https://openrouter\.ai/?$"),
}
ADAPTER_MODELS = {"vercel-ai-gateway": "typesafe-ai/jev", "openrouter": "typesafe/jev-1.13"}


class ConfigurationError(Exception):
    """A configuration problem the operator can act on."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def default_configuration(repository_root: str, model: str = DEFAULT_DIRECT_MODEL) -> dict:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "repository_root": repository_root,
        "remote_evaluation_enabled": False,
        "provider": {"adapter": "typesafe-direct", "base_url": "https://api.typesafe.ai",
                     "api_key_env": "TYPESAFE_API_KEY", "model": model, "pricing": None},
        "search": {"deadline_ms": 300_000, "concurrency": 4, "require_fit": True,
                   "default_response_tokens": CONTRACT_LIMITS["default_response_tokens"],
                   "max_response_tokens": CONTRACT_LIMITS["max_response_tokens"],
                   "threshold": 0.5},
        "scan_caps": {key: None for key in SCAN_CAP_KEYS},
        "source": {"respect_gitignore": True, "follow_links": False,
                   "max_file_bytes": 1_048_576, "extra_deny_globs": []},
        "cache": {"enabled": True, "ttl_seconds": 604_800, "max_bytes": 104_857_600,
                  "rolling_ttl_seconds": 900},
        "logging": {"level": "info", "include_source": False},
    }


def validate_configuration(value: dict) -> dict:
    """Port of the upstream configuration schema rules. Fail closed."""
    def require(condition: bool, rule: str) -> None:
        if not condition:
            raise ConfigurationError("INVALID_CONFIG", rule)

    require(isinstance(value, dict), "configuration must be a mapping")
    require(value.get("schema_version") == CONFIG_SCHEMA_VERSION, "unexpected configuration schema_version")

    root = value.get("repository_root")
    require(isinstance(root, str) and not _CONTROL.search(root)
            and ((root.startswith("/") and not root.startswith("//")) or re.match(r"^[A-Za-z]:[\\/]", root))
            and ".." not in root.replace("\\", "/").split("/"), "repository_root must be an absolute path")
    require(isinstance(value.get("remote_evaluation_enabled"), bool), "remote_evaluation_enabled must be boolean")

    provider = value.get("provider")
    require(isinstance(provider, dict), "provider must be a mapping")
    base_url = provider.get("base_url")
    require(isinstance(base_url, str) and _BASE_URL_ALLOWED.match(base_url) is not None,
            "unsupported provider endpoint")
    require(isinstance(provider.get("api_key_env"), str) and _ENV_NAME.match(provider["api_key_env"]) is not None,
            "api_key_env must be an environment variable name")
    model = provider.get("model")
    require(isinstance(model, str) and re.match(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]*$", model) is not None,
            "provider.model must be an identifier")
    adapter = provider.get("adapter") or "typesafe-direct"
    require(adapter in ADAPTER_BASE_URLS, "unknown provider adapter")
    require(ADAPTER_BASE_URLS[adapter].match(base_url) is not None, f"{adapter} endpoint does not match its host")
    if adapter in ADAPTER_MODELS:
        require(model == ADAPTER_MODELS[adapter], f"{adapter} requires the {ADAPTER_MODELS[adapter]} model id")

    pricing = provider.get("pricing")
    require(pricing is None or isinstance(pricing, dict), "pricing must be a record or null")
    if pricing is not None:
        require(pricing.get("model") == model, "pricing must match the configured model")
        require(isinstance(pricing.get("verified_at"), str) and _DATE.match(pricing["verified_at"]) is not None,
                "pricing.verified_at must be YYYY-MM-DD")
        require(_is_usd_number(pricing.get("input_usd_per_million_tokens")), "pricing input rate must be USD")
        require(pricing.get("output_usd_per_million_tokens") == 0,
                "only free-output Jev pricing is currently supported")

    search = value.get("search")
    require(isinstance(search, dict), "search must be a mapping")
    require(_positive_int(search.get("deadline_ms")), "search.deadline_ms must be positive")
    require(_positive_int(search.get("concurrency")), "search.concurrency must be positive")
    require(search.get("require_fit") is True, "search.require_fit must be true")
    require(_response_tokens(search.get("default_response_tokens"))
            and _response_tokens(search.get("max_response_tokens")), "response token bounds are invalid")
    require(search["default_response_tokens"] <= search["max_response_tokens"],
            "default response budget exceeds maximum")
    threshold = search.get("threshold")
    require(isinstance(threshold, (int, float)) and not isinstance(threshold, bool) and 0 <= threshold <= 1,
            "search.threshold must be a probability")

    retry = search.get("retry")
    require(retry is None or isinstance(retry, dict), "search.retry must be a record or absent")
    if retry is not None:
        require(isinstance(retry.get("max_retries"), int) and 0 <= retry["max_retries"] <= 10
                and _positive_int(retry.get("base_delay_ms")) and _positive_int(retry.get("max_delay_ms"))
                and retry["base_delay_ms"] <= retry["max_delay_ms"]
                and isinstance(retry.get("retry_ambiguous"), bool), "search.retry is out of bounds")

    scan_caps = value.get("scan_caps")
    require(isinstance(scan_caps, dict) and set(scan_caps) == set(SCAN_CAP_KEYS), "scan_caps must list every cap")
    for key, cap in scan_caps.items():
        require(cap is None or (_is_usd_number(cap) if key == "estimated_cost_usd" else _count(cap)),
                f"scan_caps.{key} must be null or a count")
    require(scan_caps.get("estimated_cost_usd") is None or pricing is not None,
            "a USD cap requires a dated pricing record for the model")

    source = value.get("source")
    require(isinstance(source, dict), "source must be a mapping")
    require(isinstance(source.get("respect_gitignore"), bool), "source.respect_gitignore must be boolean")
    require(source.get("follow_links") is False, "source.follow_links must be false")
    require(_positive_int(source.get("max_file_bytes")), "source.max_file_bytes must be positive")
    require(isinstance(source.get("extra_deny_globs"), list)
            and all(isinstance(item, str) and len(item) <= 4_096 for item in source["extra_deny_globs"])
            and len(source["extra_deny_globs"]) <= 128, "source.extra_deny_globs must be bounded strings")

    cache = value.get("cache")
    require(isinstance(cache, dict), "cache must be a mapping")
    require(isinstance(cache.get("enabled"), bool), "cache.enabled must be boolean")
    require(_positive_int(cache.get("ttl_seconds")), "cache.ttl_seconds must be positive")
    require(_positive_int(cache.get("max_bytes")), "cache.max_bytes must be positive")
    rolling = cache.get("rolling_ttl_seconds", 900)
    require(isinstance(rolling, int) and not isinstance(rolling, bool) and 0 <= rolling <= 900,
            "cache.rolling_ttl_seconds must be 0..900")

    logging = value.get("logging")
    require(isinstance(logging, dict) and logging.get("level") in ("silent", "error", "warn", "info", "debug"),
            "logging.level is invalid")
    require(logging.get("include_source") is False, "logging.include_source must be false")
    return value


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_usd_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _response_tokens(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= CONTRACT_LIMITS["min_response_tokens"]


def user_data_directory(env: Optional[dict] = None) -> str:
    env = env if env is not None else dict(os.environ)
    override = env.get("JEVGREP_CACHE_HOME")
    if override and os.path.isabs(override):
        return os.path.abspath(override)
    xdg = env.get("XDG_CACHE_HOME")
    return os.path.join(xdg, "jevgrep") if xdg and os.path.isabs(xdg) \
        else os.path.join(os.path.expanduser("~"), ".cache", "jevgrep")


def config_directory(env: Optional[dict] = None) -> str:
    env = env if env is not None else dict(os.environ)
    override = env.get("JEVGREP_CONFIG_HOME")
    if override and os.path.isabs(override):
        return os.path.abspath(override)
    xdg = env.get("XDG_CONFIG_HOME")
    return os.path.join(xdg, "jevgrep") if xdg and os.path.isabs(xdg) \
        else os.path.join(os.path.expanduser("~"), ".config", "jevgrep")


def _canonicalize(path: str) -> str:
    """Realpath the deepest existing ancestor; keep the missing tail lexical.

    Containment checks must see through symlinks (a link outside pointing into
    the repository is inside), while not-yet-created cache segments stay usable.
    """
    suffix: list = []
    current = os.path.abspath(path)
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        suffix.append(assert_safe_relative_path(os.path.basename(current)))
        current = parent
    return os.path.join(os.path.realpath(current), *reversed(suffix))


def is_inside_directory(candidate: str, directory: str) -> bool:
    candidate = os.path.abspath(candidate)
    directory = os.path.abspath(directory)
    root = directory if directory.endswith(os.sep) else directory + os.sep
    return candidate == directory or candidate.startswith(root)


def configuration_fingerprint(config: dict, repository_root: str) -> str:
    """Identity of the settings that decide what may be prepared and where it is sent."""
    canonical = json.dumps([
        CONFIG_SCHEMA_VERSION, repository_root,
        config["provider"].get("adapter") or "typesafe-direct",
        config["provider"]["base_url"].rstrip("/"), config["provider"]["model"],
        config["source"]["respect_gitignore"], config["source"]["max_file_bytes"],
        sorted(config["source"]["extra_deny_globs"]),
    ], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def load_configuration(config_path: str, env: Optional[dict] = None, cwd: Optional[str] = None) -> dict:
    """Read and validate one trusted configuration file (YAML)."""
    from sage.jev.config.yaml_lite import safe_load

    absolute = os.path.abspath(os.path.join(cwd or os.getcwd(), config_path))
    real_profile = _canonicalize(absolute)
    try:
        with open(real_profile, "r", encoding="utf-8") as handle:
            text = handle.read(1_048_576)
    except OSError as cause:
        raise ConfigurationError("INVALID_CONFIG",
                                 f"cannot read the configuration file {absolute} ({cause.errno})") from cause
    try:
        parsed = safe_load(text)
    except ValueError:
        raise ConfigurationError("INVALID_CONFIG", f"{absolute} is not valid YAML") from None

    try:
        config = validate_configuration(parsed)
    except ConfigurationError as cause:
        raise ConfigurationError("INVALID_CONFIG", f"{absolute}: {cause.detail}") from cause

    try:
        source_root = AuthorizedRoot.open(config["repository_root"])
        cache_directory = _canonicalize(os.path.join(
            user_data_directory(env), "scores",
            configuration_fingerprint(config, os.path.realpath(config["repository_root"]))))
    except Exception:
        raise ConfigurationError("INVALID_CONFIG",
                                 "repository_root or cache ancestors failed filesystem authorization") from None
    repository_root = source_root.path
    if is_inside_directory(real_profile, repository_root):
        raise ConfigurationError("INVALID_CONFIG",
                                 "the trusted configuration must live outside the repository it authorizes")
    if is_inside_directory(cache_directory, repository_root):
        raise ConfigurationError("INVALID_CONFIG",
                                 f"the cache directory {cache_directory} would sit inside the authorized repository; "
                                 "set JEVGREP_CACHE_HOME elsewhere")

    return {"config_path": real_profile, "config": config,
            "repository_root": repository_root, "source_root": source_root,
            "cache_directory": cache_directory,
            "fingerprint": configuration_fingerprint(config, repository_root)}


def find_profile_for(cwd: Optional[str] = None, env: Optional[dict] = None) -> str:
    """Locate the trusted profile authorizing cwd.

    Resolution order: JEVGREP_PROFILE env, else the profile in the config
    directory whose repository_root is the closest ancestor of cwd. An
    unauthorized workspace gets an actionable error, never a silent fallback.
    """
    from sage.jev.config.yaml_lite import safe_load

    env = env if env is not None else dict(os.environ)
    explicit = env.get("JEVGREP_PROFILE")
    if explicit:
        return os.path.abspath(explicit)

    target = os.path.realpath(cwd or os.getcwd())
    directory = config_directory(env)
    try:
        names = sorted(name for name in os.listdir(directory) if name.endswith((".yaml", ".yml")))
    except OSError:
        names = []
    best_path = None
    best_root = ""
    for name in names:
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                parsed = safe_load(handle.read(1_048_576))
            root = os.path.realpath(parsed["repository_root"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if (target == root or target.startswith(root.rstrip(os.sep) + os.sep)) and len(root) > len(best_root):
            best_path, best_root = path, root
    if best_path is None:
        raise ConfigurationError(
            "INVALID_CONFIG",
            f"no authorized JevGrep profile for {target}; authorize once with: "
            f"jevgrep init --root {target} --enable-remote --provider vercel-ai-gateway")
    return best_path


def resolve_credential(loaded: dict, env: Optional[dict] = None) -> str:
    env = env if env is not None else dict(os.environ)
    config = loaded["config"]
    if not config["remote_evaluation_enabled"]:
        raise ConfigurationError("REMOTE_DISABLED",
                                 f"remote evaluation is disabled in {loaded['config_path']}; set "
                                 f"remote_evaluation_enabled: true to send eligible excerpts to "
                                 f"{config['provider']['base_url']}")
    name = config["provider"]["api_key_env"]
    secret = env.get(name)
    if not secret or not secret.strip():
        raise ConfigurationError("CREDENTIAL_MISSING",
                                 f"the environment variable {name} is empty or unset; export the provider "
                                 "credential before searching")
    return secret


def doctor_report(loaded: dict, env: Optional[dict] = None, counter_id: str = REFERENCE_COUNTER_ID) -> dict:
    """Describe the configuration exactly as the engine will use it; no provider call."""
    env = env if env is not None else dict(os.environ)
    config = loaded["config"]
    secret = env.get(config["provider"]["api_key_env"])
    has_secret = bool(secret and secret.strip())
    credential = ("not_required" if not config["remote_evaluation_enabled"]
                  else "present" if has_secret else "missing")

    enabled = {key: value for key, value in config["scan_caps"].items() if value is not None}
    disabled = [key for key, value in config["scan_caps"].items() if value is None]

    problems = []
    if not config["remote_evaluation_enabled"]:
        problems.append("remote evaluation is disabled: doctor and inspect work, "
                        "search cannot dispatch a provider request")
    elif not has_secret:
        problems.append(f"the credential environment variable {config['provider']['api_key_env']} "
                        "is empty or unset")
    root_readable = True
    try:
        loaded["source_root"].read_directory(".")
    except Exception:
        root_readable = False
        problems.append(f"the authorized repository root {loaded['repository_root']} is not readable")
    if not config["cache"]["enabled"]:
        problems.append("the evaluation cache is disabled: every search re-evaluates every fragment")

    policy = score_cache_policy(config["provider"].get("adapter") or "typesafe-direct",
                                config["provider"]["model"], config["cache"])
    return {
        "config_path": loaded["config_path"],
        "config_schema_version": config["schema_version"],
        "repository_root": loaded["repository_root"],
        "repository_root_readable": root_readable,
        "remote_evaluation_enabled": config["remote_evaluation_enabled"],
        "provider": {
            "adapter": config["provider"].get("adapter") or "typesafe-direct",
            "base_url": config["provider"]["base_url"], "model": config["provider"]["model"],
            "api_key_env": config["provider"]["api_key_env"], "credential": credential,
        },
        "pricing": config["provider"].get("pricing"),
        "search": dict(config["search"]),
        "response_counter": counter_id,
        "enabled_scan_caps": enabled,
        "disabled_scan_caps": disabled,
        "source": dict(config["source"]),
        "cache": {
            "enabled": config["cache"]["enabled"], "directory": loaded["cache_directory"],
            "ttl_seconds": config["cache"]["ttl_seconds"], "max_bytes": config["cache"]["max_bytes"],
            "policy": policy["mode"], "effective_ttl_seconds": policy["ttl_seconds"],
            "present": os.path.isdir(loaded["cache_directory"]),
        },
        "problems": problems,
    }


def render_doctor_report(report: dict) -> list:
    """Human rendering for the CLI; the JSON form is the report object itself."""
    cap_lines = [f"    {key}: {value}" for key, value in sorted(report["enabled_scan_caps"].items())]
    remote = ("enabled: eligible excerpts are sent to the provider below"
              if report["remote_evaluation_enabled"] else "disabled: no excerpt leaves this machine")
    pricing = ("none: USD estimates stay null" if report["pricing"] is None
               else f"{report['pricing']['verified_at']}, "
                    f"${report['pricing']['input_usd_per_million_tokens']} per million input tokens (estimate only)")
    lines = [
        f"configuration      {report['config_path']} (schema {report['config_schema_version']})",
        f"repository root    {report['repository_root']}"
        + ("" if report["repository_root_readable"] else " (unreadable)"),
        f"remote evaluation  {remote}",
        f"provider           {report['provider']['adapter']} {report['provider']['base_url']} "
        f"model={report['provider']['model']}",
        f"credential         {report['provider']['api_key_env']} ({report['provider']['credential']}; "
        "value never printed)",
        f"pricing record     {pricing}",
        f"response budget    default {report['search']['default_response_tokens']}, "
        f"maximum {report['search']['max_response_tokens']} tokens, counter {report['response_counter']}",
        f"search limits      deadline {report['search']['deadline_ms']} ms, "
        f"concurrency {report['search']['concurrency']}, threshold {report['search']['threshold']}, "
        f"require_fit {report['search']['require_fit']}",
        "scan caps          all disabled (null)" if not cap_lines else "scan caps          enabled:",
        *cap_lines,
        f"                   disabled: {', '.join(report['disabled_scan_caps']) or 'none'}",
        f"source rules       gitignore {report['source']['respect_gitignore']}, links never followed, "
        f"max file {report['source']['max_file_bytes']} bytes, "
        f"{len(report['source']['extra_deny_globs'])} operator deny rule(s)",
        f"score cache        {'enabled' if report['cache']['enabled'] else 'disabled'} "
        f"{report['cache']['directory']} ({'present' if report['cache']['present'] else 'not created yet'}), "
        f"ttl {report['cache']['ttl_seconds']} s, max {report['cache']['max_bytes']} bytes",
        f"cache policy       {report['cache']['policy']}, "
        f"effective ttl {report['cache']['effective_ttl_seconds']} s"
        + ("; model revision unverified, scores may be stale within this window"
           if report["cache"]["policy"] == "rolling" else ""),
    ]
    if report["problems"]:
        lines.append("problems:")
        lines.extend(f"  - {problem}" for problem in report["problems"])
    return lines


def dump_configuration_yaml(config: dict) -> str:
    """Serialize one trusted profile; scalars and nesting only."""

    def render(value, indent: int) -> list:
        pad = "  " * indent
        lines = []
        for key, item in value.items():
            if isinstance(item, dict):
                lines.append(f"{pad}{key}:")
                lines.extend(render(item, indent + 1))
            elif isinstance(item, list):
                if not item:
                    lines.append(f"{pad}{key}: []")
                else:
                    lines.append(f"{pad}{key}:")
                    lines.extend(f"{pad}  - {_scalar(element)}" for element in item)
            else:
                lines.append(f"{pad}{key}: {_scalar(item)}")
        return lines

    return "\n".join(render(config, 0)) + "\n"


def _scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return text if re.fullmatch(r"[A-Za-z0-9._/@:+\-]+", text) else json.dumps(text)
