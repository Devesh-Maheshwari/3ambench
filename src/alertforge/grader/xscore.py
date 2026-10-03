"""Scoring of expert-tier requirement kinds from promtool snapshot results (see xgrade.py for the flow).

Outcome requirements count *delivered* alerts: each snapshot is filtered through the inhibition simulator
before anything is counted, so an over-broad inhibition cannot make a quiet pager look fixed.
"""

from __future__ import annotations

import json

from . import amcheck, notify
from .norm import effective
from .static import template_labels

CLASS_SEV = {"page": {"page", "critical"}, "ticket": {"ticket"}}
PAGER = "pagerduty-"


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 1.0


def labels_cover(labels: dict | None, required: dict) -> bool:
    """The expert README's label rule: every alert carries the labels it names (`severity`, `team`, `service` for
    service alerts, `slo` for burn alerts), with those values. It never says "only these", so an alert may carry
    more; whether extra labels change where it goes is graded on delivery (Rt, route checks, `_routes_alike`)."""
    return isinstance(labels, dict) and all(k in labels and str(labels[k]) == str(v) for k, v in required.items())


def counted(alerts: list[dict], svc: str, oc: dict, cls: str | None = None, extra: set | None = None) -> list[dict]:
    sev = CLASS_SEV[cls or oc["services"][svc]["class"]]
    named = oc.get("alertname")
    excl = set(oc.get("exclude", [])) | (extra or set())
    return [a for a in alerts if a.get("service") == svc and a.get("severity") in sev
            and a.get("alertname") not in excl and (named is None or a.get("alertname") == named)]


class Ctx:
    """What the scorers need from one grade: parsed rules, AM config, promtool results, second passes."""

    def __init__(self, alerts, am, am_path, am_text, results, masks, rerun):
        self.alerts, self.am, self.am_path, self.am_text = alerts, am, am_path, am_text
        self.results, self.masks, self.rerun = results, masks, rerun

    def snap(self, group: str, pid: str):
        res = self.results.get(group)
        return None if res is None else res.get(pid)

    def delivered(self, alerts: list[dict]) -> list[dict] | None:
        if not isinstance(self.am, dict):
            return None
        return notify.delivered(self.am, alerts)

    def receivers(self, labels: dict) -> list[str] | None:
        if not isinstance(self.am, dict):
            return None
        return amcheck.resolve(self.am_path, self.am_text, labels)


def window_quiet(ctx: Ctx, g: dict, p: dict, pick) -> bool:
    """HS-2: a window probe passes if nothing counted fired; otherwise re-check minute by minute after
    inhibition (a counted alert that was inhibited by a real source in the same minute is not delivered)."""
    got = ctx.snap(g["name"], p["id"])
    if got is None:
        return False
    if not pick(got):
        return True
    snaps = ctx.rerun(g, p["t0"], p["t"])
    if snaps is None:
        return False
    for alerts in snaps:
        live = ctx.delivered(alerts)
        if live is None or pick(live):
            return False
    return True


def labelless_ok(ctx: Ctx, g: dict, cluster_level: set[str]) -> bool:
    """G: no label-less alert that reaches a pager, outside the cluster-level list and the group's exempt
    names (alertnames that fire label-less in the pristine or oracle state of this scenario)."""
    p = next((x for x in g["probes"] if x.get("family") == "G"), None)
    if p is None:
        return True
    got = ctx.snap(g["name"], p["id"])
    if got is None:
        return False
    exempt = set(g.get("g_exempt", [])) | cluster_level
    for a in got:
        if a.get("alertname") in exempt:
            continue
        recv = ctx.receivers(a)
        if recv is None or any(r.startswith(PAGER) for r in recv):
            return False
    return True


