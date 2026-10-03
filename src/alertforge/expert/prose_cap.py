"""Survey complaints and packets for the E5 semantics defects (H08, H09, H10, H23). Survey answers are
first person, unedited, and name the alert or the class of pages people were annoyed by. Three voices each,
spread over the E5 tasks (R2)."""

from __future__ import annotations

from . import evidence as ev
from . import variety
from .items import Ticket
from .people import crew
from .prose_svc import _day, _key, paged_team
from .vocab import poss
from .xscen import counter_from_rates, ip, pods, series

LOW = [0.62, 0.66, 0.70, 0.73, 0.76]


def kafka_runbook(s, namespace: str, w=None) -> str:
    opts = [f"""---
alerts: [KafkaConsumerStalled]
---
# KafkaConsumerStalled

A consumer group stopped committing offsets while it still has lag, i.e. work is waiting and nothing is
taking it. KafkaConsumerStalled should page within 15 minutes of a group stopping while it has lag.
A rebalance (a deploy, a scale-up) holds commits for up to 4 minutes while lag builds. That isn't a stall and
shouldn't page anyone.

- Check the {s.name} pods first (`kubectl -n {namespace} get pods -l app={s.name}`); a stuck rebalance looks the same from here.
- The exporter is `kafka-exporter` in the monitoring namespace; if it's down, this alert can't fire at all.
""", f"""---
alerts: [KafkaConsumerStalled]
---
# Consumer group stalled

Meaning: lag on the group but no commits, so messages pile up and nothing reads them. We want the page no more
than 15 minutes after the group stops. Deploys and scale-ups make the group rebalance, which pauses commits for up
to 4 minutes; that pause is normal and must not page.

1. `kubectl -n {namespace} get pods -l app={s.name}`: crashlooping or pending pods are the usual cause.
2. No kafka-exporter, no alert: check the `kafka-exporter` job's targets if the graph is empty.
""", f"""---
alerts: [KafkaConsumerStalled]
---
# KafkaConsumerStalled ({s.name})

Pages when a consumer group has lag and has stopped committing. The deadline we agreed: paged within 15 minutes
of the group stopping. A rebalance can hold commits back for as long as 4 minutes while lag grows, and that alone
is not worth waking anyone for.

Look at the {s.name} pods in `{namespace}` first. If kafka-exporter is down the alert is blind; its job lives in the
monitoring namespace.
"""]
    return variety.card(w, "any:H09:runbook", opts) if w is not None else opts[0]


