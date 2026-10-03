"""The scraped world under every hidden scenario (expert/scraped.py): every service whose targets are up has the
series those targets export, so a rule written to survive missing series reads real traffic, not an artefact.

Regression: on afx-e1-s03 the H01 ticket's scenarios held `up` for booking-api and no request counter at all. A
floor rule that reads missing traffic as none (`... or sum(up) * 0`, written by two pilot runs) paged in every one
of them and the task scored 0.339 instead of 1.0.
"""

import os
import random
import re

import pytest

from conftest import HAVE_TOOLS

MASTER = 20260930
# The floor rule two pilot runs wrote for SRE-2412 / PM-480 action item 2, as they wrote it.
PILOT_FLOOR = ('(\n  sum by (service) (rate(http_requests_total{service="booking-api"}[5m]))\n'
               '  or sum by (service) (up{service="booking-api"}) * 0\n) < 3\n'
               'and on (service) sum by (service) (up{service="booking-api"}) > 0\n')
TOLERANT = ["or_up_times_zero", "absent_counter", "unless_at_floor", "or_labelled_vector_zero"]


def _world():
    from alertforge.expert import build, families, scraped
    w, items = families.build("E1", 3, MASTER, "afx-e1-s03")
    pristine, _ = build.materialize(w, items, set())
    oracle, _ = build.materialize(w, items, {it.rid for it in items})
    return w, items, scraped.Plan(w, items, (pristine, oracle), "unit:0")


def _group(plan, w, svc, up_values, extra=None, bare=None):
    from alertforge.expert import scraped
    from alertforge.expert.xscen import G, background, series
    n = len(up_values[0]) - 1
    g = G("T9", "unit", n)
    g.series += background(w, random.Random(5), n, {svc})
    for i, vals in enumerate(up_values):
        g.series.append(series("up", {"service": svc, "job": svc, "namespace": w.namespace,
                                      "instance": f"10.9.0.{i + 2}:8080"}, vals))
    g.series += extra or []
    if bare:
        g.bare(svc)
    with scraped.active(plan):
        return g.done(), g


def _series(d, metric, **lab):
    from alertforge.expert.xscen import dec
    out = []
    for s in d["input_series"]:
        name, inner = s["series"].split("{", 1)
        if name == metric and all(f'{k}="{v}"' in inner for k, v in lab.items()):
            out.append((s["series"], dec(s["values"])))
    return out


def test_up_targets_serve_traffic_and_go_stale_with_their_target():
    w, items, plan = _world()
    svc = "booking-api"
    n = 30
    vanish = [1] * 20 + ["stale"] + [None] * 10
    down = [1] * 10 + [0] * 21
    d, g = _group(plan, w, svc, [vanish, down, [1] * 31])
    req = _series(d, "http_requests_total", service=svc)
    assert len(req) == 9   # three targets x 200/404/500
    ups = {s.split('pod="')[1].split('"')[0]: v for s, v in _series(d, "up", service=svc)}
    for name, vals in req:
        pod = name.split('pod="')[1].split('"')[0]
        up = ups[pod]
        for m in range(n + 1):
            assert (isinstance(vals[m], int)) == (up[m] == 1), (name, m, vals[m], up[m])
            if up[m] in ("stale", 0) and m and up[m - 1] == 1:
                assert vals[m] == "stale", (name, m)   # the scrape that finds the target gone writes the marker
    # the service's traffic moves to the targets still up: per-minute requests stay at its rate
    s = w.svc(svc)
    for m in (5, 15, 25):
        inc = sum(v[m] - v[m - 1] for _, v in req if isinstance(v[m], int) and isinstance(v[m - 1], int))
        assert 0.9 * s.rps * 60 <= inc <= 1.1 * s.rps * 60, (m, inc)
    # every other service with targets up has its request counters too
    for x in w.services:
        if x.kind in ("api", "worker", "latency"):
            assert _series(d, "http_requests_total", service=x.name), x.name
        if x.kind == "grpc":
            assert _series(d, "grpc_server_handled_total", service=x.name), x.name


