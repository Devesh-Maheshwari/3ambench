#!/usr/bin/env python3
"""Scrub credentials and local paths from text that is about to be published.

    from redact import redact            # used by export_runs.py before writing runs.json
    python scripts/redact.py --check DIR  # exit 1 if anything under DIR still looks like a secret

Catches known key formats (OpenAI, Anthropic, Together, OpenRouter, HF, GitHub, AWS, Google, Slack),
JWT/OAuth tokens, `KEY=value` style assignments for secret-looking names, the exact values of any
secret-looking environment variables set in this shell, and /Users/<name> or /home/<name> paths.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

MASK = "[REDACTED]"

PATTERNS = [
    r"sk-ant-[A-Za-z0-9_\-]{20,}",                 # Anthropic keys and OAuth tokens
    r"sk-or-[A-Za-z0-9_\-]{20,}",                  # OpenRouter
    r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{20,}",  # OpenAI
    r"tgp_v1_[A-Za-z0-9_\-]{20,}",                 # Together
    r"hf_[A-Za-z0-9]{30,}",                        # Hugging Face
    r"gh[pousr]_[A-Za-z0-9]{30,}",                 # GitHub tokens
    r"github_pat_[A-Za-z0-9_]{40,}",
    r"AKIA[0-9A-Z]{16}",                           # AWS access key id
    r"AIza[0-9A-Za-z_\-]{35}",                     # Google API key
    r"xox[abprs]-[A-Za-z0-9\-]{10,}",              # Slack
    r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",  # JWT (ChatGPT/Codex auth)
    r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}",
]
SECRET_NAME = r"[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|ACCESS_KEY|AUTH)[A-Z0-9_]*"
ASSIGN = re.compile(r"(\b" + SECRET_NAME + r"\b\s*[=:]\s*[\"']?)([^\s\"',}]{8,})")
JSON_FIELD = re.compile(r'((?:\\?")(?:access_token|refresh_token|id_token|api_key|OPENAI_API_KEY|token)(?:\\?")\s*:\s*(?:\\?"))([^"\\]{8,})')
HOME = re.compile(r"/(?:Users|home)/[A-Za-z0-9._\-]+")
COMPILED = [re.compile(p) for p in PATTERNS]


def _env_secrets() -> list[str]:
    name = re.compile("^" + SECRET_NAME + "$")
    return sorted({v for k, v in os.environ.items() if name.match(k) and len(v) >= 8}, key=len, reverse=True)


def redact(text: str) -> str:
    for v in _env_secrets():
        text = text.replace(v, MASK)
    for rx in COMPILED:
        text = rx.sub(MASK, text)
    text = ASSIGN.sub(lambda m: m.group(1) + MASK, text)
    text = JSON_FIELD.sub(lambda m: m.group(1) + MASK, text)
    return HOME.sub("/home/user", text)


def findings(text: str) -> list[str]:
    hits = [m.group(0) for rx in COMPILED for m in rx.finditer(text)]
    hits += [m.group(0) for m in ASSIGN.finditer(text) if m.group(2) != MASK]
    hits += [m.group(0) for m in JSON_FIELD.finditer(text) if m.group(2) != MASK]
    hits += [v for v in _env_secrets() if v in text]
    hits += [m.group(0) for m in HOME.finditer(text)]
    return hits


def check(root: str) -> int:
    bad = 0
    for dirpath, _, files in os.walk(root):
        if "/.git" in dirpath:
            continue
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                text = open(p, encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                continue
            for h in findings(text):
                bad += 1
                print(f"{p}: looks like a secret or local path: {h[:12]}...", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", required=True, help="directory to scan before publishing")
    sys.exit(check(ap.parse_args().check))
