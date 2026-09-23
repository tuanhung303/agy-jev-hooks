#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMER_SRC="$REPO_DIR/hooks/command-timer.py"
STATUSLINE_SRC="$REPO_DIR/statusline/statusline.py"
PROMPT_SRC="$REPO_DIR/sage/sage_prompt.md"
AGY_AUDIT_SRC="$REPO_DIR/hooks/agy-stop-audit.py"
RECOMMENDER_SRC="$REPO_DIR/hooks/skill-recommender.py"
QODER_AUDIT_SRC="$REPO_DIR/hooks/qoder-stop-audit.py"
QODER_FORK_SRC="$REPO_DIR/scripts/qoder-fork.py"
ZCODE_AUDIT_SRC="$REPO_DIR/hooks/zcode-stop-audit.py"
JEVGREP_SRC="$REPO_DIR/scripts/jevgrep"

MODE="symlink"
for arg in "$@"; do
  case "$arg" in
    --copy|--sync|-c)
      MODE="copy"
      ;;
    --symlink|-s)
      MODE="symlink"
      ;;
  esac
done

if [[ ! -f "$AGY_AUDIT_SRC" ]]; then
  echo "Error: Hook file not found at $AGY_AUDIT_SRC" >&2
  exit 1
fi

if [[ ! -f "$TIMER_SRC" ]]; then
  echo "Error: Hook file not found at $TIMER_SRC" >&2
  exit 1
fi

if [[ ! -f "$STATUSLINE_SRC" ]]; then
  echo "Error: Statusline file not found at $STATUSLINE_SRC" >&2
  exit 1
fi

if [[ ! -f "$QODER_AUDIT_SRC" ]]; then
  echo "Error: Qoder hook file not found at $QODER_AUDIT_SRC" >&2
  exit 1
fi

if [[ ! -f "$QODER_FORK_SRC" ]]; then
  echo "Error: Qoder fork helper not found at $QODER_FORK_SRC" >&2
  exit 1
fi

if [[ ! -f "$ZCODE_AUDIT_SRC" ]]; then
  echo "Error: ZCode hook file not found at $ZCODE_AUDIT_SRC" >&2
  exit 1
fi

if [[ -f "$PROMPT_SRC" ]]; then
  chmod +x "$PROMPT_SRC" 2>/dev/null || true
fi

chmod +x "$AGY_AUDIT_SRC" "$TIMER_SRC" "$STATUSLINE_SRC" "$QODER_AUDIT_SRC" "$QODER_FORK_SRC" "$ZCODE_AUDIT_SRC" "$JEVGREP_SRC"
chmod +x "$SCRIPT_DIR/install.sh"
if [[ -f "$SCRIPT_DIR/sync.sh" ]]; then
  chmod +x "$SCRIPT_DIR/sync.sh"
fi

echo "Installing hooks, statusline, prompt, and launchers from $REPO_DIR (mode: $MODE)..."

mkdir -p "$HOME/.config/agy" "$HOME/.gemini/config/hooks" "$HOME/.qoder/hooks" "$HOME/.zcode/hooks" "$HOME/.local/bin"

install_file() {
  local src="$1"
  local dst="$2"
  local label="$3"
  rm -f "$dst"
  if [[ "$MODE" == "copy" ]]; then
    cp -p "$src" "$dst"
    if command -v xattr >/dev/null 2>&1; then
      xattr -c "$dst" 2>/dev/null || true
    fi
    echo "✓ Copied $label"
  else
    ln -sf "$src" "$dst"
    echo "✓ Symlinked $label"
  fi
}

# 1. agy-stop-audit (and clean up retired sage-enforce)
rm -f "$HOME/.config/agy/sage-enforce.py" "$HOME/.gemini/config/hooks/sage-enforce.py"
install_file "$AGY_AUDIT_SRC" "$HOME/.config/agy/agy-stop-audit.py" "agy-stop-audit.py"
install_file "$AGY_AUDIT_SRC" "$HOME/.gemini/config/hooks/agy-stop-audit.py" "agy-stop-audit.py (gemini hook)"

# 3. sage prompt (and legacy advisor_prompt compatibility link)
if [[ -f "$PROMPT_SRC" ]]; then
  install_file "$PROMPT_SRC" "$HOME/.config/agy/sage_prompt.md" "sage_prompt.md"
  install_file "$PROMPT_SRC" "$HOME/.config/agy/advisor_prompt.md" "advisor_prompt.md compatibility"
fi

# 4. command-timer
install_file "$TIMER_SRC" "$HOME/.config/agy/command-timer.py" "command-timer.py"
install_file "$TIMER_SRC" "$HOME/.gemini/config/hooks/command-timer.py" "command-timer.py (gemini hook)"

