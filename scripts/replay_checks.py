"""Check catalog for the replay Space: turns grader check ids into short plain-English labels.

The grader (src/alertforge/grader/grade.py) reports atomic checks as ids such as
`probe:A1.silent.short_spike.0`, `R1:route:neg2` or `I1:inhibit:3`. This module maps each id to the
requirement it belongs to, a display group (alerts / repairs / recording / routing / inhibition /
preserved) and a label a reader can follow ("silent during a short spike", "routes to pagerduty-payments").
"""

from __future__ import annotations

import json
import os
import re

GROUP_OF_CATEGORY = {"req_alerts": "alerts", "req_repairs": "repairs", "req_recording": "recording",
                     "req_routing": "routing", "req_inhibit": "inhibition"}
GROUP_ORDER = ["alerts", "repairs", "recording", "routing", "inhibition", "preserved"]

PROBE_LABELS = {
    "fire.sustained": "fires during a sustained outage",
    "silent.sustained": "stays silent for the healthy service during the outage",
    "silent.isolation": "stays silent when a different service is failing",
    "silent.bracket_silent": "stays silent just under the threshold",
    "fire.bracket_fire": "fires just over the threshold",
    "silent.normal": "stays silent on normal traffic",
    "timing.onset": "fires on time (waits out `for`, no later)",
    "silent.short_spike": "stays silent during a short spike",
    "fire.long_spike": "fires on a long spike",
    "silent.recovered": "resolves after recovery",
    "silent.low_traffic": "stays silent on very low traffic",
    "fire.all_down": "fires when every instance is down",
    "fire.nodata": "fires when the series disappears",
    "silent.partial": "stays silent on a partial blip",
    "silent.flap": "stays silent while a target flaps",
    "silent.stable": "stays silent while stable",
    "fire.crashing": "fires while pods crash-loop",
    "silent.crashing": "stays silent for a single restart",
    "card.rec_phases": "one recorded series per service (outage)",
    "card.rec_steady": "one recorded series per service (steady)",
}


def _svc(expr: str) -> str:
    m = re.search(r'service(!?=)"([^"]+)"', expr or "")
    if not m:
        return ""
    return f"service {m.group(2)}" if m.group(1) == "=" else f"services other than {m.group(2)}"


def _alert_desc(labels: dict) -> str:
    name = labels.get("alertname", "alert")
    if name.startswith(("Synthetic", "Preserved")):
        name = f"a {labels.get('severity', '')} alert".replace("  ", " ")
    team = labels.get("team")
    return f"{name} (team {team})" if team else name


def _case_desc(c: dict) -> str:
    src, tgt = c["source"], c["target"]
    same = src.get("service") == tgt.get("service")
    where = f"on {tgt.get('service')}" if same else f"({src.get('service')} → {tgt.get('service')})"
    verb = "mutes" if c.get("expect") else "does not mute"
    return f"{src.get('alertname')} {verb} {tgt.get('alertname')} {where}"


def req_title(r: dict) -> str:
    k = r["kind"]
    if k == "new_alert":
        return f"add {r['alert']['name']}"
    if k == "repair":
        return f"fix {r['alert']['name']}"
    if k == "recording":
        return "record " + ", ".join(r["records"]["names"])
    if k == "route":
        rt = r["route"]
        team = (rt.get("chain") or {}).get("required_labels", {}).get("team") or \
            (rt["pos"][0]["labels"].get("team") if rt["pos"] else "")
        return f"route team {team}" if team else "routing"
    if k == "inhibit":
        srcs = sorted({c["source"].get("alertname", "") for c in r["inhibit"]["cases"] if c["expect"]})
        return "inhibit under " + ", ".join(srcs) if srcs else "inhibition"
    return r["id"]


def _probe_label(pid: str, probe: dict | None) -> tuple[str, str]:
    parts = pid.split(".")
    fam_kind = ".".join(parts[1:3])
    if parts[1] == "value":
        return f"recorded value is correct (case {int(parts[2]) + 1})", ""
    if parts[1] == "label" and parts[2:3] == ["dyn"]:
        return "the firing alert carries the required labels", ""
    if parts[1] == "annot" and len(parts) > 2 and parts[2].startswith("lbl_"):
        return f"annotation template resolves {{{{ $labels.{parts[2][4:]} }}}}", ""
    base = PROBE_LABELS.get(fam_kind, fam_kind.replace(".", " ").replace("_", " "))
    note = ""
    if probe:
        note = ", ".join(x for x in (_svc(probe.get("expr", "")), f"t={probe['t']}m" if "t" in probe else "") if x)
    return base, note


