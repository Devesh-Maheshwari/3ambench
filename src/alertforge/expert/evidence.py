"""Incident packet formats, written the way people attach them: /api/v1/query_range JSON, a PagerDuty
incident export, the alert channel as Slack showed it (Alertmanager's default Slack title template), the
Prometheus 3.5 rule-manager log line for a failed evaluation, and a Grafana Explore CSV export.

Evidence always comes from an evidence scenario with its own seed: same mechanism and cadence as the hidden
scenarios, different magnitudes, pod names, onset and duration.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re

from ..promsim import STALE

UTC = dt.timezone.utc


class Clock:
    """Minute m of an evidence scenario → wall-clock time. `start` is local time of minute 0."""

    def __init__(self, date: str, hhmm: str, utc_offset: int, tz_label: str):
        y, mo, d = (int(x) for x in date.split("-"))
        h, mi = (int(x) for x in hhmm.split(":"))
        self.local0 = dt.datetime(y, mo, d, h, mi)
        self.off = utc_offset
        self.label = tz_label

    def local(self, m: float) -> dt.datetime:
        return self.local0 + dt.timedelta(minutes=m)

    def utc(self, m: float) -> dt.datetime:
        return (self.local(m) - dt.timedelta(hours=self.off)).replace(tzinfo=UTC)

    def hm(self, m: float) -> str:
        return self.local(m).strftime("%H:%M")

    def epoch(self, m: float) -> int:
        return int(self.utc(m).timestamp())

    def iso(self, m: float) -> str:
        return self.utc(m).strftime("%Y-%m-%dT%H:%M:%SZ")

    def day(self, m: float = 0) -> str:
        return self.local(m).strftime("%a %d %b")


def query_range(raws: list, clock: Clock, metric_filter=None, step_s: int = 60) -> str:
    result = []
    for r in raws:
        if metric_filter and not metric_filter(r):
            continue
        vals = []
        for m, v in enumerate(r.values):
            if v is None or v == STALE:
                continue
            x = f"{v}" if isinstance(v, int) else f"{float(v):.6g}"
            vals.append([clock.epoch(m), x])
        if vals:
            result.append({"metric": {"__name__": r.metric, **dict(sorted(r.labels.items()))}, "values": vals})
    doc = {"status": "success", "data": {"resultType": "matrix", "result": result}}
    return json.dumps(doc, indent=1) + "\n"


def slack_title(status: str, alerts: list[dict], group_by: list[str]) -> str:
    """Alertmanager's default Slack title: [STATUS:n] group values (other common label values)."""
    common = dict(alerts[0])
    for a in alerts[1:]:
        common = {k: v for k, v in common.items() if a.get(k) == v}
    group = {k: common[k] for k in sorted(group_by) if k in common}
    rest = {k: v for k, v in sorted(common.items()) if k not in group}
    head = f"[{status.upper()}" + (f":{len(alerts)}]" if status == "firing" else "]")
    title = f"{head} {' '.join(group.values())}"
    if len(common) > len(group):
        title += f" ({' '.join(rest.values())})"
    return title


def pagerduty_csv(rows: list[dict]) -> str:
    cols = ["incident_number", "created_on", "service_name", "title", "urgency", "status", "acknowledged_by",
            "resolved_on"]
    if rows and any(not r.get("incident_number") for r in rows):
        # a PagerDuty export is in creation order and every incident has a number
        import zlib
        rows = sorted(rows, key=lambda r: r["created_on"])
        base = 18000 + zlib.crc32(rows[0]["created_on"].encode()) % 8000
        rows = [{**r, "incident_number": r.get("incident_number") or base + i} for i, r in enumerate(rows)]
    buf = io.StringIO()
    wr = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    wr.writeheader()
    for r in rows:
        wr.writerow({k: r.get(k, "") for k in cols})
    return buf.getvalue()


def prom_eval_error_log(clock: Clock, minutes: list[float], file: str, group: str, alert: str, rule_yaml,
                        err: str, index: int = 0) -> str:
    """The Prometheus 3.5 rule-manager line for a failed evaluation (sp14). `rule_yaml` is the rule as the server
    prints it, or a function of the minute (a rule changed by a reload mid-incident)."""
    out = []
    for m in minutes:
        text = rule_yaml(m) if callable(rule_yaml) else rule_yaml
        rule = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        ts = clock.utc(m).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int((m * 60) % 1 * 1000):03d}Z"
        out.append(f'time={ts} level=WARN source=group.go:544 msg="Evaluating rule failed" component="rule manager" '
                   f'file={file} group={group} name={alert} index={index} rule="{rule}" err="{err}"')
    return "\n".join(out) + "\n"


