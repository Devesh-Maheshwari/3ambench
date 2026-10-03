"""Service runbooks (R2, editorial pass E7). A repo's runbooks were written by whoever got there first, so a task's
pages share one house style and differ in how much care each got: a thorough one, a stale one that still names the
team that had the service before a reorg, a two-liner, a page copied from another service with a name left over.
The front matter (`alerts: [...]`) is what the runbook site and the README rely on and never changes."""

from __future__ import annotations

from . import variety
from .repo import owner_then


def _front(s) -> str:
    return f"---\nalerts: [{s.camel}Down, {s.camel}HighErrorRatio]\n---\n"


def _a(w, s, shape: str, other) -> str:
    ns, d = w.namespace, w.domain
    if shape == "full":
        return (f"# {s.name}\n\n## What this means\n\n`{s.camel}Down`: no healthy {s.name} target is left. "
                f"`{s.camel}HighErrorRatio`: too large a share of its requests return 5xx.\n\n## Dashboards\n\n"
                f"- https://grafana.{d}/d/svc-overview?var-service={s.name}\n\n## First checks\n\n"
                f"1. What changed: `kubectl -n {ns} rollout history deploy/{s.name}`\n"
                f"2. What it says: `kubectl -n {ns} logs -l app={s.name} --since=15m | grep -i error`\n\n"
                f"## Rollback\n\n`argocd app rollback {s.name}`\n\n## Escalation\n\n"
                f"Database trouble: database-reliability. Anything else: the owning team (`teams/ownership.yaml`).\n")
    if shape == "stale":
        old = owner_then(w, s)
        return (f"# {s.name}\n\nOwner: {old} (#{old}-alerts). Written before the last reorg; "
                f"`teams/ownership.yaml` has the current owner.\n\n"
                f"Old wiki page: https://wiki.{d}/display/{w.company.key}/{s.name} (gone since the migration)\n\n"
                f"TODO: update after the move to ArgoCD\n\n- Down: last time `helm rollback {s.name}` fixed it.\n"
                f"- Errors: svc-overview, split the panel by route.\n")
    if shape == "short":
        return f"# {s.name}\n\nAsk in #{s.team}-alerts. Rollbacks: `argocd app rollback {s.name}`.\n"
    return (f"# {s.name}\n\nCopied from the {other.name} page, needs a proper pass.\n\n"
            f"- Down: `kubectl -n {ns} rollout history deploy/{other.name}`\n"
            f"- Errors: https://grafana.{d}/d/svc-overview?var-service={s.name}\n")


def _b(w, s, shape: str, other) -> str:
    ns = w.namespace
    if shape == "full":
        return (f"# Runbook: {s.name}\n\n**Pages for:** `{s.camel}Down` (no healthy targets), `{s.camel}HighErrorRatio` "
                f"(5xx share over its threshold).\n\n**Look at:** the {s.name} row on svc-overview, then recent deploys "
                f"in #deploys.\n\n**Do:**\n- bad deploy: `kubectl -n {ns} rollout undo deploy/{s.name}`\n"
                f"- pods pending: check the node pool with platform\n- database errors in the logs: database-reliability\n")
    if shape == "stale":
        old = owner_then(w, s)
        return (f"# Runbook: {s.name}\n\n_Maintained by {old}. (Check `teams/ownership.yaml`, this line is older than "
                f"it.)_\n\n**Do:** `kubectl -n {ns} rollout undo deploy/{s.name}` if a deploy went out in the last hour.\n"
                f"FIXME: the dashboard link moved, find the new one.\n")
    if shape == "short":
        return f"# Runbook: {s.name}\n\nUndo the last rollout, then ask {s.team}.\n"
    return (f"# Runbook: {s.name}\n\n**Pages for:** `{s.camel}Down`, `{s.camel}HighErrorRatio`.\n\n"
            f"**Do:** `kubectl -n {ns} rollout undo deploy/{other.name}`\n(template from {other.name})\n")


def _c(w, s, shape: str, other) -> str:
    if shape == "full":
        return (f"# {s.name} on-call notes\n\nIf it's Down, the pods are gone or failing readiness; look at the last "
                f"release first, it is nearly always the last release. If it's the error ratio, find out whether it's "
                f"one route or all of them on the board, and whether a dependency is timing out.\n\n"
                f"Rolling back: `argocd app rollback {s.name}`. Rolling forward is fine if the fix is one line.\n"
                f"Call database-reliability for anything that looks like the database.\n")
    if shape == "stale":
        old = owner_then(w, s)
        return (f"# {s.name} on-call notes\n\n(These notes come from {old}; owners may have changed, "
                f"`teams/ownership.yaml` knows.)\n\nRestart the deployment, then look at the logs. "
                f"Nobody has updated this since the Helm days.\n")
    if shape == "short":
        return f"# {s.name} on-call notes\n\nNothing written yet. #{s.team}-alerts.\n"
    return (f"# {s.name} on-call notes\n\nSame as {other.name} for now: roll back first "
            f"(`argocd app rollback {other.name}`), ask questions later.\n")


def _d(w, s, shape: str, other) -> str:
    ns, d = w.namespace, w.domain
    if shape == "full":
        return (f"# {s.name}\n\n| alert | first thing to check |\n|---|---|\n"
                f"| `{s.camel}Down` | `kubectl -n {ns} get pods -l app={s.name}`; recent rollouts |\n"
                f"| `{s.camel}HighErrorRatio` | error panel by code on svc-overview; upstream status pages |\n\n"
                f"Logs: https://logs.{d}/app/{s.name}. Rollback: `argocd app rollback {s.name}`.\n")
    if shape == "stale":
        old = owner_then(w, s)
        return (f"# {s.name}\n\n| owner | {old} (pre-reorg; see `teams/ownership.yaml`) |\n|---|---|\n"
                f"| logs | https://kibana.{d}/app/{s.name} (old stack) |\n")
    if shape == "short":
        return f"# {s.name}\n\n| ask | #{s.team}-alerts |\n|---|---|\n"
    return (f"# {s.name}\n\n| alert | first thing to check |\n|---|---|\n"
            f"| `{s.camel}Down` | `kubectl -n {ns} get pods -l app={other.name}` |\n")


STYLES = {"a": _a, "b": _b, "c": _c, "d": _d}


def service_runbooks(w, rng) -> dict[str, str]:
    """Runbooks for the first few services, in the house style of this repo (a quarter of the tasks each)."""
    style = STYLES[variety.house(w, "runbook-style", ["a", "b", "c", "d"])]
    svcs = w.services[:rng.choice([3, 3, 4])]
    moved = set((w.trigger or {}).get("moved", []))
    shapes = rng.sample(["full", "short", "copied"], 3)
    out = {}
    for i, s in enumerate(svcs):
        shape = "stale" if s.name in moved else shapes[i % 3]
        other = next(x for x in w.services if x.name != s.name)
        out[f"runbooks/{s.name}.md"] = _front(s) + "\n" + style(w, s, shape, other)
    return out