class Catalog:
    """Per-task check catalog: stable order, labels, grouping."""

    def __init__(self, task_dir: str):
        with open(os.path.join(task_dir, "tests", "spec.json")) as fh:
            self.spec = json.load(fh)
        with open(os.path.join(task_dir, "tests", "hidden", "scenarios.json")) as fh:
            self.groups = json.load(fh)
        self.reqs = {r["id"]: r for r in self.spec["requirements"]}
        self.probes = {p["id"]: p for g in self.groups for p in g["probes"]}
        self.entries: dict[str, dict] = {}
        self._seed()

    def requirements(self) -> list[dict]:
        return [{"id": r["id"], "group": GROUP_OF_CATEGORY[r["category"]], "title": req_title(r),
                 "weight": r["weight"]} for r in self.spec["requirements"]]

    def _seed(self) -> None:
        for r in self.spec["requirements"]:
            rid, k = r["id"], r["kind"]
            if k in ("new_alert", "repair"):
                for cid in ("label:static", "annot:summary", "annot:runbook"):
                    self.add(f"{rid}:{cid}")
                self.add(f"probe:{rid}.label.dyn")
            elif k == "route":
                n = len(r["route"]["pos"]) + (1 if r["route"].get("chain") else 0)
                for i in range(n):
                    self.add(f"{rid}:route:pos{i}")
                for i in range(len(r["route"]["neg"])):
                    self.add(f"{rid}:route:neg{i}")
            elif k == "inhibit":
                for i in range(len(self._inhibit_cases(r))):
                    self.add(f"{rid}:inhibit:{i}")
        for pid in self.probes:
            self.add(f"probe:{pid}")

    def _inhibit_cases(self, r: dict) -> list[dict]:
        cases = r["inhibit"]["cases"]
        pres = [c for c in self.spec.get("preserve", {}).get("inhibit_cases", []) if c.get("req") == r["id"]]
        return [c for c in cases if c["expect"]] + [c for c in cases if not c["expect"]] + pres

    def add(self, cid: str) -> dict:
        if cid in self.entries:
            return self.entries[cid]
        e = self._describe(cid)
        e["id"] = cid
        self.entries[cid] = e
        return e

    def _describe(self, cid: str) -> dict:
        if cid.startswith("pres:"):
            item = cid[5:]
            kind, _, name = item.partition(":")
            pres = self.spec.get("preserve", {})
            if kind == "rule":
                label = f"leaves rule {name} intact"
            elif item == "am:global":
                label = "leaves the Alertmanager global block intact"
            elif kind == "am" and name.startswith("receiver:"):
                label = f"leaves receiver {name[9:]} intact"
            elif kind == "route_case":
                cases = pres.get("route_cases", [])
                i = int(name)
                label = (f"existing routing kept: {_alert_desc(cases[i]['labels'])} → {', '.join(cases[i]['expect'])}"
                         if i < len(cases) else f"existing route case {i} kept")
            elif kind == "inhibit_case":
                cases = pres.get("inhibit_cases", [])
                i = int(name)
                label = "existing inhibition kept: " + _case_desc(cases[i]) if i < len(cases) else item
            else:
                label = f"keeps {item}"
            return {"req": "", "group": "preserved", "label": label}
        if cid.startswith("probe:"):
            pid = cid[6:]
            rid = pid.split(".")[0]
            label, note = _probe_label(pid, self.probes.get(pid))
            r = self.reqs.get(rid)
            return {"req": rid, "group": GROUP_OF_CATEGORY.get(r["category"], "alerts") if r else "alerts",
                    "label": label, "note": note}
        rid, _, rest = cid.partition(":")
        r = self.reqs.get(rid)
        group = GROUP_OF_CATEGORY.get(r["category"], "alerts") if r else "alerts"
        label, note = rest, ""
        if rest == "label:static" and r and r.get("alert"):
            label = "rule has labels " + ", ".join(f"{k}={v}" for k, v in r["alert"]["labels"].items())
        elif rest == "annot:summary":
            label = "has a summary annotation"
        elif rest == "annot:runbook" and r and r.get("alert"):
            label = "runbook_url points at the right runbook"
            note = r["alert"]["runbook"]
        elif rest.startswith("route:pos") and r:
            i = int(rest[9:])
            pos = r["route"]["pos"]
            if i < len(pos):
                label = f"{_alert_desc(pos[i]['labels'])} routes to {', '.join(pos[i]['expect'])}"
            else:
                ch = r["route"]["chain"]
                label = f"the new {ch['alert']} alert, as written, routes to {', '.join(ch['expect'])}"
        elif rest.startswith("route:neg") and r:
            c = r["route"]["neg"][int(rest[9:])]
            label = f"{_alert_desc(c['labels'])} does not reach {', '.join(c['forbid'])}"
        elif rest.startswith("inhibit:") and r:
            cases = self._inhibit_cases(r)
            i = int(rest[8:])
            label = _case_desc(cases[i]) if i < len(cases) else rest
        return {"req": rid, "group": group, "label": label, "note": note}

    # -------------------------------------------------------------- states
    def states(self, details: dict | None) -> dict[str, bool]:
        """{check id: passed} from a grader details.json (adds route:pos and preservation items)."""
        if not details:
            return {}
        out: dict[str, bool] = {}
        for k, v in (details.get("checks") or {}).items():
            out[k] = bool(v)
        for rid, rd in (details.get("requirements") or {}).items():
            fams = rd.get("families") or {}
            for i, v in enumerate(fams.get("pos") or []):
                out[f"{rid}:route:pos{i}"] = float(v) >= 1 - 1e-9
        for it in details.get("preservation_items") or []:
            out[f"pres:{it['item']}"] = bool(it.get("ok"))
        if details.get("tamper"):
            out = {k: False for k in self.entries}
        for k in out:
            self.add(k)
        return out

    def export(self) -> list[dict]:
        req_order = {rid: i for i, rid in enumerate(self.reqs)}
        items = list(self.entries.values())
        items.sort(key=lambda e: (GROUP_ORDER.index(e["group"]), req_order.get(e["req"], 99)))
        return [{k: v for k, v in e.items() if v != ""} for e in items]
