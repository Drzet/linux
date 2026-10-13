#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile production Iris lifecycle and OF generic DMA translation with shims."""
import pathlib
import re
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[3]
out = pathlib.Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)

def function(source, name):
    # Function names are pinned; fail if the definition is missing/ambiguous.
    pattern = r"(?:^|\n)([^\n;{}]*\b" + name + r"\([^;{}]*?\)\n\{)"
    found = list(re.finditer(pattern, source))
    assert len(found) == 1, name
    start = found[0].start(1)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'

address = (root / 'drivers/of/address.c').read_text()
functions = ['of_bus_default_count_cells', 'of_bus_default_map',
             'of_bus_default_translate', 'of_translate_one',
             '__of_translate_address', '__of_get_dma_parent',
             'of_translate_dma_address', 'of_translate_dma_region']
bus = re.search(r'struct of_bus \{.*?\n\};', address, re.S).group()
first = '\n'.join(function(address, n) for n in functions[:3])
rest = '\n'.join(function(address, n) for n in functions[3:])
shim = '''
static const struct of_bus generic_bus = {
 .count_cells = of_bus_default_count_cells, .map = of_bus_default_map,
 .translate = of_bus_default_translate,
};
static const struct of_bus *of_match_bus(struct device_node *n) { return &generic_bus; }
static int of_empty_ranges_quirk(const struct device_node *n) { return 0; }
'''
(out / 'of-production.h').write_text(bus + '\n' + first + shim + rest)
source = (root / 'drivers/media/platform/qcom/iris/iris_dma_context.c').read_text()
(out / 'iris-production.h').write_text(re.sub(r'^#include.*\n', '', source, flags=re.M))
lib = root / 'scripts/dtc/libfdt'
subprocess.run(['gcc', '-std=gnu11', '-g', '-O1', '-Wall', '-Wextra',
                '-Wno-unused-parameter', '-Wno-sign-compare', '-Werror',
                '-fsanitize=undefined', '-I' + str(out), '-I' + str(lib),
                '-I' + str(root / 'drivers/media/platform/qcom/iris'),
                str(root / 'tools/testing/iris/host-test.c'),
                *map(str, sorted(lib.glob('*.c'))), '-o', str(out / 'host-test')], check=True)
subprocess.run([str(out / 'host-test'), str(out / 'valid.dtb')], check=True)