_SEL = re.compile(r"\{([^{}]*)\}")
_MATCHER = re.compile(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*(=~|!~|!=|=)\s*("(?:[^"\\\\]|\\\\.)*")\s*')


def prom_string(expr: str) -> str:
    """An expression the way Prometheus prints it back (`expr.String()`): one line, the label matchers of every
    selector sorted (the rules API `query`, and the `expr` in the log's rule dump). Our rule text already uses the
    printer's spacing, so the matcher order is what differs."""
    def sort(m):
        parts = sorted(f"{x.group(1)}{x.group(2)}{x.group(3)}" for x in _MATCHER.finditer(m.group(1)))
        return "{" + ",".join(parts) + "}"
    return _SEL.sub(sort, " ".join(str(expr).split()))


def rule_yaml(rule: dict) -> str:
    """The rule as the rule manager prints it (go-yaml: wrapped at 80 columns, map keys sorted, `for` kept)."""
    import yaml
    doc = {"alert": rule["alert"], "expr": prom_string(rule["expr"])}
    if rule.get("for"):
        doc["for"] = str(rule["for"])
    for k in ("labels", "annotations"):
        if rule.get(k):
            doc[k] = {str(x): str(v) for x, v in sorted(rule[k].items())}
    return yaml.safe_dump(doc, sort_keys=False, width=80, allow_unicode=True)


def rules_api(clock: Clock, m: float, file: str, group: str, rule: dict, err: str, eval_s: float) -> str:
    """A `/api/v1/rules` response for the group holding one failing alert, in the field order a real 3.5 server
    returns (group-level evaluation fields included)."""
    def stamp(x: float, ns: int) -> str:
        return clock.utc(x).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ns:09d}Z"
    ns = int((m * 7919) % 1 * 1e9) or 418272911
    dur = rule.get("for", "0m")
    secs = int(dur[:-1]) * {"s": 1, "m": 60, "h": 3600}[dur[-1]] if dur else 0
    r = {"state": "inactive", "name": rule["alert"], "query": prom_string(rule["expr"]), "duration": secs,
         "keepFiringFor": 0, "labels": dict(sorted(rule.get("labels", {}).items())),
         "annotations": dict(sorted(rule.get("annotations", {}).items())), "alerts": [], "health": "err",
         "lastError": err, "evaluationTime": eval_s, "lastEvaluation": stamp(m, ns), "type": "alerting"}
    doc = {"status": "success", "data": {"groups": [{"name": group, "file": file, "rules": [r], "interval": 60,
                                                      "limit": 0, "evaluationTime": round(eval_s * 1.06, 9),
                                                      "lastEvaluation": stamp(m, max(0, ns - 31417))}]}}
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def humanize(v: float) -> str:
    """Prometheus' `humanize` template function for the values a lag or a ratio takes (%.4g with SI prefixes)."""
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1:
        for div, pre in ((1e24, "Y"), (1e21, "Z"), (1e18, "E"), (1e15, "P"), (1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k")):
            if a >= div:
                return f"{v / div:.4g}{pre}"
        return f"{v:.4g}"
    for mul, pre in ((1e3, "m"), (1e6, "u"), (1e9, "n"), (1e12, "p"), (1e15, "f"), (1e18, "a"), (1e21, "z"), (1e24, "y")):
        if a * mul >= 1:
            return f"{v * mul:.4g}{pre}"
    return f"{v:.4g}"


def canonical_le(le: str) -> str:
    """Prometheus 3 normalises classic-histogram `le` on ingestion: `1` is stored as `1.0` (S6)."""
    if le in ("+Inf", "-Inf", "NaN"):
        return le
    x = float(le)
    s = repr(x)
    return s if ("e" not in s) else format(x, ".17g")


def le_is_canonical(le: str) -> bool:
    return le == canonical_le(le)


def explore_csv(clock: Clock, series: dict[str, list]) -> str:
    """Grafana Explore → Inspect → Data (series joined by time)."""
    names = list(series)
    n = max(len(v) for v in series.values())
    buf = io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    wr.writerow(["Time", *names])
    for m in range(n):
        row = [series[k][m] if m < len(series[k]) else "" for k in names]
        if all(x in ("", None) for x in row):
            continue
        wr.writerow([clock.local(m).strftime("%Y-%m-%d %H:%M:%S"), *["" if x is None else x for x in row]])
    return buf.getvalue()


def selector(metric: str, labels: dict) -> str:
    return metric + "{" + ", ".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"