def h08(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    clock = ev.Clock(_day(w, rng, 2, 18), "13:00", w.company.utc_offset, w.company.tz_label)
    n = 180
    on = rng.randint(40, 60)
    ramp = [0.81, 0.85, 0.88, 0.92, 0.96]
    ratio = [ramp[(t - on) % 5] if t >= on else LOW[t % 5] for t in range(n + 1)]
    lim = item.p["limit"]
    lab = {"service": s.name, "container": s.name, "namespace": w.namespace, "pod": pods(rng, s.name, 1)[0]}
    raws = [series("container_memory_working_set_bytes", lab, [int(lim * v * (1 + rng.uniform(-0.002, 0.002))) for v in ratio]),
            series("container_spec_memory_limit_bytes", lab, [lim] * (n + 1))]
    path = f"incidents/{w.cal.inc(clock.local(0).date(), '13:00', n + 600)}"
    files = {f"{path}/query-range/{s.name}-memory.json": ev.query_range(raws, clock)}
    kills = rng.choice(["three", "four", "two"])
    times = round((n - on) / 5, -1)
    text = variety.card(w, "survey:H08", [
        f"{s.name} got OOMKilled {kills} times overnight. ContainerMemoryNearLimit, which is supposed to open a "
        f"ticket before that happens, went pending about {times:.0f} times and never opened anything. We don't want a "
        f"page for memory. We want the ticket in the afternoon, not the OOM at night. (Memory pull from the afternoon "
        f"before is in `{path}/`.)",
        f"The memory ticket for {s.name} is useless. The indexer sits right under its limit for hours, filling its "
        f"segment buffer and flushing it every few minutes, and the alert just flickers between pending and "
        f"nothing. Then the pod dies. `{path}/` has what the graph looked like.",
        f"I'm tired of finding out about {poss(s.name)} memory from OOM kills ({kills} on my last night shift). "
        f"ContainerMemoryNearLimit goes pending, goes away, goes pending again, all afternoon, and no ticket ever "
        f"shows up in our queue. I don't need a page, I need the ticket. Graph from that afternoon: `{path}/`."])
    return Ticket(_key(w, rng), "ContainerMemoryNearLimit never opens a ticket", text, crew(w, rng, paged_team(w, s.name), 1)[0], files)


def h09(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    clock = ev.Clock(_day(w, rng, 2, 18), "08:00", w.company.utc_offset, w.company.tz_label)
    n = 120
    st, en = rng.randint(30, 45), rng.randint(90, 110)
    base = rng.uniform(40, 90)
    rates = [0.0 if st <= m < en else base for m in range(n + 1)]
    lags = [rng.randint(20, 200) if m < st else (int(base * 60 * (m - st)) if m < en else 50) for m in range(n + 1)]
    lab = {"consumergroup": item.p["group"], "topic": item.p["topic"], "job": "kafka-exporter",
           "instance": "kafka-exporter.monitoring:9308"}
    raws = [series("kafka_consumergroup_current_offset_sum", lab, counter_from_rates(rng, rates, 4 * 10**6)),
            series("kafka_consumergroup_lag_sum", lab, lags)]
    path = f"incidents/{w.cal.inc(clock.local(0).date(), '08:00', en)}"
    files = {f"{path}/query-range/{item.p['group']}.json": ev.query_range(raws, clock)}
    text = variety.card(w, "survey:H09", [
        f"KafkaConsumerStalled has never fired. Not once. {s.name} stopped consuming for over an hour on "
        f"{clock.day()} and we found out from the lag dashboard. The graph for the alert expression in Grafana is "
        f"mostly empty, which I don't understand. Offsets and lag from that morning: `{path}/`.",
        f"Is KafkaConsumerStalled even wired up? {item.p['group']} sat with growing lag from {clock.hm(st)} to "
        f"{clock.hm(en)} and nobody was paged. The runbook says it pages within 15 minutes. Pull in `{path}/`.",
        f"I spent a morning ({clock.day()}) watching {item.p['group']} lag climb on a dashboard while the stall alert "
        f"did nothing. {s.name} was stuck from about {clock.hm(st)}. If that alert can't catch an hour-long stall, "
        f"what is it for? Data: `{path}/`."])
    return Ticket(_key(w, rng), "KafkaConsumerStalled never fires", text, crew(w, rng, paged_team(w, s.name), 1)[0], files)


def h10(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    clock = ev.Clock(_day(w, rng, 6, 20), "22:00", w.company.utc_offset, w.company.tz_label)
    n = 90
    gone = rng.randint(40, 55)
    inst = f"{ip(rng)}:9102"
    up = [1] * (n + 1)
    target = {"job": s.name, "instance": inst, "service": s.name, "namespace": w.namespace, "pod": pods(rng, s.name, 1)[0]}
    raws = [series("up", target, up),
            series(item.p["metric"], target, [rng.randint(0, 5) for _ in range(gone)] + ["stale"] + [None] * (n - gone))]
    path = f"incidents/{w.cal.inc(clock.local(0).date(), '22:00', gone)}"
    files = {f"{path}/query-range/{s.name}-up.json": ev.query_range(raws[:1], clock),
             f"{path}/query-range/{s.name}-pending.json": ev.query_range(raws[1:], clock)}
    downstream = rng.choice(["finance", "the analytics team", "billing ops"])
    up1 = downstream[:1].upper() + downstream[1:]
    text = variety.card(w, "survey:H10", [
        f"{up1} noticed the nightly {s.name} output has been empty for six days. The {s.name} panel says "
        f"0 pending, which we read as caught up. It wasn't doing anything: after the node pool move it came back "
        f"without its queue credentials and stopped reporting batches at all. The pods were up the whole time, so "
        f"{s.camel}Down had nothing to say, and nothing else told us either. If the batch numbers stop coming we want "
        f"a ticket within half an hour, not a week later. (The batch gauge drops out for a minute or two whenever "
        f"the consumer reconnects to the queue. That's normal and shouldn't open anything.) The pulls from the "
        f"night it stopped are in `{path}/`.",
        f"Six days of missing {s.name} runs and not a single ticket. The pods were up and the panel showed 0 pending "
        f"the whole time. When its batch numbers stop we want a ticket within 30 minutes. A blip of a minute or two "
        f"when it reconnects is fine, that happens every day. See `{path}/`.",
        f"I was the one {downstream} called about {s.name}: a week of empty output and we had no idea. The pods "
        f"were fine and the dashboard said 0 pending, which looks exactly like caught up. The batch metric had simply "
        f"stopped arriving. I'd like a ticket within 30 minutes of that happening. It does vanish for a minute or two "
        f"on every reconnect, which is harmless. `{path}/` has the night it stopped."])
    return Ticket(_key(w, rng), f"{s.name} dead for six days, panel said 0", text, crew(w, rng, paged_team(w, s.name), 1)[0], files)


def h23(item, w, rng) -> Ticket:
    s = w.svc(item.p["svc"])
    clock = ev.Clock(_day(w, rng, 2, 12), "00:00", w.company.utc_offset, w.company.tz_label)
    gb = 10**9
    n = 360
    wr = rng.randint(48, 62)
    avail = [int(103 * gb - (wr * gb * (m - 70) / 50 if 70 <= m < 120 else wr * gb if 120 <= m < 170 else 0)) for m in range(n + 1)]
    lab = {"service": s.name, "mountpoint": "/var/lib/postgresql", "device": "/dev/nvme1n1", "fstype": "ext4",
           "instance": f"{ip(rng)}:9100", "job": "node"}
    raws = [series("node_filesystem_avail_bytes", lab, avail), series("node_filesystem_size_bytes", lab, [200 * gb] * (n + 1))]
    path = f"incidents/{w.cal.inc(clock.local(0).date(), '00:00', 80)}"
    files = {f"{path}/query-range/{s.name}-db-volume.json": ev.query_range(raws, clock)}
    adr = w.notes.get("adr", {}).get("disk", "0012")
    text = variety.card(w, "survey:H23", [
        f"DiskWillFillIn4h, three nights running at around {clock.hm(80)} on the {s.name} database. The volume is "
        f"half empty. It's the backup job, it's gone by 03:00. I've started acking it without looking, which is "
        f"exactly how we'll miss the real one. Volume pull from one of those nights: `{path}/`.",
        f"Please make DiskWillFillIn4h stop paging for the nightly backup on {poss(s.name)} database. Half the volume "
        f"is free. It pages, the backup finishes, it resolves. ADR {adr} is what we agreed; this isn't it.",
        f"Every night around {clock.hm(80)} the disk prediction for the {s.name} database wakes someone up, and every "
        f"night it's the backup writing its dump. Half the disk is free. I would rather be paged when it's actually "
        f"full. One of those nights: `{path}/`."])
    return Ticket(_key(w, rng), "DiskWillFillIn4h pages every night", text, crew(w, rng, paged_team(w, s.name), 1)[0], files)
