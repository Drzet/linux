#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Validate actual v5-derived Iris DT schemas and enabled fixture copies.

The shipped VPU2 fixture stays disabled; schema checks intentionally enable
copies, because dt-validate normally skips disabled device instances.
"""
import pathlib
import re
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[3]
out = pathlib.Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
source = (root / "tools/testing/iris-upstream/sm8475-vpu2-disabled.dts").read_text()
bindings = root / "Documentation/devicetree/bindings/media"
paths = [bindings / x for x in (
    "qcom,venus-common.yaml", "qcom,sc7180-venus.yaml", "qcom,sm8475-iris.yaml")]

def run(args):
    return subprocess.run(args, capture_output=True, text=True)

def require_valid(args, name):
    proc = run(args)
    (out / (name + ".log")).write_text(proc.stdout + proc.stderr)
    if proc.returncode:
        raise AssertionError(name + ":\n" + proc.stdout + proc.stderr)

for i, path in enumerate(paths):
    require_valid(["dt-doc-validate", str(path)], "doc-" + str(i))
require_valid(["dt-mk-schema", "-j", "-o", str(out / "schema.json"),
               *map(str, paths)], "compile-schema")

pin = "2b5acf108ae37040ac1e652e029bb37f5609e886"
def pinned(path):
    return subprocess.check_output(
        ["git", "show", pin + ":" + path], cwd=root, text=True)
base = pinned("arch/arm64/boot/dts/qcom/sm8450.dtsi")
start = base.index("\tsoc: soc@0 {")
for line in base[start:].splitlines()[1:7]:
    assert line.strip() in source, "fixture bus differs: " + line
assert '#include "sm8450.dtsi"' in pinned(
    "arch/arm64/boot/dts/qcom/sm8475.dtsi")
assert '#include "sm8475.dtsi"' in pinned(
    "arch/arm64/boot/dts/qcom/sm8475-motorola-oneli.dts")

def body(text, child):
    match = re.search(r"(?ms)^\t\t\t" + re.escape(child) +
                      r" \{\n(.*?)^\t\t\t\};", text)
    assert match, "missing child: " + child
    return match.group(1)

def range_contract(text):
    # Fixture-only test of source-evidenced intended ranges, NOT IOMMU runtime.
    expected = {"non-pixel": (0x2180, 0x400, 0x25800000, 0xba800000),
                "pixel": (0x2187, 0x400, 0x00100000, 0xdff00000)}
    assert "dma-ranges = <0 0 0 0xe0000000>;" in text
    for child, expected_values in expected.items():
        block = body(text, child)
        iommu = re.search(r"iommus = <&apps_smmu (0x[0-9a-f]+) (0x[0-9a-f]+)>;", block)
        iova = re.search(r"iommu-ranges = <(0x[0-9a-f]+) (0x[0-9a-f]+)>;", block)
        assert iommu and iova, child
        found = tuple(int(v, 16) for v in iommu.groups() + iova.groups())
        assert found == expected_values, (child, found)
        assert found[2] + found[3] == 0xe0000000, child

range_contract(source)
for mutation in ("0x2181 0x400", "0xba700000", "0x00000000 0xdff00000"):
    before, after = {
        "0x2181 0x400": ("0x2180 0x400", mutation),
        "0xba700000": ("0xba800000", mutation),
        "0x00000000 0xdff00000": ("0x00100000 0xdff00000", mutation),
    }[mutation]
    try:
        range_contract(source.replace(before, after, 1))
    except AssertionError:
        pass
    else:
        raise AssertionError("false-green range acceptance: " + mutation)

disabled = out / "disabled.dts"
disabled.write_text(source)
require_valid(["dtc", "-I", "dts", "-O", "dtb", "-o",
               str(out / "disabled.dtb"), str(disabled)], "disabled-dtc")
assert source.count('status = "disabled";') == 2
enabled = source.replace('status = "disabled";', 'status = "okay";')
assert enabled != source

def omit_child(text, name):
    pattern = r"(?ms)^\t\t\t" + re.escape(name) + r" \{\n.*?^\t\t\t\};\n"
    result, n = re.subn(pattern, "", text, count=1)
    assert n == 1, name
    return result

cases = [
    ("valid", enabled, None),
    ("missing-pixel", omit_child(enabled, "pixel"), "pixel"),
    ("missing-nonpixel", omit_child(enabled, "non-pixel"), "non-pixel"),
    ("missing-pixel-range",
     enabled.replace("iommu-ranges = <0x00100000 0xdff00000>;", "", 1), "iommu-ranges"),
    ("missing-nonpixel-range",
     enabled.replace("iommu-ranges = <0x25800000 0xba800000>;", "", 1), "iommu-ranges"),
    ("controller-iommus",
     enabled.replace("dma-coherent;", "dma-coherent;\n\t\t\tiommus = <&apps_smmu 0x2180 0x400>;", 1),
     "False schema does not allow"),
    ("missing-dma-ranges",
     enabled.replace("dma-ranges = <0 0 0 0xe0000000>;", "", 1), "dma-ranges"),
    ("secure-child",
     enabled.replace("non-pixel {", "secure-pixel { iommus = <&apps_smmu 0x2187 0x400>; };\n\t\t\tnon-pixel {", 1),
     "False schema does not allow"),
    ("unknown-pixel-property",
     enabled.replace("iommu-ranges = <0x00100000 0xdff00000>;",
                     "iommu-ranges = <0x00100000 0xdff00000>;\n\t\t\t\tunknown-test = <1>;", 1),
     "unknown-test"),
]
for name, dts, diagnostic_expected in cases:
    dts_path, dtb_path = out / (name + ".dts"), out / (name + ".dtb")
    dts_path.write_text(dts)
    require_valid(["dtc", "-I", "dts", "-O", "dtb", "-o", str(dtb_path),
                   str(dts_path)], name + "-dtc")
    proc = run(["dt-validate", "-s", str(out / "schema.json"),
                "-l", "qcom,sm8475-iris", str(dtb_path)])
    diag = proc.stdout + proc.stderr
    (out / (name + "-schema.log")).write_text(diag)
    if diagnostic_expected is None:
        assert proc.returncode == 0 and not diag.strip(), diag
    else:
        assert diagnostic_expected in diag and "qcom,sm8475-iris" in diag, (
            name, proc.returncode, diag)
        assert "Traceback" not in diag
    print("PASS", name, "accepted" if diagnostic_expected is None
          else "rejected: " + diagnostic_expected)

print("PASS Motorola reference ranges, malformed-bound negatives, "
      "accepted oneli SoC cells and upstream v5-derived binding")
