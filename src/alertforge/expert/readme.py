"""The repo README (house rules), in the words of whoever wrote it at each company (R2, editorial pass E8).

Every paragraph has three wordings and every wording states the same rules: the severity table, the alert
classes, Down semantics, SLO names, the routing table, the inhibition rules, the `runbook_url` pattern, the group
and `ALERTS` rules. Each wording is used by a third of the tasks of a build (`variety.house`), the section order
and headings vary, and every README ends with what this particular repo does differently.
"""

from __future__ import annotations

from . import knobs, variety


def _pick(w, key: str, opts: list[str]) -> str:
    return variety.house(w, f"readme:{key}", opts)


def intro(w, f) -> str:
    c = w.company
    return _pick(w, "intro", [
        f"Prometheus rules and the shared Alertmanager config for {c.name}. The observability team owns this repo. "
        f"Service teams file `{c.key}-` tickets against it and review changes to their own rules. Alertmanager is "
        f"shared with the platform and data Prometheus servers, so most team subtrees route alerts that don't come "
        f"from here.",
        f"This is where {c.name}'s alerting lives: the rules our Prometheus evaluates and the Alertmanager config every "
        f"Prometheus in the company sends to. Observability owns it; teams ask for changes with `{c.key}-` tickets and "
        f"review whatever touches their own rules. The platform and data Prometheus servers use the same "
        f"Alertmanager, which is why many team subtrees route alerts this repo never fires.",
        f"Alerting rules and routing for {c.name}, maintained by observability. If you own a service, open a "
        f"`{c.key}-` ticket for changes and expect to review the ones to your rules. One Alertmanager serves this "
        f"Prometheus as well as the platform and data ones, so plenty of the routing below is for alerts defined "
        f"elsewhere."])


def layout(w, f) -> str:
    ops = _pick(w, "layout-ops", [
        "PagerDuty exports and postmortems are the authoritative times; Slack threads are local time and can be a minute off.",
        "times: PagerDuty and postmortems win; Slack is local time and sometimes a minute out",
        "PagerDuty and postmortem times are the ones to trust; Slack exports are in local time, give or take a minute"])
    rows = _pick(w, "layout-rows", [
        f"""| what | where | authoritative for |
|---|---|---|
| alerting and recording rules | `rules/` | |
| routing, receivers, inhibition | `alertmanager/alertmanager.yml` | |
| SLO catalog | `slo/services.yaml` | targets, SLO windows, SLI families, traffic floors |
| who owns what | `teams/ownership.yaml` | team of every service (wins over anything a runbook says) |
| policies | `docs/adr/` | burn-rate policy, scrape intervals, disk alerts |
| runbooks | `runbooks/*.md` | what an alert is for and how fast it has to page |
| incident packets | `incidents/` | {ops} |
| smoke tests | `tests/visible/` | just the smoke suite, not a spec |""",
        f"""| path | contents | source of truth for |
|---|---|---|
| `rules/` | every alerting and recording rule | |
| `alertmanager/alertmanager.yml` | routes, receivers, inhibitions | |
| `slo/services.yaml` | the SLO catalog | SLO targets and windows, SLI family per service, traffic floors |
| `teams/ownership.yaml` | owners | which team owns each service; beats any runbook |
| `docs/adr/` | decisions | burn-rate policy, scrape intervals, disk alerts |
| `runbooks/*.md` | one page per alert or service | what the alert means, how quickly it must page |
| `incidents/` | packets from past incidents | {ops} |
| `tests/visible/` | the smoke suite | nothing; it is not a spec |""",
        f"""- `rules/`: alerting and recording rules.
- `alertmanager/alertmanager.yml`: routing tree, receivers, inhibition rules.
- `slo/services.yaml`: the SLO catalog, the one place for targets, SLO windows, SLI families and traffic floors.
- `teams/ownership.yaml`: the team that owns each service. If a runbook disagrees, this file is right.
- `docs/adr/`: decisions (burn-rate policy, scrape intervals, disk alerts).
- `runbooks/*.md`: what each alert is for and how fast it has to page.
- `incidents/`: packets from past incidents ({ops}).
- `tests/visible/`: a smoke suite. Not a spec."""])
    tools = _pick(w, "layout-tools", [
        "`bin/af-check` is CI (promtool check, the smoke tests, amtool check-config). `bin/range2test` turns a "
        "`query_range` pull from an incident packet into a promtool test so you can replay it against a branch.",
        "CI is `bin/af-check`: promtool check, the smoke tests, amtool check-config. To replay an incident against a "
        "branch, feed one of the packet's `query_range` pulls to `bin/range2test` and run the promtool test it writes.",
        "Two scripts: `bin/af-check` (what CI runs: promtool check, smoke tests, amtool check-config) and "
        "`bin/range2test`, which makes a promtool test out of a `query_range` pull from `incidents/`, for replaying an "
        "incident against your change."])
    return f"{rows}\n\n{tools}"


