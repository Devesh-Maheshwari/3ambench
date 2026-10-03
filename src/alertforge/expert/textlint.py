"""Gate lints that read a built task the way a careful on-call engineer would and fail on contradictions between
its packets, its text and the repo it ships (R4, R6, FA-4, BM-6, S7). Text only, no promtool.

- paged team:   a page in a PagerDuty export or Slack channel names the team its alert carries in the starting repo
- alert exists: every alert a packet shows paging exists in the starting repo (or is the cluster-level alert)
- anachronism:  a packet dated before the split never says the new team was (or wasn't) paged
- rule dumps:   a rule-manager log or rules API paste shows the starting repo's labels for that rule
- deploy tags:  a release tag is never dated after its deploy
- deploy hours: rollouts start in office hours on working days (one evening hotfix per log allowed)
- numbering:    INC and PagerDuty numbers grow with time across the task
- recency:      "this week" points at packets from this week
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re

TITLE = re.compile(r"\[(?:FIRING:\d+|RESOLVED)\] ([A-Z][A-Za-z0-9]+)(?: ([a-z][\w.-]*))?(?: \(([^)]*)\))?")
TAG = re.compile(r"^(\d{4}-\d\d-\d\d)T(\d\d):(\d\d):\d\d[+-]\d\d:\d\d rollouts \S+ v(\d{4})\.(\d\d)\.(\d\d)-\d+ (.*)$")


def _pristine_labels(m: dict) -> dict[str, list[dict]]:
    from .then import state
    files, _ = state(m["world"])
    out: dict[str, list[dict]] = {}
    for f in files.values():
        for g in f.groups:
            for r in g["rules"]:
                if "alert" in r:
                    out.setdefault(r["alert"], []).append({**(g.get("labels") or {}), **(r.get("labels") or {})})
    return out


def _titles(path: str, text: str):
    """(title, pagerduty service or None, created_on or None) from an export or a channel dump."""
    if path.endswith(".csv") and text.startswith("incident_number"):
        for row in csv.DictReader(io.StringIO(text)):
            yield row["title"], row["service_name"].split(" ")[0], row["created_on"]
    elif path.endswith(".txt"):
        for line in text.splitlines():
            yield line, None, None


def paged_team_lint(m: dict) -> list[str]:
    w, ws = m["world"], m["static"]
    labels = _pristine_labels(m)
    cluster = set(w.cluster_level)
    out = []
    for path, text in ws.items():
        if not path.startswith("incidents/"):
            continue
        for title, _pd, _ in _titles(path, text):
            mm = TITLE.search(title)
            if not mm:
                continue
            alert, _svc, rest = mm.groups()
            if alert not in labels:
                if alert not in cluster:
                    out.append(f"{path}: {alert} pages in the packet but the starting repo has no such alert")
                continue
            teams = {str(lab.get("team")) for lab in labels[alert] if lab.get("team")}
            vals = (rest or "").split()
            if teams and vals and vals[-1] not in teams:
                out.append(f"{path}: {alert} shown with team {vals[-1]!r}, the starting repo labels it {sorted(teams)}")
    return out


def rule_dump_lint(m: dict) -> list[str]:
    labels = _pristine_labels(m)
    out = []
    for path, text in m["static"].items():
        if path.endswith("prometheus.log"):
            for mm in re.finditer(r"name=(\w+) index=\d+ rule=\"(.*?)\" err=", text):
                team = re.search(r"\\n  team: ([\w-]+)", mm.group(2))
                want = {str(x.get("team")) for x in labels.get(mm.group(1), [])}
                if team and want and team.group(1) not in want:
                    out.append(f"{path}: log shows team {team.group(1)} on {mm.group(1)}, the repo has {sorted(want)}")
        elif path.endswith("rules-api.json"):
            import json
            for g in json.loads(text)["data"]["groups"]:
                for r in g["rules"]:
                    want = {str(x.get("team")) for x in labels.get(r["name"], [])}
                    if want and r["labels"].get("team") not in want:
                        out.append(f"{path}: rules API shows team {r['labels'].get('team')} on {r['name']}")
    return out


EVENT_FILES = ("timeline.md", "postmortem.md", "prometheus.log", "rules-api.json", "alerts-explore.csv", "deploys.log",
               "slack-alerts.txt", "app.log")


def _packet_days(ws: dict, inc: str) -> list[dt.date]:
    """The days an incident happened on: what its own event files say (a PagerDuty export also has the pages of the
    days around it, and an E3 channel export two weeks of history, so those only count when nothing else does)."""
    own, rest = set(), set()
    for path, text in ws.items():
        if path.startswith(f"incidents/{inc}/"):
            days = {dt.date.fromisoformat(d) for d in re.findall(r"(20\d\d-\d\d-\d\d)", text[:40000])}
            (own if path.rsplit("/", 1)[-1] in EVENT_FILES else rest).update(days)
    return sorted(own) if own else sorted(rest)[-1:]


def anachronism_lint(m: dict) -> list[str]:
    w, ws = m["world"], m["static"]
    tr = w.trigger or {}
    if not tr.get("moved"):
        return []
    new, day = tr["new"], tr.get("day")
    out = []
    pat = re.compile(rf"\b{re.escape(new)}( rota)? (were|wasn't|was|weren't|never|on-call)\b|paged {re.escape(new)}\b"
                     rf"|\(page {re.escape(new)}\)|page {re.escape(new)}\)")
    for inc in sorted({p.split("/")[1] for p in ws if p.startswith("incidents/")}):
        days = _packet_days(ws, inc)
        if not days or day is None or min(days) >= day:
            continue
        for path, text in ws.items():
            if path.startswith(f"incidents/{inc}/") and (path.endswith(".md") or path.endswith(".txt") or path.endswith(".csv")):
                hit = pat.search(text)
                if hit:
                    out.append(f"{path}: dated {min(days)} (before the split on {day}) but says {hit.group(0)!r}")
    return out


def deploy_lint(m: dict) -> list[str]:
    out = []
    for path, text in m["static"].items():
        if not path.endswith("deploys.log"):
            continue
        evening = 0
        for line in text.splitlines():
            mm = TAG.match(line)
            if not mm:
                continue
            day = dt.date.fromisoformat(mm.group(1))
            tag = dt.date(int(mm.group(4)), int(mm.group(5)), int(mm.group(6)))
            if tag > day:
                out.append(f"{path}: release {tag} rolled out on {day}")
            if "started" in mm.group(7):
                h = int(mm.group(2))
                if day.weekday() >= 5 or h < 8:
                    out.append(f"{path}: rollout started {day:%a} {mm.group(2)}:{mm.group(3)}")
                elif h >= 19:
                    evening += 1
        if evening > 1:
            out.append(f"{path}: {evening} rollouts started in the evening")
    return out


def numbering_lint(m: dict) -> list[str]:
    ws = m["static"]
    out = []
    incs = []
    for inc in sorted({p.split("/")[1] for p in ws if p.startswith("incidents/INC-")}):
        days = _packet_days(ws, inc)
        if days:   # an INC is opened once the thing is noticed: the last day of a packet that spans several
            incs.append((int(inc[4:]), days[-1], inc))
    incs.sort()
    for (a, da, ia), (b, db, ib) in zip(incs, incs[1:]):
        if db < da - dt.timedelta(days=1):
            out.append(f"{ib} ({db}) is numbered after {ia} ({da})")
    firsts = []
    for path, text in ws.items():
        if path.endswith(".csv") and text.startswith("incident_number"):
            rows = list(csv.DictReader(io.StringIO(text)))
            nums = [int(r["incident_number"]) for r in rows]
            if nums != sorted(nums) or len(set(nums)) != len(nums):
                out.append(f"{path}: incident numbers not increasing")
            if rows:
                firsts.append((int(rows[0]["incident_number"]), rows[0]["created_on"], path))
    firsts.sort()
    for (a, ca, pa), (b, cb, pb) in zip(firsts, firsts[1:]):
        if cb < ca and dt.datetime.fromisoformat(ca[:-1]) - dt.datetime.fromisoformat(cb[:-1]) > dt.timedelta(days=1):
            out.append(f"{pb}: #{b} created {cb} but numbered above #{a} ({ca}, {pa})")
    return out


def recency_lint(m: dict) -> list[str]:
    w = m["world"]
    today = w.cal.today
    monday = today - dt.timedelta(days=today.weekday())
    out = []
    for t in m["tickets"].values():
        if "this week" not in t.body:
            continue
        for inc in set(re.findall(r"incidents/([\w-]+)/", t.body)):
            days = _packet_days(m["static"], inc)
            if days and min(days) < monday - dt.timedelta(days=1):
                out.append(f"{t.key} says 'this week' about {inc}, dated {min(days)} (week starts {monday})")
    return out


def lint(m: dict) -> list[str]:
    return (paged_team_lint(m) + rule_dump_lint(m) + anachronism_lint(m) + deploy_lint(m) + numbering_lint(m)
            + recency_lint(m))