def outcome_q(r: dict, groups: list[dict], ctx: Ctx, cluster_level: set[str]) -> tuple[float, dict]:
    oc = r["outcome"]
    named = oc.get("alertname")
    exists = named is None or bool(ctx.alerts.get(named))
    touch = set(oc.get("touch", []))
    fam: dict[str, list[float]] = {"F_detect": [], "F_regress": [], "S_defect": [], "S_std": []}
    rt, lab, gl, dup = [], [], [], 1.0
    seen: set[str] = set()
    noisy: list[str] = []
    for g in groups:
        masked = set(ctx.masks.get(g["name"], []))
        own = [p for p in g["probes"] if p.get("family") in fam]
        if not own:
            continue
        gl.append(1.0 if labelless_ok(ctx, g, cluster_level) else 0.0)
        bg = set(g.get("o_exempt", [])) - touch
        for p in own:
            if p["type"] == "count":
                fam[p["family"]].append(1.0 if ctx.snap(g["name"], p["id"]) is True else 0.0)
                continue
            svc = p["svc"]
            if p["family"].startswith("F"):
                hits = []
                for pid in p.get("any_of", [p["id"]]):
                    got = ctx.snap(g["name"], pid)
                    live = ctx.delivered(got) if got is not None else None
                    if live is not None:
                        hits.extend(counted(live, svc, oc, p.get("class"), bg))
                fam[p["family"]].append(1.0 if (hits and exists) else 0.0)
                seen.update(a["alertname"] for a in hits)
                if hits:
                    want = oc["services"][svc] if not p.get("class") else oc["classes"][p["class"]]
                    rt.append(_route_ok(ctx, hits, want))
                    lab.append(_labels_ok(ctx, hits, want, oc))
                    if len({a["alertname"] for a in hits}) > oc.get("expected_count", 1):
                        dup = 0.8
            else:
                quiet = not (masked & touch) and window_quiet(
                    ctx, g, p, lambda xs, s=svc, c=p.get("class"): counted(xs, s, oc, c, bg))
                fam[p["family"]].append(1.0 if quiet else 0.0)
                if not quiet:
                    got = ctx.snap(g["name"], p["id"]) or []
                    noisy.append(f"{p['id']}:" + ",".join(sorted({a.get('alertname', '') for a in counted(got, svc, oc, p.get('class'), bg)})))
    q = 1.0
    for v in fam.values():
        q *= _mean(v)
    G, Rt, L = _mean(gl), _mean(rt), _mean(lab)
    q *= G * dup * (0.6 + 0.25 * Rt + 0.15 * L)
    return q, {**{k: round(_mean(v), 4) for k, v in fam.items()}, "G": G, "dup": dup, "Rt": round(Rt, 4),
               "L": round(L, 4), "exists": exists, "counted": sorted(seen), "noisy": noisy[:6]}


def _route_ok(ctx: Ctx, hits: list[dict], want: dict) -> float:
    ok = []
    for a in hits:
        recv = ctx.receivers(a)
        if recv is None:
            ok.append(0.0)
            continue
        good = set(want["expect"]) <= set(recv)
        stray = any(x.startswith(PAGER) and x not in want["expect"] for x in recv)
        ok.append(1.0 if good and not stray else 0.0)
    return _mean(ok)


def _merged(d):
    """A rule reference whose labels include its group's `labels:` (the rule's own value wins)."""
    from .loader import RuleRef
    g = d.group.get("labels") if isinstance(d.group.get("labels"), dict) else {}
    r = d.rule.get("labels") if isinstance(d.rule.get("labels"), dict) else {}
    if not g:
        return d
    return RuleRef(d.file, d.group, {**d.rule, "labels": {**g, **r}})