def house(w, f) -> str:
    return _pick(w, "house", [
        "Older files predate the conventions below; don't reformat them in passing. All groups run at the global "
        "evaluation interval (1m); we don't use `interval:`, `query_offset:` or `limit:` on groups. Rules don't read "
        "`ALERTS` or `ALERTS_FOR_STATE` (an alert keeping its own firing series up is the one exception we allow): when "
        "one alert should hold another back, that's an inhibition rule in Alertmanager, where on-call can see it.\n"
        "The deploy job refuses a repo with symlinks in it, anywhere, so there are none: copy a file instead of "
        "linking it.",
        "Some files are older than the conventions here. Leave their formatting alone unless you are changing them "
        "for a reason. Every group runs at the global evaluation interval of 1m: no `interval:`, `query_offset:` or "
        "`limit:` on a group. No rule reads `ALERTS` or `ALERTS_FOR_STATE`, except an alert that keeps its own firing "
        "series up. If one alert should silence another, write an inhibition rule in Alertmanager so on-call can see "
        "it.\nNo symlinks anywhere in the repo: the deploy job rejects them. Copy the file instead.",
        "A word on old files: they predate the rules below, and we don't reformat them in drive-by changes. Groups "
        "all evaluate at the global interval (1m); `interval:`, `query_offset:` and `limit:` aren't used on groups "
        "here. Reading `ALERTS` or `ALERTS_FOR_STATE` from a rule is off limits; the only allowed case is an alert "
        "holding up its own firing series. Holding one alert back while another fires is Alertmanager's job "
        "(inhibition), visible to on-call.\nThe deploy job fails on any symlink in the repo, so copy files rather "
        "than linking them."])


def sees(w, f) -> str:
    adr = f["scrape"]
    return _pick(w, "sees", [
        "This Prometheus scrapes our apps and the ingress. Every app target gets a `service` label from its Kubernetes "
        "Service name and a `pod` label at relabel time, so `up{service=\"…\"}` is how you find a service's targets. "
        "kube-state-metrics and the kubelet live in the platform team's Prometheus, which we don't alert from. Platform "
        "federates a short allowlist of cAdvisor container series into ours (`container_memory_*`, `container_spec_*`, "
        "`container_cpu_*`) and nothing from kube-state-metrics. Synthetic checks run in the vendor's uptime tool, "
        "which only covers the public homepage. For us, `up` and the services' own request counters are the "
        "liveness signals.\n"
        f"Scrape intervals per job are in ADR {adr}. We size rate windows at about four scrape intervals.",
        "We scrape the app pods and the ingress controller. Relabelling puts the Kubernetes Service name in `service` "
        "and the pod name in `pod` on every app target, so a service's targets are `up{service=\"…\"}`. The kubelet "
        "and kube-state-metrics are scraped by platform's Prometheus and we don't alert on them; platform federates an "
        "allowlist of cAdvisor series to us (`container_memory_*`, `container_spec_*`, `container_cpu_*`), none from "
        "kube-state-metrics. The uptime vendor's synthetic checks cover the public homepage only. That leaves `up` and "
        "each service's own request counters as our liveness signals.\n"
        f"ADR {adr} has the scrape interval of every job. A rate window should cover about four scrape intervals.",
        "What we can see from here: our apps and the ingress. App targets are relabelled with `service` (the "
        "Kubernetes Service name) and `pod`; `up{service=\"…\"}` lists a service's targets. Platform runs the "
        "Prometheus with the kubelet and kube-state-metrics in it, and we don't alert from that one. They federate "
        "a few cAdvisor container series into ours (`container_memory_*`, `container_spec_*`, `container_cpu_*`), "
        "and nothing from kube-state-metrics. The vendor's synthetic checks only watch the public homepage, so "
        "liveness here means `up` and the services' own request counters.\n"
        f"Per-job scrape intervals: ADR {adr}. Rate windows are sized at roughly four scrape intervals."])