def test_a_discovery_flap_keeps_the_counter_running_and_bare_is_respected():
    w, items, plan = _world()
    svc = "booking-api"
    flap = [1] * 20 + ["stale", None] + [1] * 9
    d, _ = _group(plan, w, svc, [flap] * 3)
    for name, vals in _series(d, "http_requests_total", service=svc, code="200"):
        assert vals[20] == "stale" and vals[21] is None
        assert vals[22] - vals[19] > 2.5 * (vals[19] - vals[18])   # served through the flap
    d, _ = _group(plan, w, svc, [[1] * 31] * 3, bare=True)
    assert not _series(d, "http_requests_total", service=svc) and d["bare"] == {svc: ["grpc", "http"]}


def test_a_cards_own_histogram_gets_request_counters_that_add_up_to_its_count():
    from alertforge.expert.items_latency import slow_hist
    w, items, plan = _world()
    svc = "booking-api"
    lab = {"service": svc, "namespace": w.namespace, "pod": "booking-api-abc12-xyz34", "instance": "10.9.9.9:8080",
           "job": svc}
    hist = slow_hist(random.Random(3), lab, 30, 40.0, [0.001] * 31)
    d, _ = _group(plan, w, svc, [[1] * 31], extra=hist)
    req = _series(d, "http_requests_total", service=svc)
    cnt = _series(d, "http_request_duration_seconds_count", service=svc)
    assert len(cnt) == 1 and {n.split('pod="')[1].split('"')[0] for n, _ in req} == {lab["pod"]}
    for m in range(1, 31):
        assert sum(v[m] - v[m - 1] for _, v in req) == cnt[0][1][m] - cnt[0][1][m - 1]


def test_the_scraped_world_is_its_own_random_stream():
    w, items, plan = _world()
    a, _ = _group(plan, w, "booking-api", [[1] * 31] * 3)
    b, _ = _group(plan, w, "booking-api", [[1] * 31] * 3)
    assert a["input_series"] == b["input_series"]
    from alertforge.expert.xscen import G, background
    rng1, rng2 = random.Random(9), random.Random(9)
    g1 = G("T9", "x", 30)
    g1.series += background(w, rng1, 30)
    g1.done()
    from alertforge.expert import scraped
    g2 = G("T9", "x", 30)
    g2.series += background(w, rng2, 30)
    with scraped.active(plan):
        g2.done()
    assert rng1.random() == rng2.random()   # the scenario stream drew nothing more


def test_a_pod_started_after_the_first_one_died_gets_a_live_target():
    """unify_targets: a pod beyond the drawn targets copies the first target as drawn. It used to copy it after that
    target had followed the first pod out, so a rollout's later pods (H07, H22) had an `up` that was never 1 and the
    service read as absent while it served."""
    from alertforge.expert.xscen import Raw, unify_targets
    bg = [Raw("up", {"service": "a", "job": "a", "namespace": "ns", "instance": "10.0.0.2:8080"}, [1] * 6, bg=True)]
    first = Raw("http_requests_total", {"service": "a", "pod": "a-1", "instance": "10.9.9.9:8080"}, [1, 2, "stale", None, None, None])
    later = Raw("http_requests_total", {"service": "a", "pod": "a-2", "instance": "10.9.9.8:8080"}, [None, None, 0, 1, 2, 3])
    out = unify_targets(bg + [first, later])
    ups = {r.labels["pod"]: r.values for r in out if r.metric == "up"}
    assert ups == {"a-1": [1, 1, "stale", None, None, None], "a-2": [None, None, 1, 1, 1, 1]}


