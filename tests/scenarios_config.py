"""config.toml: the TOML writer, the merge over the chart's own keys, port overrides."""

from __future__ import annotations

import re

from harness import check, container, env_of, integrity, render, section

FULL = "rel-molesignal"


def same(a, b) -> bool:
    """Equality that tells 1 from 1.0 (Python's == does not)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b




def toml_fidelity() -> None:
    section("config · TOML written from values keeps its types")
    user = {
        "auth": {"session_ttl_secs": 86400},
        "cache": {"object": {"enabled": True, "max_size": 10737418240, "root": "/var/cache/objects"}},
        "querier": {"max_scan_rows": 100000000, "ratio": 0.5},
        "compactor": {"interval_secs": 600},
        "notify": {"smtp": {"host": "smtp.example.com", "port": 587, "starttls": False}},
        "node": {"roles_hint": ["a", "b"], "label": 'say "hi" <b>&</b> 你好\\n'},
        "telemetry": {"trace": {"slow_thresholds": {"object_store_ms": 250}}},
        "weird": {"key with spaces": 1, "dotted.key": "x"},
    }
    m = render({"config": user})
    check(m.returncode == 0, "renders", m.stderr)
    cfg = m.config()
    # `telemetry` also carries the keys the chart derives (log format and level).
    derived = {"telemetry": {"log_format": "json", "log_level": "info,tantivy=warn"}}
    for table, given in user.items():
        expected = {**derived.get(table, {}), **given}
        check(same(cfg[table], expected), f"[{table}] round-trips with the same types", f"{cfg[table]!r} vs {expected!r}")
    text = m.config_text()
    check(
        not re.search(r"= -?\d+\.0\b", text) and "e+" not in text,
        "whole numbers are written as integers, never 86400.0 or 1e+08",
        text,
    )
    check("max_size = 10737418240\n" in text, "a number above 32 bits is written in full")

    ints = render(sets=["config.compactor.interval_secs=7", "config.querier.max_scan_rows=200000000"])
    check(
        same(ints.config()["compactor"], {"interval_secs": 7}) and ints.config()["querier"]["max_scan_rows"] == 200000000,
        "numbers given with --set come out as integers too",
    )

    tables = render({"config": {"compactor": {"nested": {"deep": {"leaf": 1}}}, "only": {"sub": {"x": True}}}})
    check(
        same(tables.config()["compactor"], {"nested": {"deep": {"leaf": 1}}}) and same(tables.config()["only"], {"sub": {"x": True}}),
        "tables that hold only sub-tables need no header of their own",
    )

    bad = render({"config": {"a": {"b": [{"c": 1}]}}})
    check(bad.returncode != 0 and "arrays of tables" in bad.stderr, "arrays of tables are refused with a way out", bad.stderr[:200])
    bad = render({"config": {"a": [[1, 2]]}})
    check(bad.returncode != 0 and "arrays of tables or nested arrays" in bad.stderr, "nested arrays are refused too")


def merge_rules() -> None:
    section("config · what you set wins over what the chart derives")
    m = render({"config": {"wal": {"dir": "/mnt/wal"}, "telemetry": {"log_format": "text"}, "http": {"gzip": False}}})
    cfg = m.config()
    check(cfg["wal"] == {"dir": "/mnt/wal"}, "your wal.dir replaces the chart's")
    check(cfg["telemetry"] == {"log_format": "text", "log_level": "info,tantivy=warn"}, "tables merge key by key")
    check(cfg["http"] == {"gzip": False}, "false is kept; it does not fall back to the default")
    check(cfg["object_store"]["bucket"] == "molesignal", "keys you did not touch stay")

    removed = render({"config": {"telemetry": {"log_level": None}, "scheduled_reports": None}})
    check(removed.returncode == 0, "null values render", removed.stderr)
    cfg = removed.config()
    check("log_level" not in cfg["telemetry"] and "scheduled_reports" not in cfg, "null removes a key or a whole table")

    check(
        render(sets=["logging.format=text", "logging.level=debug"]).config()["telemetry"]
        == {"log_format": "text", "log_level": "debug"},
        "logging.* feeds telemetry.*",
    )

    ext = render(sets=["externalUrl=https://obs.example.com"]).config()
    check(ext["http"] == {"external_url": "https://obs.example.com"}, "externalUrl becomes http.external_url")
    ing = render(
        {"ingress": {"enabled": True, "hosts": [{"host": "obs.example.com", "paths": [{"path": "/"}]}], "tls": [{"secretName": "x", "hosts": ["obs.example.com"]}]}}
    ).config()
    check(ing["http"] == {"external_url": "https://obs.example.com"}, "the ingress host (with TLS) becomes the external URL")
    plain = render(
        {"ingress": {"enabled": True, "hosts": [{"host": "obs.local", "paths": [{"path": "/"}]}]}}
    ).config()
    check(plain["http"] == {"external_url": "http://obs.local"}, "without TLS it is http")
    check("http" not in render().config(), "no external URL unless known")

    web = render({"mode": "distributed", "web": {"enabled": True, "image": {"repository": "example.com/web", "tag": "1"}}})
    check(web.returncode == 0, "web layer renders", web.stderr)
    check(
        web.config()["scheduled_reports"]["renderer"]["base_url"] == f"http://{FULL}-web:80",
        "reports render through the web layer when it exists",
    )
    integrity(web, "distributed + web")


def port_overrides() -> None:
    section("config · port overrides reach every place that names a port")
    m = render(
        {
            "mode": "distributed",
            "config": {"http": {"port": 8080}, "grpc": {"port": 9082}, "otlp_grpc": {"port": 14317}, "probe": {"register_port": 6084, "control_port": 6085}},
        }
    )
    check(m.returncode == 0, "renders", m.stderr)
    integrity(m, "port overrides")

    def ports(kind: str, role: str) -> dict:
        main = container(m.get(kind, f"{FULL}-{role}"), "molesignal")
        return {p["name"]: p["containerPort"] for p in main["ports"]}

    check(ports("Deployment", "router") == {"http": 8080, "otlp-grpc": 14317}, "router container ports")
    check(ports("Deployment", "alert") == {"http": 8080, "probe-register": 6084, "probe-control": 6085}, "alert container ports")
    router = m.get("Service", f"{FULL}-router")
    check({p["name"]: p["port"] for p in router["spec"]["ports"]} == {"http": 8080, "otlp-grpc": 14317}, "router Service ports")
    env = env_of(container(m.get("Deployment", f"{FULL}-querier"), "molesignal"))
    check(env["MS_CLUSTER_ADVERTISE_ADDR"]["value"] == "$(POD_IP):9082", "advertise address uses the gRPC port")
    check(
        m.config()["scheduled_reports"]["renderer"]["base_url"] == f"http://{FULL}-router:8080",
        "report rendering uses the HTTP port",
    )
    probe = container(m.get("Deployment", f"{FULL}-router"), "molesignal")["readinessProbe"]
    check(probe["httpGet"]["port"] == "http", "probes use the named port, so they follow along")


SCENARIOS = [toml_fidelity, merge_rules, port_overrides]