# every variant below says `critical` is the older spelling of `page` (same routing, same page); the spec carries it
# so that the grader's "leave as is" check reads either spelling as the same severity (build.build_task)
SEVERITY_ALIAS = {"critical": "page"}


def severity(w, f) -> str:
    return _pick(w, "severity", [
        "`page` wakes someone up. `critical` is the older spelling of `page`: it routes and pages the same way, and new "
        "rules use `page`. `warning` pages nobody. Generation-1 files still have a few two-tier alerts with a `warning` "
        "and a `critical` copy.\n\n"
        "We page when users are hurt now, or will be within the hour, and whoever gets paged can act on it. "
        "Everything else is a ticket (`severity: ticket`), which lands in the owning team's queue.",
        "Severities: `page` gets someone out of bed; `critical` means the same thing in older rules (same routing, same "
        "page) and new rules say `page`; `warning` goes to Slack and pages nobody. A few generation-1 alerts come in "
        "pairs, a `warning` copy and a `critical` copy.\n\n"
        "A page is for users hurting now or within the hour, and only if the person paged can do something about "
        "it. Anything else is `severity: ticket` and goes to the owning team's queue.",
        "- `page`: wakes someone up.\n- `critical`: the old name for `page`. Routed and paged exactly like it; write "
        "`page` in new rules.\n- `warning`: pages nobody.\n- `ticket`: lands in the owning team's queue.\n\n"
        "Some generation-1 alerts still exist twice, as a `warning` and a `critical` copy. The rule for paging: "
        "users are affected now or will be within the hour, and the person who gets paged can act. If not, it's "
        "a ticket."])


def classes(w, f) -> str:
    disk = f["disk"]
    text = _pick(w, "classes", [
        "- **Service alerts** (`<ServiceCamel>HighErrorRatio`, `<ServiceCamel>Down`, burn alerts) page the owning team.\n"
        "  Service error alerts are per service, never per pod; one page per service per outage. They use a 5m window\n"
        "  and `for: 10m`. Errors are 5xx; 4xx are the caller's problem.\n"
        "- **Capacity alerts** (`ContainerMemoryNearLimit`, disk) open a ticket for the owning team, except where an ADR\n"
        f"  says a level pages (disk: ADR {disk}). `ContainerMemoryNearLimit` opens a ticket when a container keeps reaching\n"
        "  90% of its memory limit for 10 minutes. One busy 5-minute stretch isn't that. We want the ticket inside 20\n"
        "  minutes of the first time it reaches 90%, which leaves time before the OOM.\n"
        "- **Fleet alerts** (node, database, cache, broker exporters) are `infra`'s, `severity: warning`, and page nobody.\n"
        "- Any other alert keeps the `severity` and `team` it has unless a ticket says otherwise. Ticket alerts carry\n"
        "  `service` like pages do.\n\n"
        "Every alert has labels `severity` and `team`. Service, capacity and ticket alerts also carry `service` on the\n"
        "alert (from the expression or as a static label). Annotations: a `summary` that says what is affected, and\n"
        "`runbook_url`.",
        "Service alerts are `<ServiceCamel>HighErrorRatio`, `<ServiceCamel>Down` and the burn alerts. They page the team "
        "that owns the service, and error alerts are per service, not per pod, so an outage is one page per service. "
        "Window 5m, `for: 10m`. Only 5xx count as errors; a 4xx is the caller's problem.\n\n"
        "Capacity alerts (`ContainerMemoryNearLimit`, disk) are tickets for the owning team unless an ADR makes a level "
        f"page (disk: ADR {disk}). `ContainerMemoryNearLimit` is for a container that keeps hitting 90% of its memory "
        "limit for 10 minutes, not for one busy 5-minute stretch, and the ticket should be open within 20 minutes of "
        "the first time it hits 90%, well before the OOM.\n\n"
        "Fleet alerts (node, database, cache and broker exporters) belong to `infra`, are `severity: warning`, and "
        "page nobody. All other alerts keep their `severity` and `team` unless a ticket asks for a change. Ticket "
        "alerts carry `service`, same as pages.\n\n"
        "Labels on every alert: `severity`, `team`. Service, capacity and ticket alerts carry `service` as well, from "
        "the expression or set statically. Annotations: a `summary` naming what is affected, and a `runbook_url`.",
        "| class | examples | goes to | rules |\n|---|---|---|---|\n"
        "| service | `<ServiceCamel>HighErrorRatio`, `<ServiceCamel>Down`, burn alerts | owning team, paged | per "
        "service and never per pod (one page per service per outage); error alerts on a 5m window with `for: 10m`; "
        "errors are 5xx (4xx are the caller's problem) |\n"
        f"| capacity | `ContainerMemoryNearLimit`, disk | owning team, ticket | an ADR can make a level page (disk: ADR {disk}) |\n"
        "| fleet | node, database, cache, broker exporters | `infra`, `severity: warning` | pages nobody |\n\n"
        "`ContainerMemoryNearLimit` means a container keeps reaching 90% of its memory limit for 10 minutes; a "
        "single busy 5-minute stretch doesn't count. The ticket should exist within 20 minutes of the first time the "
        "container reaches 90%, so there is time before the OOM kill. Alerts outside these classes keep their "
        "`severity` and `team` unless a ticket changes them, and ticket alerts carry `service` the way pages do.\n\n"
        "All alerts: labels `severity` and `team`; service, capacity and ticket alerts add `service` (from the "
        "expression or as a static label); annotations `summary` (what is affected) and `runbook_url`."])
    if knobs.on("label-rule"):   # the grader's reading (xscore.labels_cover), stated; off in the v0.2 candidates
        text += " Other labels are fine as long as they don't change where the alert goes."
    return text


