"""sage.jev.evidence.tools - Tool argument normalization across harness shapes.

Harness tool calls arrive as dicts, parsed JSON strings, or bare command and
path strings. These helpers give the evidence assembler one shape to read;
detection uses the raw value, redaction happens at render time.
"""
import json
from typing import Any, Dict, Set, Tuple

PATH_ARG_KEYS: Tuple[str, ...] = (
    "TargetFile", "target_file", "targetFile",
    "FilePath", "file_path", "filePath",
    "AbsolutePath", "absolute_path", "absolutePath",
    "TargetPath", "target_path", "targetPath",
    "path", "Path",
    "file", "File",
    "filename", "fileName", "file_name",
    "dest", "destination",
    "output_file", "output_path",
)

CMD_ARG_KEYS: Tuple[str, ...] = (
    "CommandLine", "command_line", "commandLine",
    "command", "Command",
    "cmd", "Cmd",
    "script", "Script",
)

READ_TOOLS: Set[str] = {
    "view_file", "read_file", "cat", "open_file", "read_url_content",
    "read_browser_page", "read_resource", "list_dir", "find_by_name", "grep_search",
}

_PATH_TOOLS = READ_TOOLS | {
    "write_to_file", "replace_file_content", "multi_replace_file_content",
    "edit_file", "create_file", "apply_diff", "patch", "modify_file", "write_file",
}

_CMD_TOOLS = {"run_command", "bash", "exec", "terminal", "cmd", "command"}


def normalize_tool_args(tool_args: Any, tool_name: str = "") -> Dict[str, Any]:
    """Dict args pass through; string args parse as JSON or bare command/path."""
    if isinstance(tool_args, str):
        raw = tool_args.strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        name_lower = str(tool_name or "").strip().lower()
        if name_lower in _CMD_TOOLS:
            return {"CommandLine": raw}
        if name_lower in _PATH_TOOLS:
            return {"TargetFile": raw}
        return {"raw_arg": raw}
    if isinstance(tool_args, dict):
        return tool_args
    return {}


def extract_path_from_args(args: Dict[str, Any]) -> str:
    """Target or absolute file path across supported key aliases."""
    for key in PATH_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().strip("\"'")
    return ""


def extract_command_from_args(args: Dict[str, Any]) -> str:
    """Terminal command line string across supported key aliases."""
    for key in CMD_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""
