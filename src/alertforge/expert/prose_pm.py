"""H04's postmortem and its packet (R1): traffic to a service stops while its pods stay up.

Four narratives (an ingress host rule, an edge WAF rule, traffic-split weights, a DNS cut-over). Every one keeps
the facts the checks read: the floors are in the catalog (action item 1, done), and action item 2 asks for a page
when the service is under its floor for 5 minutes with its pods up, inside 15 minutes of traffic stopping. The
evidence agrees with itself: the upstream flaps in bursts, the error-ratio page's firing and resolve times come
from replaying the repo's own rule over the pull (`replay_ratio`), the ingress shows the same error share as the
app while traffic flows, and what the edge did during the outage is in the packet.
"""

from __future__ import annotations

import math

from .. import promsim as ps
from . import evidence as ev
from . import then, variety
from .items import Ticket
from .people import crew
from .vocab import public_zone
from .prose_svc import _day, _pd_rows, paged_team, unique_key
from .xscen import counter_from_rates, http_series, pods, rs_hash, series, up_values

NARRATIVES = ("ingress", "edge", "weights", "dns")


def replay_ratio(raws: list, thr: float, window: int = 5, for_: int = 10) -> tuple[int | None, int | None]:
    """(minute the error-ratio page fires, minute it resolves) for `sum(rate(5xx[5m])) / sum(rate(all[5m])) > thr`
    with `for: 10m`, evaluated every minute the way Prometheus extrapolates `rate()`."""
    n = max(len(r.values) for r in raws) - 1
    pending = fired = None
    for t in range(n + 1):
        bad = tot = 0
        for r in raws:
            x = ps.extrapolated(r.values, t, window)
            if x is None:
                continue
            tot += x
            if r.labels.get("code", "").startswith("5"):
                bad += x
        cond = tot > 0 and bad / tot > thr
        if cond and pending is None:
            pending = t
        if cond and fired is None and pending is not None and t - pending >= for_:
            fired = t
        if not cond:
            if fired is not None:
                return fired, t
            pending = None
    return fired, None


def _flapping(rng, T: float, n: int, err_on: int, drop: int) -> list[float]:
    """Bursts of 2-4 minutes at 2.4-5x the threshold, lulls of 1-2 minutes at 1.3-1.8x: over a 5m window the ratio
    stays above the threshold from the second minute on, and the last minute before the change is a burst."""
    err = [rng.uniform(0.002, 0.006) for _ in range(n + 1)]
    m, burst = err_on, True
    while m < drop:
        k = rng.randint(2, 4) if burst else rng.randint(1, 2)
        for x in range(m, min(drop, m + k)):
            err[x] = T * (rng.uniform(2.4, 5.0) if burst else rng.uniform(1.3, 1.8))
        m += k
        burst = not burst
    err[err_on] = err[err_on + 1] = T * rng.uniform(2.8, 3.4)
    err[drop - 1] = max(err[drop - 1], T * rng.uniform(2.6, 3.6))
    return err


def _ingress(rng, w, s, n, rps, err, host, drop, back, kind) -> tuple[list, int]:
    """nginx_ingress_controller_requests for the service's host, per controller pod: the app's own status mix while
    traffic flows (`rps` is what clients sent); during the outage whatever the narrative says the ingress did.
    Returns (series, requests that failed during the outage)."""
    ctrl = pods(rng, "ingress-nginx-controller", 2, rs_hash(rng))
    out, lost = [], 0
    for i, cp in enumerate(ctrl):
        share = [0.5 + (0.04 if i == 0 else -0.04)] * (n + 1)
        base = {"controller_class": "k8s.io/ingress-nginx", "controller_namespace": "ingress-nginx", "controller_pod": cp,
                "host": host, "method": "GET", "path": "/", "job": "ingress-nginx", "instance": f"10.{rng.randint(16, 31)}.0.{rng.randint(2, 99)}:10254"}
        app = {**base, "ingress": s.name, "namespace": w.namespace, "service": s.name}
        flow = [0.0 if drop <= m < back else r * sh for m, (r, sh) in enumerate(zip(rps, share))]
        for status, frac in (("200", lambda e: (1 - e) * 0.97), ("404", lambda e: (1 - e) * 0.03), ("500", lambda e: e)):
            rates = [x * frac(e) for x, e in zip(flow, err)]
            out.append(series("nginx_ingress_controller_requests", {**app, "status": status},
                              counter_from_rates(rng, rates, rng.randint(10**5, 10**6) if status == "200" else rng.randint(200, 4000))))
        gone = [r * sh if drop <= m < back else 0.0 for m, (r, sh) in enumerate(zip(rps, share))]
        lost += int(sum(gone) * 60)
        if kind == "ingress":   # the host matched no rule: the controller's default backend answered 404
            lab = {k: v for k, v in base.items() if k != "path"}
            vals = counter_from_rates(rng, gone, 0)
            first = next((m for m, v in enumerate(vals) if v), None)
            out.append(series("nginx_ingress_controller_requests", {**lab, "status": "404"},
                              [None] * first + vals[first:] if first else vals))
        elif kind == "weights":   # both backends at weight 0: the ingress answered 503 itself
            vals = counter_from_rates(rng, gone, 0)
            first = next((m for m, v in enumerate(vals) if v), 0)
            out.append(series("nginx_ingress_controller_requests", {**app, "status": "503"}, [None] * first + vals[first:]))
    return out, lost


