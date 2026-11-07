"""Tests for tools/desktop_qa/offline_check.py (lane Q1).

Run: py -3.12 -m pytest tools/desktop_qa/test_offline_check.py -q
The TEST-NET negative control lives in `offline_check.py selftest` (it needs a
real in-flight connect); these tests cover the scanner, netstat parser and the
loopback sampler deterministically.
"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline_check as oc  # noqa: E402

NETSTAT_SAMPLE = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:18851        0.0.0.0:0              LISTENING       4001
  TCP    127.0.0.1:52000        127.0.0.1:18851        ESTABLISHED     4002
  TCP    127.0.0.1:52001        192.0.2.1:9            SYN_SENT        4002
  TCP    127.0.0.1:52002        93.184.216.34:443      ESTABLISHED     4999
  UDP    0.0.0.0:5353           *:*                                    4001
  UDP    127.0.0.1:1900         127.0.0.1:1900                         4002
"""


def _run(argv):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = oc.main(argv)
    return code, json.loads(buffer.getvalue())


def test_parse_netstat_tcp_and_udp_rows():
    rows = oc.parse_netstat(NETSTAT_SAMPLE)
    assert len(rows) == 6
    listening = rows[0]
    assert listening["state"] == "LISTENING" and listening["pid"] == 4001
    udp = rows[4]
    assert udp["proto"] == "UDP" and udp["state"] == "" and udp["pid"] == 4001


def test_split_addr_handles_ipv4_and_ipv6():
    assert oc._split_addr("127.0.0.1:18851") == ("127.0.0.1", "18851")
    assert oc._split_addr("[::1]:18851") == ("::1", "18851")
    assert oc._split_addr("0.0.0.0:0") == ("0.0.0.0", "0")


def test_relevant_filters_listening_and_wildcards():
    rows = oc.parse_netstat(NETSTAT_SAMPLE)
    targets = {4001, 4002}
    kept = [row for row in rows if oc._relevant(row, targets)]
    remotes = {row["foreign"] for row in kept}
    # LISTENING, wildcard UDP and non-target PIDs are dropped
    assert remotes == {"127.0.0.1:18851", "192.0.2.1:9", "127.0.0.1:1900"}


def test_scan_allowlists_media_and_flags_remote(tmp_path):
    root = tmp_path / "repo"
    (root / "bot/replay").mkdir(parents=True)
    (root / "bot/web/static/replays_app").mkdir(parents=True)
    (root / "bot/replay/normalize.py").write_text(
        '# source: https://valorant-api.com/v1/maps\n'
        'IMG = "https://media.valorant-api.com/maps/x/displayicon.png"\n', "utf-8")
    (root / "bot/replay/p2_review.py").write_text(
        'LOCAL = "http://127.0.0.1:11434/api/chat"\n', "utf-8")
    (root / "bot/replay/evil.py").write_text(
        'URL = "https://telemetry.example/collect"\n', "utf-8")
    (root / "bot/replay/test_evil.py").write_text(
        'URL = "https://ignored.example/x"\n', "utf-8")
    (root / "bot/web/static/replays_app/viewer.js").write_text(
        r"const ok = /^https:\/\/media\.valorant-api\.com\/[\w/.-]+$/i" + "\n", "utf-8")
    (root / "bot/web/static/replays_app/index.html").write_text(
        '<script src="https://cdn.example/x.js"></script>\n', "utf-8")

    code, report = _run(["scan", "--root", str(root)])
    assert code == 1 and report["ok"] is False
    urls = {(row["file"], row["url"].split("/")[2]) for row in report["violations"]}
    assert urls == {("bot/replay/evil.py", "telemetry.example"),
                    ("bot/web/static/replays_app/index.html", "cdn.example")}
    verdicts = {(row["file"], row["verdict"]) for row in report["findings"]}
    assert ("bot/replay/normalize.py", "allowlisted") in verdicts
    assert ("bot/replay/p2_review.py", "loopback") in verdicts
    assert ("bot/web/static/replays_app/viewer.js", "allowlisted") in verdicts
    assert all("test_evil.py" not in row["file"] for row in report["findings"])


def test_scan_accepts_clean_loopback_only_tree(tmp_path):
    root = tmp_path / "repo"
    (root / "bot/replay").mkdir(parents=True)
    (root / "bot/replay/p2_review.py").write_text(
        'BASE = "http://127.0.0.1:1234/v1"\nWS = "ws://localhost:18851/ws"\n', "utf-8")
    code, report = _run(["scan", "--root", str(root)])
    assert code == 0 and report["ok"] is True
    assert report["counts"]["violations"] == 0


def test_sample_sees_real_loopback_peer_and_passes(tmp_path):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.create_connection(server.getsockname(), timeout=5)
    try:
        report = oc.sample(os.getpid(), 0.6, 0.2, tree=False)
    finally:
        client.close()
        server.close()
    assert report["ok"] is True, report
    assert report["peers"], "the established loopback connection must be sampled"
    assert all(peer["loopback"] for peer in report["peers"])


def test_sample_flags_non_loopback_row(monkeypatch):
    text = (
        "  Proto  Local Address          Foreign Address        State           PID\n"
        f"  TCP    127.0.0.1:52001        192.0.2.1:9            SYN_SENT        {os.getpid()}\n"
    )
    monkeypatch.setattr(oc, "netstat_text", lambda: text)
    report = oc.sample(os.getpid(), 0.01, 0.01, tree=False)
    assert report["ok"] is False
    assert report["violations"][0]["remote"] == "192.0.2.1:9"
    assert report["violations"][0]["states"] == ["SYN_SENT"]