def test_lint_reports_a_service_left_without_its_series():
    from alertforge.expert.scrapelint import check_groups
    w, items, plan = _world()
    d, _ = _group(plan, w, "booking-api", [[1] * 31] * 3)
    assert check_groups(plan, [d]) == []
    cut = {**d, "input_series": [s for s in d["input_series"]
                                 if not (s["series"].startswith("http_requests_total") and 'service="booking-api"' in s["series"])]}
    bad = check_groups(plan, [cut])
    assert any("booking-api has targets up and no http series" in b for b in bad), bad
    assert check_groups(plan, [{**cut, "bare": {"booking-api": ["http"]}}]) == []
    no_lb = {**d, "input_series": [s for s in d["input_series"] if not s["series"].startswith("nginx_ingress_controller")]}
    assert any("booking-api has targets up and no ingress series" in b for b in check_groups(plan, [no_lb]))


def _incs(vals, m):
    a, b = vals[m - 1], vals[m]
    return (b - a if b >= a else b) if isinstance(a, int) and isinstance(b, int) else 0


def test_the_ingress_counts_what_the_app_served_and_answers_while_the_service_is_out():
    """fairness G1: README "This Prometheus scrapes our apps and the ingress". Every HTTP service with targets has the
    ingress controller's request counters: while it serves, status by status what its own counters counted; while
    its targets are down, 502s, and once none is left, 503s, for the traffic it had. An error ratio read at the load
    balancer used to see no series at all and never fired."""
    w, items, plan = _world()
    svc = "booking-api"
    out = [1] * 10 + [0] * 10 + ["stale"] + [None] * 10
    d, _ = _group(plan, w, svc, [out] * 3)
    app = _series(d, "http_requests_total", service=svc)
    lb = {s.split('status="')[1].split('"')[0]: v for s, v in _series(d, "nginx_ingress_controller_requests", service=svc)}
    assert {"200", "404", "500", "502", "503"} <= set(lb), sorted(lb)
    assert all(f'host="{svc}.' in s and 'job="ingress-nginx"' in s and not re.search(r'[{,]pod="', s)
               for s, _ in _series(d, "nginx_ingress_controller_requests", service=svc))   # not an app target
    for m in range(2, 10):
        for code in ("200", "404", "500"):
            mine = sum(_incs(v, m) for s, v in app if f'code="{code}"' in s)
            assert _incs(lb[code], m) == mine, (code, m)
    rate = sum(_incs(v, 9) for _, v in app)
    for m in range(11, 20):
        assert 0.85 * rate <= _incs(lb["502"], m) <= 1.15 * rate and _incs(lb["200"], m) == 0, m
        assert lb["503"][m] is None
    for m in range(22, 31):
        assert 0.85 * rate <= _incs(lb["503"], m) <= 1.15 * rate, m
    for x in w.services:   # every other HTTP service is behind the ingress too; gRPC services are not
        has = bool(_series(d, "nginx_ingress_controller_requests", service=x.name))
        assert has == (x.kind in ("api", "worker", "latency")), x.name


# ---------------------------------------------------------------------------- the pilot regression

@pytest.fixture(scope="module")
def e1s03(tmp_path_factory):
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    from alertforge.expert import build
    return build.build_task("E1", 3, str(tmp_path_factory.mktemp("e1s03")), "afx-e1-s03", MASTER)


def _pilot_state(m):
    from alertforge.expert import solutions
    from alertforge.expert.model import rules_named
    files, am, _ = solutions.oracle(m)
    for _, _, r in rules_named(files, "BookingApiTrafficBelowFloor"):
        r["expr"] = PILOT_FLOOR
    return files, am


def _grade(m, files, am):
    from alertforge.expert.build import grade_map, state_map
    return grade_map(m["spec"], os.path.dirname(m["hidden_dir"]), {**m["static"], **state_map(files, am)})