def health(w, f) -> str:
    return _pick(w, "health", [
        "Each service has one Down alert, `<ServiceCamel>Down` (`checkout-api` → `CheckoutApiDown`). It pages the owning "
        "team within 10 minutes of the service losing its last healthy target, including when the targets are gone. A "
        "restart that drops targets for up to 2 minutes must not page. Labels: `severity: page`, `team`, and `service`.",
        "One Down alert per service, named `<ServiceCamel>Down` (so `checkout-api` has `CheckoutApiDown`). When a service "
        "has no healthy target left, and that includes having no targets at all, its owner gets paged within 10 "
        "minutes. Targets missing for 2 minutes or less (a restart) must not page. It carries `severity: page`, "
        "`team` and `service`.",
        "`<ServiceCamel>Down` (for `checkout-api`: `CheckoutApiDown`) is the only Down alert a service has. Lose the last "
        "healthy target, or lose the targets altogether, and the owning team is paged within 10 minutes; a restart that "
        "takes targets away for at most 2 minutes doesn't page. Labels `severity: page`, `team`, `service`."])


def slos(w, f) -> str:
    adr = f["slo"]
    return _pick(w, "slos", [
        "SLI records are `slo:sli_error:ratio_rate<window>` for 5m, 30m, 1h and 6h: the bad fraction, `by (service)`, "
        "covering every service that emits the family's metrics (don't filter them by service). Latency SLIs are per "
        "service (`slo:sli_latency:ratio_rate<window>`), because the objective is per service. Burn alerts only read the "
        "SLI records for their own service. Names: `<ServiceCamel>ErrorBudgetBurnFast` (`for: 2m`, `severity: page`) and "
        "`<ServiceCamel>ErrorBudgetBurnSlow` (`for: 15m`, `severity: ticket`), labels `severity`, `team` and "
        f"`slo: <service>-availability`. The factors come from ADR {adr} and the window in the catalog.",
        "Error SLIs are recorded as `slo:sli_error:ratio_rate<window>` (windows 5m, 30m, 1h, 6h): the fraction of bad "
        "requests `by (service)`, one record per window for every service with the family's metrics. Don't add a "
        "service filter to them. Latency SLIs are recorded per service as `slo:sli_latency:ratio_rate<window>`, since "
        "each service has its own objective. A burn alert reads its own service's SLI records and nothing else; it is "
        "called `<ServiceCamel>ErrorBudgetBurnFast` (`severity: page`, `for: 2m`) or `<ServiceCamel>ErrorBudgetBurnSlow` "
        "(`severity: ticket`, `for: 15m`) and is labelled `severity`, `team` and `slo: <service>-availability`. Factors: "
        f"ADR {adr}; window: the catalog.",
        "- SLI records: `slo:sli_error:ratio_rate<window>`, windows 5m, 30m, 1h and 6h, the bad fraction "
        "`by (service)` across every service that emits the family's metrics (no per-service filter).\n"
        "- Latency SLIs: `slo:sli_latency:ratio_rate<window>`, one per service because objectives differ per service.\n"
        "- Burn alerts read only their own service's SLI records: `<ServiceCamel>ErrorBudgetBurnFast` (`for: 2m`, "
        "`severity: page`), `<ServiceCamel>ErrorBudgetBurnSlow` (`for: 15m`, `severity: ticket`), labelled `severity`, "
        "`team`, `slo: <service>-availability`.\n"
        f"- Burn factors: ADR {adr}. SLO window: `slo/services.yaml`."])


