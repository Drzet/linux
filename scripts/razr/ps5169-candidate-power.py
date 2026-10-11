#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Oneli candidate only: model both existing VCC/VIO PS5169 rails.

Runs after original attributed PS5169 v3 git-am in GitHub Actions. This is
an explicit proposal, not an assertion about reset polarity or sequencing.
Vendor source describes S10B for VCC and L8C for VIO.
"""
from pathlib import Path

src = Path("drivers/usb/typec/mux/ps5169.c")
s = src.read_text()

def once(old, new):
    global s
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"Expected one match, got {n}: {old!r}")
    s = s.replace(old, new, 1)

once("struct regulator *vcc;", "struct regulator_bulk_data supplies[2];")
start = s.index('\tps5169->vcc = devm_regulator_get(dev, "vcc");')
end = s.index("\n\tps5169->reset_gpio =", start)
s = s[:start] + (
    '\tps5169->supplies[0].supply = "vcc";\n'
    '\tps5169->supplies[1].supply = "vio";\n'
    '\tret = devm_regulator_bulk_get(dev, ARRAY_SIZE(ps5169->supplies),\n'
    '\t\t\t\t       ps5169->supplies);\n'
    '\tif (ret)\n'
    '\t\treturn dev_err_probe(dev, ret, "Failed to get vcc/vio supplies\\n");\n'
) + s[end:]
once("ret = regulator_enable(ps5169->vcc);",
     "ret = regulator_bulk_enable(ARRAY_SIZE(ps5169->supplies), ps5169->supplies);")
if s.count("regulator_disable(ps5169->vcc);") != 2:
    raise SystemExit("Unexpected cleanup paths")
s = s.replace("regulator_disable(ps5169->vcc);",
              "regulator_bulk_disable(ARRAY_SIZE(ps5169->supplies), ps5169->supplies);")
src.write_text(s)

binding = Path("Documentation/devicetree/bindings/usb/parade,ps5169.yaml")
b = binding.read_text()
needle = '  vcc-supply:\n    description: 1.8V power supply\n'
if b.count(needle) != 1:
    raise SystemExit("Unexpected upstream v3 VCC binding")
b = b.replace(needle, needle + '\n  vio-supply:\n    description: 1.8V I/O power supply\n', 1)
required = "required:\n  - compatible\n  - reg\n  - vcc-supply\n"
if b.count(required) != 1:
    raise SystemExit("Unexpected upstream v3 required properties")
b = b.replace(required, required + "  - vio-supply\n", 1)
binding.write_text(b)