# 5. statusline
install_file "$STATUSLINE_SRC" "$HOME/.config/agy/statusline.py" "statusline.py"

# 5b. Qoder stop audit hook and fork helper
install_file "$QODER_AUDIT_SRC" "$HOME/.qoder/hooks/qoder-stop-audit.py" "qoder-stop-audit.py (Qoder hook)"
install_file "$QODER_FORK_SRC" "$HOME/.qoder/hooks/qoder-fork.py" "qoder-fork.py (Qoder helper)"

# 5b-bis. ZCode stop audit hook and skill router copy
install_file "$ZCODE_AUDIT_SRC" "$HOME/.zcode/hooks/zcode-stop-audit.py" "zcode-stop-audit.py (ZCode hook)"
install_file "$RECOMMENDER_SRC" "$HOME/.zcode/hooks/skill-recommender.py" "skill-recommender.py (ZCode hook)"

# 5c. Skill recommender pre-hook and route table
install_file "$RECOMMENDER_SRC" "$HOME/.config/agy/skill-recommender.py" "skill-recommender.py"
install_file "$RECOMMENDER_SRC" "$HOME/.gemini/config/hooks/skill-recommender.py" "skill-recommender.py (gemini hook)"
install_file "$RECOMMENDER_SRC" "$HOME/.qoder/hooks/skill-recommender.py" "skill-recommender.py (Qoder hook)"

# 5d. jevgrep launcher
install_file "$JEVGREP_SRC" "$HOME/.local/bin/jevgrep" "jevgrep launcher"

# 6. sage package copy when mode is copy
if [[ "$MODE" == "copy" ]]; then
  rm -rf "$HOME/.config/agy/sage"
  cp -Rp "$REPO_DIR/sage" "$HOME/.config/agy/sage"
  if command -v xattr >/dev/null 2>&1; then
    xattr -rc "$HOME/.config/agy/sage" 2>/dev/null || true
  fi
  echo "✓ Copied sage package to $HOME/.config/agy/sage"
  rm -rf "$HOME/.config/agy/skills"
  cp -Rp "$REPO_DIR/skills" "$HOME/.config/agy/skills"
  echo "✓ Copied skills pack to $HOME/.config/agy/skills"
fi

# 7. Configure ~/.gemini/config/hooks.json if needed
HOOKS_JSON="$HOME/.gemini/config/hooks.json"
/usr/bin/python3 - << PYEOF
import json, os

hooks_path = os.path.expanduser("~/.gemini/config/hooks.json")
data = {}
if os.path.exists(hooks_path):
    try:
        with open(hooks_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

# sage-enforce and legacy session-sage are gone; drop stale registrations.
data.pop("sage-enforce", None)
data.pop("session-sage", None)

# Ensure agy-stop-audit is registered on Stop event
data["agy-stop-audit"] = {
    "Stop": [{
        "type": "command",
        "command": f"python3 {os.path.expanduser('~/.config/agy/agy-stop-audit.py')}",
        "timeout": 30
    }]
}

# Remove redundant legacy session-advisor and session-stop-audit hook entries to prevent duplicate execution
data.pop("session-advisor", None)
data.pop("session-stop-audit", None)

# Ensure command-timer is registered
data.setdefault("command-timer", {
    "PreToolUse": [{
        "matcher": "run_command",
        "hooks": [{
            "type": "command",
            "command": f"python3 {os.path.expanduser('~/.gemini/config/hooks/command-timer.py')} pre_tool",
            "timeout": 5
        }]
    }],
    "PostToolUse": [{
        "matcher": "run_command",
        "hooks": [{
            "type": "command",
            "command": f"python3 {os.path.expanduser('~/.gemini/config/hooks/command-timer.py')} post_tool",
            "timeout": 5
        }]
    }],
    "PreInvocation": [{
        "type": "command",
        "command": f"python3 {os.path.expanduser('~/.gemini/config/hooks/command-timer.py')} pre_invocation",
        "timeout": 5
    }]
})

# Ensure skill-recommender is registered (PreInvocation: fires when the user submits a prompt)
data["skill-recommender"] = {
    "PreInvocation": [{
        "type": "command",
        "command": f"python3 {os.path.expanduser('~/.config/agy/skill-recommender.py')} pre_invocation",
        "timeout": 15
    }]
}

with open(hooks_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
PYEOF
echo "✓ Verified and updated hooks.json configuration"

# 7b. Register the Qoder Stop hook in ~/.qoder/settings.json (idempotent)
/usr/bin/python3 - << PYEOF
import json, os, shutil, time

settings_path = os.path.expanduser("~/.qoder/settings.json")
data = {}
if os.path.exists(settings_path):
    try:
        with open(settings_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

command = f"python3 {os.path.expanduser('~/.qoder/hooks/qoder-stop-audit.py')}"
stop = data.setdefault("hooks", {}).setdefault("Stop", [])
registered = any(
    "qoder-stop-audit.py" in hook.get("command", "")
    for entry in stop
    for hook in entry.get("hooks", [])
)
if registered:
    print("✓ Qoder Stop hook already registered")
else:
    if os.path.exists(settings_path):
        shutil.copy2(settings_path, f"{settings_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    stop.append({"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 150}]})
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("✓ Registered Qoder Stop hook in ~/.qoder/settings.json")