TABLES = ["""| severity | receivers |
|---|---|
| page, critical | `pagerduty-<team>`, `slack-<team>`, and `slack-sre-fyi` |
| ticket | `jira-<team>`, `slack-<team>` |
| warning | `slack-<team>` |""", """| alert severity | delivered to |
|---|---|
| `page` or `critical` | `pagerduty-<team>` + `slack-<team>` + `slack-sre-fyi` |
| `ticket` | `jira-<team>` + `slack-<team>` |
| `warning` | `slack-<team>` only |""", """- `page` and `critical`: `pagerduty-<team>`, `slack-<team>`, `slack-sre-fyi`
- `ticket`: `jira-<team>`, `slack-<team>`
- `warning`: `slack-<team>`"""]


def routing(w, f) -> str:
    table = _pick(w, "routing-table", TABLES)
    return _pick(w, "routing", [
        "The `team` label on the alert is the routing key and has to match `teams/ownership.yaml`. Each team has a "
        f"subtree matching its `team`:\n\n{table}\n\n"
        "SRE watches every page in #sre-fyi (`slack-sre-fyi`); nobody is paged from there. The root receiver "
        "`slack-alerts-default` is the catch-all for alerts with no `team`; nobody is on it. Rule evaluation failures go "
        "to `slack-prometheus-meta`. The paging unit is the service: one page per service per outage; two services "
        "failing together are two pages, even for the same team. Don't change receivers you don't own, `global`, or add "
        "time intervals.",
        "Alerts are routed on their `team` label, which must be a team in `teams/ownership.yaml`. Every team has its own "
        f"subtree, matched on `team`, that sends:\n\n{table}\n\n"
        "`slack-sre-fyi` is #sre-fyi, where SRE sees every page; it pages nobody. Anything without a `team` ends up at the "
        "root receiver, `slack-alerts-default`, which nobody watches. Failed rule evaluations are routed to "
        "`slack-prometheus-meta`. We page per service: one page per service per outage, so two services down at once "
        "means two pages even when they belong to the same team. Leave other teams' receivers, `global` and time "
        "intervals alone (don't add any).",
        f"Routing key: the alert's `team` label, matching `teams/ownership.yaml`. One subtree per team:\n\n{table}\n\n"
        "Notes: #sre-fyi (`slack-sre-fyi`) is how SRE keeps an eye on every page, and nobody is paged from it. The root "
        "receiver `slack-alerts-default` catches whatever has no `team`; no one reads it. Evaluation failures go to "
        "`slack-prometheus-meta`. One page per service per outage: two failing services are two pages, also within one "
        "team. Not yours to change: other teams' receivers, `global`. Don't add time intervals."])


def inhibition(w, f) -> str:
    cluster = ""
    if w.clusters:
        cluster = _pick(w, "inhibition-cluster", [
            "\nCluster-level alerts carry no `service`; the list is `ClusterUnreachable`. When `ClusterUnreachable` fires "
            "it pages platform (`pagerduty-platform`) and holds back the service pages in that cluster, `equal: [cluster]`.",
            "\nThe only cluster-level alert is `ClusterUnreachable`, and it has no `service` label. It pages platform "
            "(`pagerduty-platform`), and while it fires the service pages in the same cluster are held back "
            "(`equal: [cluster]`).",
            "\nCluster level: `ClusterUnreachable` (no `service` label) pages platform through `pagerduty-platform`; "
            "while it is firing, service pages from that cluster stay held back, `equal: [cluster]`."])
    return _pick(w, "inhibition", [
        "Between two services' own alerts, `equal: [service]`, so one service's outage never silences another's. A "
        "critical copy of an alert holds back the warning copy of the same alert for the same service: "
        "`equal: [alertname, service]`.",
        "Inhibitions between service alerts use `equal: [service]`: an outage of one service must never mute another "
        "service's alerts. The critical copy of an alert holds back its warning copy, same alert and same service, "
        "with `equal: [alertname, service]`.",
        "- service alerts holding back other service alerts: always `equal: [service]`, so no outage silences another "
        "service.\n- critical copy over warning copy of the same alert for the same service: `equal: [alertname, service]`."]) + cluster


