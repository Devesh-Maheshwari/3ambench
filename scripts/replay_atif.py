"""Read Harbor ATIF trajectories (agent/trajectory.json) and recover the file edits an agent made.

Supported edit shapes (anything else that looks like it modifies files marks the replay approximate):
  - structured write/edit tools: Write, Edit, MultiEdit, write_file, replace_in_file, str_replace, create_file
  - apply_patch (V4A patch text), as a tool or as `apply_patch <<'EOF'` inside a shell command
  - shell heredocs: `cat > path <<'EOF' ... EOF` and `cat <<'EOF' > path`
Paths are resolved against /workspace/monitoring (the task's working directory).
"""

from __future__ import annotations

import json
import re
import shlex

WS = "/workspace/monitoring"
SHELL_TOOLS = {"bash", "shell", "exec_command", "bash_command", "run_terminal_cmd", "local_shell", "terminal",
               "run_shell_command", "execute_command"}
_HEREDOC = re.compile(r"cat\s+(?:>\s*(?P<p1>\S+)\s+<<-?\s*(?P<q1>['\"]?)(?P<t1>\w+)(?P=q1)"
                      r"|<<-?\s*(?P<q2>['\"]?)(?P<t2>\w+)(?P=q2)\s*>\s*(?P<p2>\S+))[^\n]*\n")
_PATCH_HEREDOC = re.compile(r"apply_patch\s+<<-?\s*(['\"]?)(\w+)\1[^\n]*\n")
_MUTATING = re.compile(r"(?<![0-9&])>(?!\s*/dev/null)(?!&)|\bsed\s+-i|\btee\b|\bmv\s|\bcp\s|\brm\s|\bpython3?\s+-c"
                       r"|\bperl\s+-[pi]|\bapply_patch\b|\btruncate\b|\bpatch\s")


def rel_path(p: str | None) -> str | None:
    """Workspace-relative path, or None when the path is outside /workspace/monitoring."""
    if not p:
        return None
    p = p.strip().strip("'\"")
    if p.startswith(WS + "/"):
        p = p[len(WS) + 1:]
    elif p.startswith("/"):
        return None
    p = re.sub(r"^\./", "", p)
    return None if ".." in p.split("/") else p


def command_text(args: dict) -> str:
    c = args.get("command", args.get("cmd", args.get("keystrokes", args.get("input", ""))))
    if isinstance(c, list):
        c = c[-1] if len(c) >= 3 and c[0] in ("bash", "sh", "zsh") and c[1] in ("-lc", "-c") else shlex.join(c)
    return str(c or "")


def shell_ops(cmd: str) -> tuple[list[tuple], bool]:
    """Edits recoverable from a shell command, plus whether an unrecovered write may remain."""
    ops, rest, pos = [], [], 0
    while True:
        m_h, m_p = _HEREDOC.search(cmd, pos), _PATCH_HEREDOC.search(cmd, pos)
        m = min((x for x in (m_h, m_p) if x), key=lambda x: x.start(), default=None)
        if m is None:
            rest.append(cmd[pos:])
            break
        tag = m.group(2) if m is m_p else (m.group("t1") or m.group("t2"))
        end = re.compile(rf"^\s*{re.escape(tag)}\s*$", re.M).search(cmd, m.end())
        body = cmd[m.end():end.start() if end else len(cmd)]
        rest.append(cmd[pos:m.start()])
        if m is m_p:
            ops.append(("patch", body))
        else:
            path = rel_path(m.group("p1") or m.group("p2"))
            if path:
                ops.append(("write", path, body))
        pos = end.end() if end else len(cmd)
    leftover = "".join(rest)
    return ops, bool(_MUTATING.search(leftover))


