"""H8: the inhibition simulator agrees with a real Alertmanager process on the generated case shapes.

Marked `am`: needs the alertmanager binary (AF_ALERTMANAGER, PATH, or ../../vendor/bin/alertmanager).
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import pytest
import yaml

from alertforge import routing
from alertforge.grader.amcheck import inhibited

from conftest import VENDOR_BIN

AM = os.environ.get("AF_ALERTMANAGER") or shutil.which("alertmanager") or os.path.join(VENDOR_BIN, "alertmanager")
pytestmark = [pytest.mark.am, pytest.mark.skipif(not os.path.exists(AM), reason="alertmanager binary not found")]

RULES = {
    "oracle": routing.inhibit_rule("SvcDown", ["BurnFast", "HighErrorRatio"]),
    "R05": {k: v for k, v in routing.inhibit_rule("SvcDown", ["BurnFast", "HighErrorRatio"]).items() if k != "equal"},
    "legacy": {"source_match": {"alertname": "SvcDown"}, "target_match_re": {"alertname": "Burn.*"}, "equal": ["service"]},
}


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _real(rule: dict, source: dict | None, target: dict) -> bool:
    d = tempfile.mkdtemp()
    cfg = {"route": {"receiver": "null"}, "receivers": [{"name": "null"}], "inhibit_rules": [rule]}
    with open(os.path.join(d, "am.yml"), "w") as fh:
        yaml.safe_dump(cfg, fh)
    port, cport = _port(), _port()
    proc = subprocess.Popen([AM, f"--config.file={d}/am.yml", f"--storage.path={d}/data",
                             f"--web.listen-address=127.0.0.1:{port}", f"--cluster.listen-address=",
                             f"--cluster.advertise-address=127.0.0.1:{cport}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}/api/v2"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{base}/status", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        alerts = [{"labels": a} for a in ([source] if source else []) + [target]]
        req = urllib.request.Request(f"{base}/alerts", json.dumps(alerts).encode(), {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        want = target["alertname"]
        for _ in range(30):
            time.sleep(0.2)
            got = json.load(urllib.request.urlopen(f"{base}/alerts", timeout=5))
            tgt = [a for a in got if a["labels"].get("alertname") == want and a["labels"] == target]
            if tgt and (tgt[0]["status"]["inhibitedBy"] or len(got) == len(alerts)):
                return bool(tgt[0]["status"]["inhibitedBy"])
        return False
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(d, ignore_errors=True)


CASES = routing.inhibit_cases("SvcDown", ["BurnFast", "HighErrorRatio"], "chat-gw", "orders-api", "payments", "PodMemoryHigh")


@pytest.mark.parametrize("rule_name", sorted(RULES))
@pytest.mark.parametrize("i", range(len(CASES)))
def test_simulator_matches_alertmanager(rule_name, i):
    c = CASES[i]
    rule = RULES[rule_name]
    sim = inhibited({"inhibit_rules": [rule]}, c["source"], c["target"])
    assert sim == _real(rule, c["source"], c["target"])
