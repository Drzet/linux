#!/usr/bin/env python3
"""Exercise the real oneli PS5169 binding branch and probe override rejection.

Kernel Image/modules compilation is intentionally NOT run here: the prior
source has already passed full ARM64 build. This test executes dt-mk-schema,
dtc, fdtget and dt-validate against compiled positive/negative DTB fixtures,
and runs the *actual production* ps5169_probe() extracted by the existing
fifth-round host harness with extra property-presence fault injection.

dt-validate may emit validation errors while returning success, therefore
exit codes alone are NEVER considered proof that a negative fixture failed.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
work = root / "ps5169-conditional-record"
work.mkdir(exist_ok=True)
schema = root / "Documentation/devicetree/bindings/usb/parade,ps5169.yaml"
fixture = root / "tests/usb/ps5169/oneli-valid.dts"
probe_harness = root / "scripts/razr/ps5169-probe-fault-injection.py"
base = fixture.read_text()
out_schema = work / "processed-schema.json"

# Each negative fixture starts from the SAME positive baseline and violates
# exactly one prohibited property, so a failure elsewhere cannot pass silently.
overrides = {
    "dp-eq": "parade,dp-eq-db = <2 0>;",
    "usb-rx-eq": "parade,usb-rx-eq-db = <5 2>;",
    "usb-tx-eq": "parade,usb-tx-eq-db = <2 0>;",
    "dp-gain": "parade,dp-set-gain;",
    "usb-rx-gain": "parade,usb-rx-set-gain;",
    "usb-tx-gain": "parade,usb-tx-set-gain;",
    "lfps": "parade,fine-tune-lfps-swing;",
}
expected = tuple(item.split(" ")[0].rstrip(";") for item in overrides.values())
assert len(overrides) == 7 and len(set(expected)) == 7
assert 'compatible = "motorola,oneli-ps5169", "parade,ps5169";' in base
assert "enable-gpios = <&tlmm 19 0>;" in base
assert "reset-gpios" not in base
assert all(item not in base for item in overrides.values())
anchor = "\t\t\tretimer-switch;\n"
assert base.count(anchor) == 1

def execute(argv):
    p = subprocess.run(argv, cwd=root, text=True, capture_output=True)
    cmd = " ".join(str(a) for a in argv)
    return p, f"$ {cmd}\nreturncode={p.returncode}\n{p.stdout}{p.stderr}\n"

def require(condition, detail):
    if not condition:
        raise AssertionError(detail)

print("Building processed DT schema with the real Parade binding and referenced USB switch schemas", flush=True)
cmd = [
    "dt-mk-schema", "-j",
    str(schema),
    str(root / "Documentation/devicetree/bindings/usb/usb-switch.yaml"),
    str(root / "Documentation/devicetree/bindings/usb/usb-switch-ports.yaml"),
]
p, log = execute(cmd)
(work / "schema-compile.log").write_text(log)
require(p.returncode == 0, "dt-mk-schema failed:\n" + log)
require(p.stdout.strip().startswith("{"), "dt-mk-schema did not emit processed JSON")
out_schema.write_text(p.stdout)

results = {}
cases = {"valid": base}
for name, property_line in overrides.items():
    cases["invalid-" + name] = base.replace(anchor,
        anchor + "\t\t\t" + property_line + "\n")
# Test GPIO mutually-exclusive binding as a separate negative control.
cases["invalid-both-gpios"] = base.replace(anchor,
    anchor + "\t\t\treset-gpios = <&tlmm 19 0>;\n")

for case, dts in cases.items():
    dts_path = work / (case + ".dts")
    dtb = work / (case + ".dtb")
    dts_path.write_text(dts)
    p, log = execute(["dtc", "-I", "dts", "-O", "dtb", "-o", str(dtb), str(dts_path)])
    (work / (case + ".dtc.log")).write_text(log)
    require(p.returncode == 0, "DT compilation failed for " + case + "\n" + log)

    # Ensure actual DTB retains phandle, pin=19 and active-high flags=0.
    p, gpio_log = execute(["fdtget", "-t", "x", str(dtb),
                           "/i2c@0/retimer@30", "enable-gpios"])
    require(p.returncode == 0, "missing actual enable-gpios: " + gpio_log)
    cells = [int(x, 16) for x in p.stdout.strip().split()]
    require(len(cells) == 3 and cells[0] != 0 and cells[1:] == [19, 0],
            f"{case}: incorrect GPIO19 active-high DTB cells: {cells}")

    # -l limits *applied schemas*; the test uses the compiled oneli compatible
    # pair and the actual Linux binding, rather than a hand-reimplemented rule.
    p, log = execute(["dt-validate", "-s", str(out_schema),
                      "-l", "parade,ps5169", str(dtb)])
    (work / (case + ".validate.log")).write_text(log)
    validation = p.stdout + p.stderr
    if case == "valid":
        require(p.returncode == 0 and not validation.strip(),
                "valid oneli fixture rejected:\n" + log)
        print("PASS valid oneli-compatible PS5169, GPIO19 active-high, no EQ/gain/LFPS", flush=True)
    else:
        require("retimer@30" in validation and "parade,ps5169" in validation,
                f"{case} did not produce a Parade retimer schema diagnostic:\n{log}")
        if case == "invalid-both-gpios":
            require("reset-gpios" in validation or "oneOf" in validation,
                    "both GPIO fixture lacks expected mutually-exclusive diagnostics:\n" + log)
        else:
            # dt-validate errors have varied exit-code semantics across
            # releases: require an actual failure message, not just nonzero.
            require("should not be valid under" in validation or
                    "is not valid under" in validation or
                    "not valid under" in validation,
                    f"{case} did not fail conditional 'not:anyOf' schema:\n{log}")
            require(expected[list(overrides).index(case.removeprefix("invalid-"))] in dts,
                    f"{case}: intended prohibited property not inserted")
        print(f"PASS {case}: actual binding rejected fixture ({p.returncode=})", flush=True)
    results[case] = {
        "expected_valid": case == "valid",
        "diagnostics_bytes": len(validation),
        "validator_exit": p.returncode,
        "dtb_sha256": hashlib.sha256(dtb.read_bytes()).hexdigest(),
        "enable_gpio_cells": cells,
    }

# Extend the existing, already passing production-probe extraction harness
# in memory (not a copy of the driver, and not an alteration to the old test).
# The only stub change simulates presence of one *real named property* at a time.
original = probe_harness.read_text()
old = "static int raw_reads, source_oneli = 1;"
require(original.count(old) == 1, "probe harness changed: override stub marker missing")
new = old + "\nstatic const char *mock_override_property;"
updated = original.replace(old, new)
old_reset = "    source_oneli = 1;\n}"
require(updated.count(old_reset) == 1, "probe reset marker missing")
updated = updated.replace(old_reset, "    source_oneli = 1;\n    mock_override_property = NULL;\n}")
old_prop = """static bool device_property_present(const struct device *d, const char *s)
{ (void)d; (void)s; return false; }"""
require(updated.count(old_prop) == 1, "probe property lookup stub missing")
updated = updated.replace(old_prop, """static bool device_property_present(const struct device *d, const char *s)
{ (void)d; return mock_override_property && !strcmp(s, mock_override_property); }""")
marker = '    puts("PS5169 production lifecycle fault injection PASS (host mocks, not kernel/hardware)");'
require(updated.count(marker) == 1, "probe tests insertion marker missing")
extra = r'''
    {
        const char *prohibited[] = {
            "parade,dp-eq-db", "parade,usb-rx-eq-db",
            "parade,usb-tx-eq-db", "parade,dp-set-gain",
            "parade,usb-rx-set-gain", "parade,usb-tx-set-gain",
            "parade,fine-tune-lfps-swing"
        };
        unsigned int n;
        for (n = 0; n < ARRAY_SIZE(prohibited); n++) {
            reset_fake();
            client.data = NULL;
            mock_override_property = prohibited[n];
            assert(ps5169_probe(&client) == -EINVAL);
            assert_clean(NO_FAULT);
            assert(raw_reads == 2 && io_writes == 0);
            assert(sw_registered == 0 && rt_registered == 0);
            mock_override_property = NULL;
            assert(ps5169_probe(&client) == 0);
            ps5169_remove(&client);
            assert(!powered && !fake_enable.last);
            assert(sw_registered == sw_unregistered);
            assert(rt_registered == rt_unregistered);
        }
        puts("production ps5169_probe: all seven forbidden OEM properties rejected before I2C writes and reprobe PASS");
    }
'''
updated = updated.replace(marker, extra + "\n" + marker)
# Replace the harness' root lookup because this generated script is kept in
# a validation artifact directory instead of scripts/razr in the source tree.
old_root = 'root = Path(__file__).resolve().parents[2]'
require(updated.count(old_root) == 1, "probe root lookup missing")
updated = updated.replace(old_root, "root = Path(" + repr(str(root)) + ")")
generated = work / "production-probe-override-injection.py"
generated.write_text(updated)
p, log = execute([sys.executable, str(generated)])
(work / "runtime-override.log").write_text(log)
require(p.returncode == 0 and
        "all seven forbidden OEM properties rejected" in p.stdout,
        "actual production probe override rejection failed:\n" + log)
print("PASS actual production ps5169_probe: seven generic tuning property rejections before register writes, clean reprobe", flush=True)

results["runtime"] = {
    "probe_harness_sha256": hashlib.sha256(probe_harness.read_bytes()).hexdigest(),
    "generated_host_harness_sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
    "returncode": p.returncode,
    "rejected_properties": list(expected),
}
results["provenance"] = {
    "binding_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
    "valid_fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
    "driver_sha256": hashlib.sha256(
        (root / "drivers/usb/typec/mux/ps5169.c").read_bytes()).hexdigest(),
}
(work / "results.json").write_text(json.dumps(results, indent=2) + "\n")
print("PS5169 conditional schema + production runtime test: ALL PASS", flush=True)