def _labels_ok(ctx: Ctx, hits: list[dict], want: dict, oc: dict) -> float:
    ok = []
    conv = want["labels"]
    for a in hits:
        defs = [_merged(d) for d in ctx.alerts.get(a["alertname"], [])]
        good = False
        for d in defs:
            lab = d.rule.get("labels") if isinstance(d.rule.get("labels"), dict) else {}
            lab = {str(k): str(v) for k, v in lab.items()}
            if lab.get("service") == a.get("service"):
                lab.pop("service")
            ann = d.rule.get("annotations") if isinstance(d.rule.get("annotations"), dict) else {}
            if (labels_cover(lab, conv) and str(ann.get("summary", "")).strip()
                    and str(ann.get("runbook_url", "")).strip() == oc["runbook_prefix"] + a["alertname"]):
                good = True
        ok.append(1.0 if good else 0.0)
    return _mean(ok)


def storm_q(r: dict, groups: list[dict], ctx: Ctx) -> tuple[float, dict]:
    st = r["storm"]
    bound, paged, split = [], [], []
    for g in groups:
        sg = g.get("storm")
        if not sg:
            continue
        snaps = [ctx.snap(g["name"], p["id"]) for p in g["probes"] if p.get("family") == "storm"]
        notes = None
        if isinstance(ctx.am, dict) and all(s is not None for s in snaps):
            notes = notify.notifications(ctx.am, ctx.am_path, ctx.am_text, snaps)
        if notes is None:
            bound.append(0.0), paged.append(0.0), split.append(0.0)
            continue
        pages = [n for n in notes if n[0].startswith(PAGER)]
        b_ok, p_ok = True, True
        muted = set(sg.get("muted", []))
        for svc in sg["failing"]:
            owner = PAGER + st["services"][svc]["team"]
            mine = [n for n in pages if svc in n[3]]
            b_ok &= len(mine) <= 1
            # a held-back service pages nobody (a stale team label paging the old rota is still a page)
            p_ok &= (not mine) if svc in muted else any(n[0] == owner for n in mine)
        if sg.get("cluster"):
            p_ok &= any(n[0] == sg["cluster"] for n in pages)
        bound.append(1.0 if b_ok else 0.0)
        paged.append(1.0 if p_ok else 0.0)
        if sg.get("split"):
            split.append(0.0 if any(len(n[3] & set(sg["failing"])) > 1 for n in pages) else 1.0)
    q = _mean(bound) * _mean(paged) * _mean(split)
    return q, {"bound_ok": round(_mean(bound), 4), "owner_paged": round(_mean(paged), 4),
               "split_ok": round(_mean(split), 4)}


def _routes_alike(ctx, name: str, svc: str | None, lab: dict, required: dict) -> bool:
    """Extra labels on a named alert are fine when they don't change where it goes (its routing is not otherwise
    graded): the receivers with them equal the receivers with the required labels alone."""
    extra = {k: v for k, v in lab.items() if k not in required and k != "service"}
    if not extra or ctx is None or not isinstance(ctx.am, dict):
        return True
    base = {"alertname": name, **({"service": svc} if svc else {}), **{k: str(v) for k, v in required.items()}}
    a, b = ctx.receivers({**base, **extra}), ctx.receivers(base)
    return a is not None and b is not None and sorted(set(a)) == sorted(set(b))


def _best_static(defs: list, a: dict, svc: str | None, ctx=None):
    """README reading of a named alert's static parts: the required labels with their values (others allowed when
    they don't change where it goes; a static `service` equal to the alert's own service is fine either way), the
    summary names the service (templated or written out, like the Down alerts do), and the runbook URL."""
    best, best_res = None, {"labels": False, "summary": False, "runbook": False}
    for d in defs:
        lab = d.rule.get("labels")
        lab = {str(k): str(v) for k, v in lab.items()} if isinstance(lab, dict) else None
        if lab is not None and svc and lab.get("service") == svc:
            lab.pop("service")
        ann = d.rule.get("annotations") if isinstance(d.rule.get("annotations"), dict) else {}
        s = ann.get("summary")
        res = {"labels": lab is not None and labels_cover(lab, a["labels"])
                         and _routes_alike(ctx, a["name"], svc, lab, a["labels"]),
               "summary": isinstance(s, str) and s.strip() != "" and (
                   "service" in template_labels({"annotations": {"s": s}}) or bool(svc and svc in s)),
               "runbook": str(ann.get("runbook_url", "")).strip() == a["runbook"]}
        if best is None or sum(res.values()) > sum(best_res.values()):
            best, best_res = d, res
    return best, best_res