@pytest.mark.slow
def test_floor_rules_that_survive_missing_series_score_full_on_e1_s03(e1s03):
    """The pilot's floor rule and the bank's missing-series-tolerant floors, everything else the oracle: reward 1.0."""
    from alertforge.expert import solutions
    from alertforge.expert.scrapelint import lint
    m = e1s03
    assert lint(m) == []
    keys, det = _grade(m, *_pilot_state(m))
    assert keys["reward"] == 1.0, {r: (v["q"], v["families"].get("noisy")) for r, v in det["requirements"].items()}
    it = next(i for i in m["items"] if i.code == "H04")
    names = {n: fn for n, fn, e in it.variants(m["world"]) if e == "accept"}
    assert set(TOLERANT) <= set(names)
    for name in TOLERANT:
        files, am, _ = solutions.variant_state(m, it, names[name])
        keys, det = _grade(m, files, am)
        assert keys["reward"] == 1.0, (name, {r: v["q"] for r, v in det["requirements"].items() if v["s"] < 1})


@pytest.mark.slow
def test_without_the_scraped_world_e1_s03_charges_the_pilot_floor(tmp_path, monkeypatch, e1s03):
    """Negative control: the same build with the background world switched off is the scenarios the pilot was
    graded on (only `tests/` differs), and the pilot's rule loses T1 to its own floor alert, as it did (0.339)."""
    from alertforge.expert import build, scraped
    monkeypatch.setattr(scraped.Plan, "complete", lambda self, g: None)
    old = build.build_task("E1", 3, str(tmp_path), "afx-e1-s03", MASTER)
    m = e1s03
    assert old["pristine_map"] == m["pristine_map"] and old["oracle_map"] == m["oracle_map"]
    assert [g["probes"] for g in old["groups"]] == [g["probes"] for g in m["groups"]]
    keys, det = _grade(old, *_pilot_state(old))
    t1 = det["requirements"]["T1"]
    assert keys["reward"] < 0.4 and t1["s"] < 1, keys
    assert any("BookingApiTrafficBelowFloor" in x for x in t1["families"]["noisy"]), t1
    # every series the pilot's scenarios had is still there with its values, `up` only gaining the pod it now pairs
    # with
    import re

    def nopod(x):
        return re.sub(r',pod="[^"]*"', "", x)
    for g_old, g_new in zip(old["groups"], m["groups"]):
        full = {s["series"]: s["values"] for s in g_new["input_series"]}
        ups = {nopod(s["series"]): s["values"] for s in g_new["input_series"] if s["series"].startswith("up{")}
        for s in g_old["input_series"]:
            got = ups.get(nopod(s["series"])) if s["series"].startswith("up{") else full.get(s["series"])
            assert got == s["values"], (g_old["name"], s["series"])


@pytest.fixture(scope="module")
def e2s01(tmp_path_factory):
    if not HAVE_TOOLS:
        pytest.skip("promtool/amtool not available")
    from alertforge.expert import build
    return build.build_task("E2", 1, str(tmp_path_factory.mktemp("e2s01")), "afx-e2-s01", MASTER)


@pytest.mark.slow
def test_an_error_ratio_read_at_the_ingress_scores_full_on_e2_s01(e2s01):
    """fairness G1 on the task that found it: OPS-2880 says "about 39% of requests failed at the LB". CartSvcHighErrorRatio
    read from the ingress controller, everything else the oracle, scored 0.30 (T2 never fired); it now scores as
    the service-metric fix does."""
    from alertforge.expert import solutions
    from alertforge.expert.model import rules_named
    from alertforge.expert.scrapelint import lint
    m = e2s01
    assert lint(m) == [] and m["spec"]["severity_alias"] == {"critical": "page"}
    files, am, _ = solutions.oracle(m)
    keys, _ = _grade(m, files, am)
    assert keys["reward"] == 1.0
    lb = 'nginx_ingress_controller_requests{service="cart-svc"'
    for _, _, r in rules_named(files, "CartSvcHighErrorRatio"):
        r["expr"] = (f'sum by (service) (rate({lb},status=~"5.."}}[5m])) / sum by (service) (rate({lb}}}[5m])) > '
                     + r["expr"].rsplit("> ", 1)[1])
    keys, det = _grade(m, files, am)
    assert keys["reward"] == 1.0, {r: (v["q"], v["families"]) for r, v in det["requirements"].items() if v["s"] < 1}
