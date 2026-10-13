#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile DTs, run real schema validation, require diagnostics for each negative."""
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[3]
out = pathlib.Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
bindings = root / 'Documentation/devicetree/bindings/media'
schemas = [bindings / 'qcom,venus-common.yaml', bindings / 'qcom,sm8475-iris.yaml',
           bindings / 'qcom,sm8475-iris-dma.yaml']
subprocess.run(['dt-doc-validate', *map(str, schemas)], check=True)
subprocess.run(['dt-mk-schema', '-j', '-o', str(out / 'schemas.json'), *map(str, schemas)], check=True)
source = (root / 'tools/testing/iris/sm8475-dma-disabled.dts').read_text()
# Ensure the test really uses the pinned integration bus, not the old 1-cell guess.
pin = '2b5acf108ae37040ac1e652e029bb37f5609e886'
base = subprocess.check_output(['git', 'show', pin + ':arch/arm64/boot/dts/qcom/sm8450.dtsi'], cwd=root, text=True)
start = base.index('\tsoc: soc@0 {')
for line in base[start:].splitlines()[1:7]:
    assert line.strip() in source, line
board = subprocess.check_output(['git', 'show', pin + ':arch/arm64/boot/dts/qcom/sm8475-motorola-oneli.dts'], cwd=root, text=True)
assert '#include "sm8475.dtsi"' in board
soc = subprocess.check_output(['git', 'show', pin + ':arch/arm64/boot/dts/qcom/sm8475.dtsi'], cwd=root, text=True)
assert '#include "sm8450.dtsi"' in soc

cases = [
 ('valid', source, None),
 ('missing-iommu', source.replace('iommus = <&apps_smmu 0x2180 0x400>;', ''), 'iommus'),
 ('extra-stream', source.replace('iommus = <&apps_smmu 0x2180 0x400>;',
   'iommus = <&apps_smmu 0x2180 0x400>, <&apps_smmu 0x2181 0x400>;'), 'iommus'),
 ('missing-upper', source.replace('memory-region = <&ns_low>, <&ns_high>;',
   'memory-region = <&ns_low>;'), 'memory-region'),
 ('missing-consumers', source.replace('qcom,dma-consumers = <&nonpixel>, <&pixel>;', ''), 'qcom,dma-consumers'),
 ('single-consumer', source.replace('qcom,dma-consumers = <&nonpixel>, <&pixel>;',
   'qcom,dma-consumers = <&nonpixel>;'), 'qcom,dma-consumers'),
 ('controller-stream', source.replace('qcom,dma-consumers = <&nonpixel>, <&pixel>;',
   'qcom,dma-consumers = <&nonpixel>, <&pixel>; iommus = <&apps_smmu 0x2180 0x400>;'), 'iommus'),
 ('secure-property', source.replace('compatible = "qcom,sm8475-iris-nonpixel";',
   'compatible = "qcom,sm8475-iris-nonpixel"; qcom,secure-context;'), 'qcom,secure-context'),
]
for name, text, expected in cases:
    dts, dtb = out / (name + '.dts'), out / (name + '.dtb')
    dts.write_text(text)
    r = subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', str(dtb), str(dts)], text=True, capture_output=True)
    (out / (name + '-dtc.log')).write_text(r.stdout + r.stderr)
    r.check_returncode()
    r = subprocess.run(['dt-validate', '-s', str(out / 'schemas.json'), '-l', 'qcom,sm8475-iris', str(dtb)], text=True, capture_output=True)
    diagnostic = r.stdout + r.stderr
    (out / (name + '-schema.log')).write_text(diagnostic)
    # Some dt-validate releases return 0 even for schema errors. Inspect output.
    if expected is None:
        assert r.returncode == 0 and not diagnostic.strip(), diagnostic
    else:
        assert expected in diagnostic and 'qcom,sm8475-iris' in diagnostic, diagnostic
        assert 'Traceback' not in diagnostic, diagnostic
    print('PASS:', name, 'accepted' if expected is None else 'expected schema rejection: ' + expected)
print('PASS: exact integration bus hierarchy/cells/ranges dependency verified:', pin)