def runbooks(w, f) -> str:
    return _pick(w, "runbooks", [
        f"`runbook_url` is `https://runbooks.{w.domain}/alerts/<AlertName>`. The site is built from `runbooks/*.md` in "
        "this repo, and each page lists the alerts it covers in its front matter.",
        f"Runbook links point at `https://runbooks.{w.domain}/alerts/<AlertName>`. That site is generated from "
        "`runbooks/*.md` here; a page's front matter lists the alerts it covers.",
        f"Use `https://runbooks.{w.domain}/alerts/<AlertName>` as the `runbook_url`. The runbook site is built from "
        "`runbooks/*.md`, and the front matter of each page says which alerts it covers."])


def extras(w, f) -> str:
    """What this repo does that the rules above don't: one of three kinds of section, each true of this world."""
    kind = _pick(w, "extras", ["exceptions", "changelog", "contacts"])
    legacy = sorted(x.stem for x in w.files.values() if x.gen == 1 and x.stem.startswith("legacy-"))
    operator = sorted(x.stem for x in w.files.values() if x.gen == 3)
    dead = w.notes.get("dead_receiver")
    if kind == "exceptions":
        lines = ["## Known exceptions", "", "| where | what | why it's still there |", "|---|---|---|"]
        if legacy:
            lines.append(f"| {', '.join(f'`rules/{x}.yml`' for x in legacy)} | inline ratios, two-tier warning/critical "
                         f"copies, quoted durations | generation 1; moved out when someone has a reason to touch them |")
        if operator:
            lines.append(f"| {', '.join(f'`rules/{x}.yaml`' for x in operator)} | PrometheusRule manifests | those teams "
                         f"deploy rules with the operator |")
        lines.append("| fleet files (`node-exporter`, `postgres`, ...) | upstream thresholds | nobody has tuned them |")
        if dead:
            lines.append(f"| `{dead}` receiver | routes nothing | kept until its last user is confirmed gone |")
        return "\n".join(lines)
    if kind == "changelog":
        mon = ["2025-11", "2026-02", "2026-05", "2026-08"]
        return "\n".join(["## Changelog", "",
                          f"- {mon[0]}: burn alerts moved to the SLI records (ADR {f['slo']}).",
                          f"- {mon[1]}: `slack-sre-fyi` added; SRE stopped being paged for every team.",
                          f"- {mon[2]}: disk alerts per ADR {f['disk']}.",
                          f"- {mon[3]}: kafka-exporter scraped every 1m instead of 15s (ADR {f['scrape']})."])
    return "\n".join(["## Contacts", "",
                      "- Questions: #obs-help. Office hours Tuesday and Thursday afternoons.",
                      f"- Urgent routing problems out of hours: the SRE rota (`pagerduty-sre`).",
                      "- Reviews: observability for anything under `alertmanager/`; the owning team for its rules."])


SECTIONS = [("Where things live", layout), (None, house), ("What this Prometheus sees", sees),
            ("Severity and paging", severity), ("Alert classes", classes), ("Service health", health), ("SLOs", slos),
            ("Routing", routing), ("Inhibition", inhibition), ("Runbooks", runbooks)]
HEADINGS = {"Where things live": ["Where things live", "Layout", "What's where"],
            "What this Prometheus sees": ["What this Prometheus sees", "Scrape targets", "What we scrape"],
            "Severity and paging": ["Severity and paging", "Paging policy", "Severities"],
            "Alert classes": ["Alert classes", "Kinds of alerts", "Alert classes"],
            "Service health": ["Service health", "Down alerts", "Service health"],
            "SLOs": ["SLOs", "SLO alerting", "SLIs and burn alerts"],
            "Routing": ["Routing", "Who gets what", "Routing"],
            "Inhibition": ["Inhibition", "Inhibition rules", "Inhibitions"],
            "Runbooks": ["Runbooks", "Runbook links", "Runbooks"]}


def readme(w, f: dict) -> str:
    order = list(SECTIONS)
    if _pick(w, "order", [0, 1]):
        i, j = order.index(("Alert classes", classes)), order.index(("Routing", routing))
        order[i], order[j] = order[j], order[i]
    title = _pick(w, "title", ["# monitoring", f"# {w.company.name} monitoring", "# alerting"])
    out = [title, "", intro(w, f), ""]
    for head, fn in order:
        if head:
            out += [f"## {_pick(w, 'h:' + head, HEADINGS[head])}", ""]
        out += [fn(w, f), ""]
    out += [extras(w, f), ""]
    return "\n".join(out)
