"""Generated secrets must survive an upgrade. `lookup` cannot run offline, so these
tests use a chart variant in which the one call that reads an existing Secret reads
from the test instead (see harness.chart_variant); the logic around it is the
production code."""

from __future__ import annotations

import base64

from harness import b64, chart_variant, check, render, section, unb64

FULL = "rel-molesignal"


def secret(name: str, **data: str) -> dict:
    return {name: {"data": {k: b64(v) for k, v in data.items()}}}


def run() -> None:
    section("secrets · generated on the first install, reused on every upgrade")
    with chart_variant(fake_lookup=True) as chart:
        fresh_a = render(chart=chart)
        fresh_b = render(chart=chart)
        check(fresh_a.returncode == 0, "renders with the lookup hook", fresh_a.stderr)
        core_a = fresh_a.get("Secret", f"{FULL}-secret")["data"]
        core_b = fresh_b.get("Secret", f"{FULL}-secret")["data"]
        check(
            core_a["cipher-key"] != core_b["cipher-key"] and core_a["cluster-token"] != core_b["cluster-token"],
            "a first install generates fresh random values each time",
        )
        check(
            len(base64.b64decode(unb64(core_a["cipher-key"]))) == 32,
            "the generated cipher key decodes to 32 bytes",
        )

        existing = {
            **secret(f"{FULL}-secret", **{"cipher-key": "EXISTING-CIPHER", "cluster-token": "existing-token"}),
            **secret(f"{FULL}-postgresql", password="existingpgpass"),
            **secret(f"{FULL}-rustfs", **{"access-key": "existingak", "secret-key": "existingsk"}),
        }
        upgraded = render({"__fakeSecrets": existing}, chart=chart)
        check(upgraded.returncode == 0, "renders with existing secrets", upgraded.stderr)
        got = upgraded.get("Secret", f"{FULL}-secret")["data"]
        check(
            got == existing[f"{FULL}-secret"]["data"],
            "an upgrade keeps the cipher key and the cluster token",
            f"got {({k: unb64(v) for k, v in got.items()})}",
        )
        check(
            upgraded.get("Secret", f"{FULL}-postgresql")["data"] == existing[f"{FULL}-postgresql"]["data"],
            "an upgrade keeps the generated database password",
        )
        check(
            upgraded.get("Secret", f"{FULL}-rustfs")["data"] == existing[f"{FULL}-rustfs"]["data"],
            "an upgrade keeps the generated RustFS credentials",
        )

        cipher = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
        explicit = render(
            {
                "__fakeSecrets": existing,
                "secrets": {"cipherKey": cipher, "clusterToken": "chosen-token"},
                "postgresql": {"auth": {"password": "chosenpass"}},
            },
            chart=chart,
        )
        check(explicit.returncode == 0, "renders with explicit values", explicit.stderr)
        got = explicit.get("Secret", f"{FULL}-secret")["data"]
        check(
            unb64(got["cipher-key"]) == cipher and unb64(got["cluster-token"]) == "chosen-token",
            "a value you set wins over what is in the cluster",
        )
        check(
            unb64(explicit.get("Secret", f"{FULL}-postgresql")["data"]["password"]) == "chosenpass",
            "so does the database password",
        )

        partial = render(
            {"__fakeSecrets": secret(f"{FULL}-secret", **{"cipher-key": "ONLY-CIPHER"})},
            chart=chart,
        )
        got = partial.get("Secret", f"{FULL}-secret")["data"]
        check(
            unb64(got["cipher-key"]) == "ONLY-CIPHER" and len(unb64(got["cluster-token"])) == 48,
            "a key missing from the existing secret is generated, the others are kept",
        )

        pinned = render({"secrets": {"jwtSecretOverride": base64.b64encode(b"j" * 40).decode()}}, chart=chart)
        check(
            "jwt-secret-override" in pinned.get("Secret", f"{FULL}-secret")["data"],
            "the optional JWT override is stored when set",
        )

    section("secrets · values the chart refuses")
    from harness import refuses

    refuses(
        "a cipher key that is not 32 bytes",
        "32 random bytes",
        sets=["secrets.cipherKey=dG9vLXNob3J0"],
    )
    refuses(
        "a cipher key that is not base64",
        "32 random bytes",
        sets=["secrets.cipherKey=!!!not-base64!!!"],
    )


SCENARIOS = [run]