def alert_q(r: dict, groups: list[dict], ctx: Ctx) -> tuple[float, dict]:
    """Named alerts (core-style count probes): E · dup · fire · silent · (0.7 + 0.3·labels/annotations)."""
    a = r["alert"]
    defs = [_merged(d) for d in ctx.alerts.get(a["name"], [])]
    svc = a.get("service") or next((p["svc"] for g in groups for p in g["probes"] if p.get("svc")), None)
    best, res = _best_static(defs, a, svc, ctx)
    fire, silent, dyn = [], [], []
    for g in groups:
        for p in g["probes"]:
            got = ctx.snap(g["name"], p["id"])
            if p.get("family") in ("fire", "timing"):
                fire.append(1.0 if got is True else 0.0)
            elif p.get("family") == "silent":
                silent.append(1.0 if got is True else 0.0)
            elif p.get("family") == "dyn":
                mine = [x for x in (got or []) if x.get("alertname") == a["name"] and x.get("service") == p["svc"]]
                refs = template_labels(best.rule) if best is not None else set()
                dyn.append(1.0 if any(all(x.get(k) == v for k, v in a["labels"].items())
                                      and all(x.get(l) for l in refs) for x in mine) else 0.0)
    refs_ok = _mean(dyn) if dyn else 1.0
    E = 1.0 if defs else 0.0
    # identical copies fire as one alert (Alertmanager deduplicates them): only distinct definitions count
    distinct = {json.dumps(effective(x.rule, x.group), sort_keys=True, default=str) for x in defs}
    d = 0.8 if len(distinct) > a.get("expected_count", 1) else 1.0
    la = _mean([1.0 if res["labels"] else 0.0, 1.0 if res["summary"] else 0.0, 1.0 if res["runbook"] else 0.0,
                refs_ok])
    q = E * d * _mean(fire) * _mean(silent) * (0.7 + 0.3 * la)
    return q, {"E": E, "dup": d, "fire": round(_mean(fire), 4), "silent": round(_mean(silent), 4),
               "label_annot": round(la, 4)}


def annotation_q(r: dict, groups: list[dict], ctx: Ctx) -> tuple[float, dict]:
    """Every label an annotation template reads is present on the firing alert (a Slack line that stops
    mid-sentence reads a label the aggregation dropped)."""
    name = r["annotation"]["alert"]
    defs = ctx.alerts.get(name, [])
    fired = []
    for g in groups:
        for p in g["probes"]:
            if p.get("family") == "annot":
                got = ctx.snap(g["name"], p["id"]) or []
                fired += [a for a in got if a.get("alertname") == name]
    if not defs or not fired:
        return 0.0, {"fired": bool(fired), "exists": bool(defs)}
    refs = set()
    for d in defs:
        refs |= template_labels({"annotations": d.rule.get("annotations") or {}})
    ok = all(all(l in a for l in refs) for a in fired)
    svc = r["annotation"].get("service")

    def names_it(d) -> bool:
        """The summary says what is affected: it is non-empty and names the service, written out or through a
        label whose value on the fired alert is the service."""
        text = str((d.rule.get("annotations") or {}).get("summary", ""))
        if not text.strip():
            return False
        if svc is None or svc in text:
            return True
        return any(a.get(lbl) == svc for lbl in template_labels({"annotations": {"s": text}}) for a in fired)

    summ = all(names_it(d) for d in defs)
    return (1.0 if ok and summ else 0.0), {"refs": sorted(refs), "fired": len(fired), "names_service": summ}