def call_ops(name: str, args: dict) -> tuple[list[tuple], bool]:
    """(edit ops, maybe_unreplayed) for one tool call."""
    n = (name or "").lower()
    path = rel_path(args.get("file_path") or args.get("path") or args.get("filename"))
    if n in ("write", "write_file", "create_file", "create") and "content" in args:
        return ([("write", path, str(args["content"]))] if path else []), False
    if n in ("edit", "str_replace", "replace_in_file", "str_replace_editor", "edit_file"):
        old = args.get("old_string", args.get("old", args.get("old_str")))
        new = args.get("new_string", args.get("new", args.get("new_str")))
        if path and old is not None and new is not None:
            return [("replace", path, str(old), str(new), bool(args.get("replace_all")))], False
        return [], bool(path)
    if n == "multiedit":
        return ([("replace", path, str(e.get("old_string", "")), str(e.get("new_string", "")),
                  bool(e.get("replace_all"))) for e in args.get("edits") or []] if path else []), False
    if n == "apply_patch":
        return [("patch", str(args.get("input") or args.get("patch") or command_text(args)))], False
    if n in SHELL_TOOLS:
        return shell_ops(command_text(args))
    return [], False


def apply_v4a(files: dict, patch: str) -> tuple[list[tuple], bool]:
    """Turn a V4A patch into write/delete ops against `files` ({rel: text}); returns (ops, ok)."""
    ops, ok = [], True
    lines = patch.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("*** Add File: "):
            path, body = rel_path(ln[14:]), []
            i += 1
            while i < len(lines) and not lines[i].startswith("*** "):
                body.append(lines[i][1:] if lines[i].startswith("+") else lines[i])
                i += 1
            if path:
                ops.append(("write", path, "\n".join(body) + "\n"))
            continue
        if ln.startswith("*** Delete File: "):
            path = rel_path(ln[17:])
            if path:
                ops.append(("delete", path))
            i += 1
            continue
        if ln.startswith("*** Update File: "):
            path, target = rel_path(ln[17:]), None
            i += 1
            if i < len(lines) and lines[i].startswith("*** Move to: "):
                target = rel_path(lines[i][13:])
                i += 1
            hunks, cur = [], []
            while i < len(lines) and not (lines[i].startswith("*** ") and lines[i] != "*** End of File"):
                if lines[i].startswith("@@"):
                    if cur:
                        hunks.append(cur)
                    cur = []
                elif lines[i] != "*** End of File":
                    cur.append(lines[i])
                i += 1
            if cur:
                hunks.append(cur)
            text = files.get(path) if path else None
            if text is None:
                ok = False
                continue
            cursor = 0
            for h in hunks:
                old = "\n".join(x[1:] for x in h if x[:1] in (" ", "-") or x == "")
                new = "\n".join(x[1:] for x in h if x[:1] in (" ", "+") or x == "")
                at = text.find(old, cursor) if old else len(text)
                if at < 0:
                    ok = False
                    continue
                text = text[:at] + new + text[at + len(old):]
                cursor = at + len(new)
            if target and target != path:
                ops.append(("delete", path))
            ops.append(("write", target or path, text))
            continue
        i += 1
    return ops, ok


def load_trajectory(path: str) -> dict:
    with open(path) as fh:
        return json.load(fh)


def text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text")
    return str(content)


def iter_events(traj: dict):
    """Yield ('think', text) | ('call', id, name, args) | ('obs', id, text) in trajectory order.

    The first user message (the task instruction) and system messages are skipped.
    """
    seen_user = False
    for st in traj.get("steps") or []:
        src = st.get("source")
        if src == "system":
            continue
        if src == "user":
            if seen_user:
                yield ("think", "[user] " + text_of(st.get("message")))
            seen_user = True
            continue
        thought = "\n\n".join(x for x in (st.get("reasoning_content") or "", text_of(st.get("message"))) if x.strip())
        if thought.strip():
            yield ("think", thought)
        calls = st.get("tool_calls") or []
        for c in calls:
            yield ("call", c.get("tool_call_id"), c.get("function_name", ""), c.get("arguments") or {})
        results = ((st.get("observation") or {}).get("results")) or []
        for k, r in enumerate(results):
            cid = r.get("source_call_id") or (calls[k].get("tool_call_id") if k < len(calls) else None)
            yield ("obs", cid, text_of(r.get("content")))