# 7c. Register the Qoder skill-recommender pre-hook on UserPromptSubmit (idempotent)
reco_command = f"python3 {os.path.expanduser('~/.qoder/hooks/skill-recommender.py')} userpromptsubmit"
ups = data.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
reco_registered = any(
    "skill-recommender.py" in hook.get("command", "")
    for entry in ups
    for hook in entry.get("hooks", [])
)
if reco_registered:
    print("✓ Qoder skill-recommender pre-hook already registered")
else:
    ups.append({"hooks": [{"type": "command", "command": reco_command, "timeout": 15}]})
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("✓ Registered Qoder skill-recommender pre-hook on UserPromptSubmit")
PYEOF

# 7c. Register the ZCode hooks in ~/.zcode/cli/config.json (idempotent).
# Configuration-file hooks are disabled by default in ZCode; the hooks block
# must set enabled: true or the runner never fires.
/usr/bin/python3 - << PYEOF
import json, os, shutil, time

config_path = os.path.expanduser("~/.zcode/cli/config.json")
data = {}
if os.path.exists(config_path):
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

hooks = data.setdefault("hooks", {})
hooks["enabled"] = True
events = hooks.setdefault("events", {})

stop = events.setdefault("Stop", [])
audit_command = f"python3 {os.path.expanduser('~/.zcode/hooks/zcode-stop-audit.py')}"
audit_registered = any(
    "zcode-stop-audit.py" in hook.get("command", "")
    for entry in stop
    for hook in entry.get("hooks", [])
)

ups = events.setdefault("UserPromptSubmit", [])
reco_command = f"python3 {os.path.expanduser('~/.zcode/hooks/skill-recommender.py')} userpromptsubmit"
reco_registered = any(
    "skill-recommender.py" in hook.get("command", "")
    for entry in ups
    for hook in entry.get("hooks", [])
)

if audit_registered and reco_registered:
    print("✓ ZCode hooks already registered")
else:
    if os.path.exists(config_path):
        shutil.copy2(config_path, f"{config_path}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    if not audit_registered:
        stop.append({"hooks": [{"type": "command", "command": audit_command, "timeout": 60}]})
    if not reco_registered:
        ups.append({"hooks": [{"type": "command", "command": reco_command, "timeout": 15}]})
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("✓ Registered ZCode Stop audit and UserPromptSubmit skill router")
PYEOF

# 8. Git hooks for automatic post-commit and post-merge sync
GIT_DIR="$(git -C "$REPO_DIR" rev-parse --git-dir 2>/dev/null || true)"
if [[ -n "$GIT_DIR" && -d "$GIT_DIR/hooks" ]]; then
  if [[ -f "$REPO_DIR/scripts/git-hooks/post-commit" ]]; then
    cp -p "$REPO_DIR/scripts/git-hooks/post-commit" "$GIT_DIR/hooks/post-commit"
    chmod +x "$GIT_DIR/hooks/post-commit"
    echo "✓ Installed git post-commit hook"
  fi
  if [[ -f "$REPO_DIR/scripts/git-hooks/post-merge" ]]; then
    cp -p "$REPO_DIR/scripts/git-hooks/post-merge" "$GIT_DIR/hooks/post-merge"
    chmod +x "$GIT_DIR/hooks/post-merge"
    echo "✓ Installed git post-merge hook"
  fi
fi

echo "Verifying installation targets:"
ls -l "$HOME/.config/agy/agy-stop-audit.py" "$HOME/.gemini/config/hooks/agy-stop-audit.py" "$HOME/.gemini/config/hooks/command-timer.py" "$HOME/.config/agy/statusline.py" "$HOME/.qoder/hooks/qoder-stop-audit.py" "$HOME/.qoder/hooks/qoder-fork.py" "$HOME/.zcode/hooks/zcode-stop-audit.py" "$HOME/.zcode/hooks/skill-recommender.py"
echo "Installation complete."