def _cdn_csv(clock, rps, err, drop, back, host) -> str:
    """The CDN's per-minute status export for the host (what the edge answered, before anything reached us)."""
    rows = ["minute_utc,host,requests,status_2xx,status_403,status_4xx_other,status_5xx"]
    for m in range(len(rps)):
        r = rps[m] * 60
        if drop <= m < back:
            rows.append(f"{clock.utc(m):%Y-%m-%dT%H:%M}Z,{host},{round(r)},0,{round(r)},0,0")
        else:
            e = round(r * err[m])
            rows.append(f"{clock.utc(m):%Y-%m-%dT%H:%M}Z,{host},{round(r)},{round(r * 0.97) - e},0,{round(r * 0.03)},{e}")
    return "\n".join(rows) + "\n"


def public_domain(w) -> str:
    """The zone customers use: the internal zone without its `corp.`/`int.` prefix."""
    return public_zone(w.domain)


def h04(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    team = paged_team(w, s.name)
    key = unique_key(w, w.company.key, rng.randint(2300, 2600))
    pm = f"PM-{rng.randint(300, 480)}"
    date = _day(w, rng, 6, 20, weekday=True)
    clock = ev.Clock(date, rng.choice(["13:40", "10:15", "19:05", "11:20", "15:35", "09:50"]), w.company.utc_offset,
                     w.company.tz_label)
    n, T = 110, float(s.err_thr)
    err_on, drop, back = rng.randint(6, 12), rng.randint(27, 31), rng.randint(80, 95)
    ev_rps = s.rps * rng.uniform(1.6, 2.2)
    err = _flapping(rng, T, n, err_on, drop)
    # what clients sent; when traffic comes back they retry what they queued for a quarter of an hour
    offered = [ev_rps * rng.uniform(0.94, 1.06) * (1 + (1.3 * math.exp(-(m - back) / 5) if m >= back else 0))
               for m in range(n + 1)]
    rps = [0.0 if drop <= m < back else x for m, x in enumerate(offered)]   # what reached the service
    raws = http_series(rng, w, s.name, n, rps, err, s.pods, post=rng.uniform(0.1, 0.4))
    fire, res = replay_ratio(raws, T)
    if fire is None or res is None or not (fire < drop <= res):
        raise RuntimeError(f"H04 packet for {s.name}: the error-ratio page fires at {fire}, resolves at {res}")
    ups = [series("up", {"service": s.name, "job": s.name, "namespace": w.namespace, "instance": i, "pod": p}, up_values(n))
           for i, p in sorted({(r.labels["instance"], r.labels["pod"]) for r in raws})]
    kind = variety.card(w, "H04:pm", list(NARRATIVES))
    host = f"{s.name}.{public_domain(w)}"
    edge, lost = _ingress(rng, w, s, n, offered, err, host, drop, back, kind)
    path = f"incidents/{pm}"
    files = {f"{path}/query-range/{s.name}-requests.json": ev.query_range(raws, clock),
             f"{path}/query-range/{s.name}-up.json": ev.query_range(ups, clock),
             f"{path}/query-range/ingress-{s.name}.json": ev.query_range(edge, clock)}
    if kind == "edge":
        files[f"{path}/cdn-{host}-status.csv"] = _cdn_csv(clock, offered, err, drop, back, host)
    alert = f"{s.camel}HighErrorRatio"
    lab = then.labels(w, alert, s.name) or {"alertname": alert, "service": s.name, "severity": "page", "team": team}
    files[f"{path}/slack-alerts.txt"] = "\n".join([
        f"{clock.hm(fire)} {then.title(w, lab, receiver_prefix='slack-')}",
        f"{clock.hm(res)} {then.title(w, lab, 'resolved', receiver_prefix='slack-')}"]) + "\n"
    p = crew(w, rng, team, 3)
    found = drop + rng.randint(35, 45)
    k1 = unique_key(w, w.company.key, rng.randint(2100, 2290))
    k3 = unique_key(w, "PLAT", rng.randint(400, 900))
    from .prose_pm_text import postmortem
    files[f"{path}/postmortem.md"] = postmortem(kind, w, rng, s=s, pm=pm, date=date, clock=clock, team=team, p=p,
                                                alert=alert, fire=fire, res=res, drop=drop, back=back, found=found,
                                                err_on=err_on, lost=lost, keys=(k1, key, k3), host=host)
    page_at = clock.iso(fire)
    files[f"{path}/pagerduty-incidents.csv"] = ev.pagerduty_csv(_pd_rows(rng, w, clock, [
        {"created_on": page_at, "service_name": f"{then.pager(w, lab) or team} (prod)", "title": then.title(w, lab),
         "urgency": "high", "status": "resolved", "acknowledged_by": p[0].full, "resolved_on": clock.iso(res)}]))
    title, body = variety.card(w, "H04", [
        (f"{pm} action item 2: page on {s.name} traffic loss",
         f"Item 2 of the action items in `{path}/postmortem.md`. The floors went into the catalog last week; nothing "
         f"reads them yet."),
        (f"{s.name}: no page when traffic stopped ({pm}, item 2)",
         f"See the postmortem and the pulls in `{path}/`. {p[2].first} asked that the page lands with "
         f"{w.svc(s.name).team}, who own {s.name} (ownership.yaml)."),
        (f"follow-up from {pm}: {s.name} traffic floor page",
         f"The postmortem's second action item is ours. Catalog floors are merged (item 1), so the page can use them. "
         f"Packet and writeup: `{path}/`."),
        (f"{pm} action 2 ({s.name})",
         f"Picking up action item 2 from `{path}/postmortem.md`. {p[1].first} would like it done before the next "
         f"ingress or DNS change window.")])
    return Ticket(key, title, body, p[2], files)
