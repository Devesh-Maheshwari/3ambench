"""Expert-tier builder: `alertforge expert --out DIR [--families E1,E2] [--seeds 1-8] [--jobs 4]`.

Builds candidate tasks into DIR/tasks, runs the acceptance gate on each, writes partial/ and null/ policies,
moves candidates that fail the gate to DIR/rejected/ and logs every result to DIR/gate.jsonl."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed


RESAMPLES = 3   # hidden-scenario redraws before a seed whose scenarios can't separate a wrong variant is rejected


def _reject(out: str, tid: str) -> None:
    """A failed rebuild cannot leave an old task in the accepted/publishable directories."""
    src, dst = os.path.join(out, "tasks", tid), os.path.join(out, "rejected", tid)
    if os.path.exists(src):
        if os.path.exists(dst):
            shutil.rmtree(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
    for sub in ("partial", "null"):
        shutil.rmtree(os.path.join(out, sub, tid), ignore_errors=True)


def _one(args) -> dict:
    fam, seed, out, master, adversaries = args[:5]
    from . import build, gate, knobs
    if len(args) > 5 and args[5] is not None:
        knobs.set_active(args[5])
    tid = f"afx-{fam.lower()}-s{seed:02d}"
    t0 = time.time()
    sweep: list[str] = []
    try:
        for k in range(RESAMPLES):
            m = build.build_task(fam, seed, os.path.join(out, "tasks"), tid, master, resample=k)
            sweep = gate.variant_sweep(m)
            if not sweep:
                break
    except Exception as e:  # a seed the composer cannot host is logged, not fatal
        _reject(out, tid)
        return {"task_id": tid, "family": fam, "seed": seed, "pass": False, "reasons": [f"build failed: {e!r}"]}
    t1 = time.time()
    try:
        res = gate.gate_task(m, out, adversaries=adversaries, sweep=sweep)
    except Exception as e:
        _reject(out, tid)
        return {"task_id": tid, "family": fam, "seed": seed, "pass": False,
                "reasons": [f"gate failed: {e!r}"]}
    res.update(seed=seed, build_seconds=round(t1 - t0, 1), gate_seconds=round(time.time() - t1, 1), resample=m["resample"],
               knobs=sorted(knobs.ACTIVE), ticket_share=_ticket_share(m["spec"]),
               n_checks=m["n_checks"], items=[f"{it.rid}:{it.code}" for it in m["items"]],
               kept=[k.alert for k in m["kept"]], company=m["world"].company.name,
               n_alert_rules=sum(1 for f in m["world"].files.values() for g in f.groups for r in g["rules"] if "alert" in r),
               n_record_rules=sum(1 for f in m["world"].files.values() for g in f.groups for r in g["rules"] if "record" in r),
               n_rule_files=len(m["world"].files))
    if not res["pass"]:
        _reject(out, tid)
    return res


def _ticket_share(spec: dict) -> float:
    w = [(r["weight"], bool(r.get("ticket"))) for r in spec["requirements"]]
    return round(sum(x for x, t in w if t) / (sum(x for x, _ in w) or 1), 3)


def _seeds(spec: str) -> list[int]:
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",")]


def main(a) -> int:
    fams = a.families.split(",")
    kn = None if getattr(a, "knobs", None) is None else [k for k in a.knobs.split(",") if k]
    if kn is not None:
        from . import knobs
        knobs.set_active(kn)   # fail fast on a misspelt knob
    jobs = [(f, s, a.out, a.master_seed, not a.no_adversaries, kn) for s in _seeds(a.seeds) for f in fams]
    os.makedirs(a.out, exist_ok=True)
    log = open(os.path.join(a.out, "gate.jsonl"), "a")
    rows, fails = [], 0
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(_one, j) for j in jobs]
        for fu in as_completed(futs):
            res = fu.result()
            rows.append(res)
            fails += 0 if res["pass"] else 1
            log.write(json.dumps({**res, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}, default=str) + "\n")
            log.flush()
            print(f"{res['task_id']:16s} {'PASS' if res['pass'] else 'FAIL'} oracle={res.get('oracle')} alt={res.get('alt')} "
                  f"null={res.get('null')} partial={res.get('partial')} grader={res.get('grader_seconds')} "
                  f"checks={res.get('n_checks')}" + ("" if res["pass"] else f"\n    {str(res['reasons'])[:600]}"), flush=True)
    mpath = os.path.join(a.out, "manifest.jsonl")
    keep = {}
    if os.path.exists(mpath):
        for line in open(mpath):
            r = json.loads(line)
            keep[r["task_id"]] = r
    for r in rows:
        keep.pop(r["task_id"], None)
        if r["pass"]:
            keep[r["task_id"]] = {k: r.get(k) for k in ("task_id", "family", "seed", "company", "items", "kept", "n_checks",
                                                        "n_alert_rules", "n_record_rules", "n_rule_files", "grader_seconds",
                                                        "partial", "alert_rules_pristine", "ticket_share", "detect",
                                                        "overlap", "overlap_seed", "decoys", "resample", "knobs")} | {"tier": "expert"}
    with open(mpath, "w") as fh:
        for tid in sorted(keep):
            fh.write(json.dumps(keep[tid]) + "\n")
    print(f"{len(rows) - fails}/{len(rows)} candidates pass the gate")
    # cross-task sameness (R2, SPEC 7.6 item 3): every sentence in at most a third of the tasks carrying its text
    from . import xlint
    built = [(f, s) for s in _seeds(a.seeds) for f in fams]
    bad, stats = xlint.check([xlint.render(f, s, a.master_seed) for f, s in built])
    with open(os.path.join(a.out, "xlint.json"), "w") as fh:
        json.dump({"tasks": [f"afx-{f.lower()}-s{s:02d}" for f, s in built], "over_cap": bad, **stats}, fh, indent=1, default=str)
    print(f"cross-task lint: {len(bad)} sentences over the cap, max 6-gram Jaccard {stats['max_jaccard_6gram']}")
    for b in bad[:20]:
        print("   ", b)
    return 0 if not bad and not fails else 1


if __name__ == "__main__":
    sys.exit(0)
