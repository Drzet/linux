#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile and execute the upstream Iris routing function, not a rewrite."""
import pathlib
import re
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[3]
out = pathlib.Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
iris = root / "drivers/media/platform/qcom/iris"
enums = (iris / "iris_buffer.h").read_text()
routing = (iris / "iris_resources.c").read_text()
enum = re.search(r"enum iris_buffer_type \{[^}]+\};", enums, re.S)
assert enum, "production buffer-type enum absent"
start = routing.index("struct device *iris_get_cb_dev(")
brace = routing.index("{", start)
depth = 1
end = brace + 1
while depth:
    depth += (routing[end] == "{") - (routing[end] == "}")
    end += 1
function = routing[start:end]
assert function.count("case BUF_") == 13
(out / "production-routing.h").write_text(enum.group() + "\n" + function + "\n")

queue = (iris / "iris_hfi_queue.c").read_text()
internal = (iris / "iris_buffer.c").read_text()
vb2 = (iris / "iris_vidc.c").read_text()
assert queue.count("struct device *dev = core->np_dev ? core->np_dev : core->dev;") == 2
assert "core->iface_q_table_vaddr = dma_alloc_attrs(dev," in queue
assert "core->sfr_vaddr = dma_alloc_attrs(dev," in queue
assert "dma_free_attrs(dev, SFR_SIZE," in queue
assert "dma_free_attrs(dev, queue_size," in queue
assert "struct device *dev = iris_get_cb_dev(inst, buffer_type);" in internal
assert "struct device *dev = iris_get_cb_dev(inst, buffer->type);" in internal
assert "dma_alloc_attrs(dev, buffer->buffer_size," in internal
assert "dma_free_attrs(dev, buffer->buffer_size," in internal
assert "src_vq->dev = iris_get_cb_dev(inst, BUF_INPUT);" in vb2
assert "dst_vq->dev = iris_get_cb_dev(inst, BUF_OUTPUT);" in vb2
probe = (iris / "iris_probe.c").read_text()
assert 'if (of_device_is_compatible(dev->of_node, "qcom,sm8475-iris"))' in probe
assert "return dev_err_probe(dev, -EOPNOTSUPP," in probe
assert "platform_device_register_full(&plat_dev_info)" in probe
assert probe.count("platform_device_unregister(to_platform_device(") >= 3

harness = root / "tools/testing/iris-upstream/routing-harness.c"
executable = out / "routing-harness"
cmd = ["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
       "-fsanitize=undefined", "-g", "-I", str(out), str(harness),
       "-o", str(executable)]
subprocess.run(cmd, check=True)
proc = subprocess.run([str(executable)], text=True, capture_output=True)
(out / "routing.log").write_text(proc.stdout + proc.stderr)
assert proc.returncode == 0, proc.stdout + proc.stderr
print(proc.stdout.strip())
print("PASS producer HFI/internal allocation-free symmetry, VB2 routing, "
      "common child lifecycle hooks and SM8475 fail-closed gate (source checks)")
