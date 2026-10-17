#!/usr/bin/env python3
"""Compile and execute actual PS5169 functions with faulting userspace shims.

This is a source-extraction host test, not a kernel KUnit or hardware test.
Each function body is extracted from the owned, committed driver before GCC
compilation; we do not maintain handwritten copies of the logic under test.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[2] / "drivers/usb/typec/mux/ps5169.c"
src = SOURCE.read_text()

def extract(name):
    match = re.search(r"static int\s+" + name + r"\s*\(", src)
    if not match:
        raise RuntimeError(f"missing {name} definition")
    start = match.start()
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i+1]
    raise RuntimeError("unbalanced function " + name)

start = src.index("/* Useful constants */")
end = src.index("static const struct regmap_config", start)
declarations = src[start:end]

functions = "\n\n".join(extract(name) for name in
    ("ps5169_write", "ps5169_set", "ps5169_switch_set",
     "ps5169_retimer_set", "ps5169_init"))
# These structural checks enforce probe's init failure/cleanup and LE conversion,
# even though the probe itself requires a live kernel device model.
for fragment in ("ret = ps5169_init(ps5169);", "typec_retimer_unregister(ps5169->retimer);",
                 "vcc_disable:", "regulator_bulk_disable(",
                 "le16_to_cpu(chip_id)", "le16_to_cpu(chip_revision)"):
    if fragment not in src:
        raise AssertionError("probe/identity cleanup regression: " + fragment)
if "static void ps5169_init" in src or "static void ps5169_write" in src:
    raise AssertionError("I/O status discarded by production helpers")

preamble = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <limits.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define BIT(x) (1U << (x))
#define U32_MAX UINT32_MAX
#define USB_TYPEC_DP_SID 0xff01
#define TYPEC_STATE_SAFE 0
#define TYPEC_STATE_USB 1
#define TYPEC_DP_STATE_C 2
#define TYPEC_DP_STATE_D 3
#define TYPEC_DP_STATE_E 4
enum typec_orientation { TYPEC_ORIENTATION_NONE, TYPEC_ORIENTATION_NORMAL, TYPEC_ORIENTATION_REVERSE };
struct mutex { int unused; };
struct gpio_desc { int unused; };
struct regulator_bulk_data { const char *supply; };
struct regmap { int dummy; };
struct typec_switch { int dummy; };
struct typec_mux { int dummy; };
struct typec_switch_dev { void *drvdata; };
struct typec_retimer { void *drvdata; };
struct typec_altmode { unsigned int svid; };
struct typec_mux_state { struct typec_altmode *alt; void *data; unsigned long mode; };
struct typec_retimer_state { struct typec_altmode *alt; void *data; unsigned long mode; };

static void usleep_range(unsigned int a, unsigned int b) { (void)a; (void)b; }
static int io_calls, fail_io_at, downstream_mux_calls, fail_mux_at;
static int downstream_sw_calls, fail_sw_at;
static int inject_io(void) {
    io_calls++;
    return io_calls == fail_io_at ? -EIO : 0;
}
static int regmap_write(struct regmap *m, unsigned int reg, unsigned int val) {
    (void)m; (void)reg; (void)val;
    return inject_io();
}
static int regmap_update_bits(struct regmap *m, unsigned int reg, unsigned int mask, unsigned int val) {
    (void)m; (void)reg; (void)mask; (void)val;
    return inject_io();
}
static int regmap_set_bits(struct regmap *m, unsigned int reg, unsigned int mask) {
    return regmap_update_bits(m,reg,mask,mask);
}
static int regmap_clear_bits(struct regmap *m, unsigned int reg, unsigned int mask) {
    return regmap_update_bits(m,reg,mask,0);
}
static int regmap_assign_bits(struct regmap *m, unsigned int reg, unsigned int mask, bool val) {
    return regmap_update_bits(m,reg,mask,val ? mask : 0);
}
static void mutex_lock(struct mutex *m) { (void)m; }
static void mutex_unlock(struct mutex *m) { (void)m; }
static void *typec_switch_get_drvdata(struct typec_switch_dev *sw) { return sw->drvdata; }
static void *typec_retimer_get_drvdata(struct typec_retimer *rt) { return rt->drvdata; }
static int typec_switch_set(struct typec_switch *sw, enum typec_orientation o) {
    (void)sw; (void)o;
    downstream_sw_calls++;
    return downstream_sw_calls == fail_sw_at ? -EIO : 0;
}
static int typec_mux_set(struct typec_mux *mux, struct typec_mux_state *state) {
    (void)mux; (void)state;
    downstream_mux_calls++;
    return downstream_mux_calls == fail_mux_at ? -EIO : 0;
}
static void reset_faults(void) {
    io_calls = fail_io_at = downstream_mux_calls = fail_mux_at = 0;
    downstream_sw_calls = fail_sw_at = 0;
}
"""

tests = r"""
int main(void)
{
    struct redriver ps = {0};
    struct typec_switch_dev sw = { .drvdata = &ps };
    struct typec_retimer rt = { .drvdata = &ps };
    struct typec_altmode dp = { .svid = USB_TYPEC_DP_SID };
    struct typec_altmode other = { .svid = 0xabcd };
    struct typec_retimer_state state = { .mode = TYPEC_STATE_USB };
    int i, operations;

    /* Inject each I2C error in the config/AUX/HPD/LFPS programming chain. */
    ps.mode = TYPEC_STATE_USB;
    ps.tune_lfps = true;
    reset_faults();
    assert(ps5169_set(&ps) == 0);
    operations = io_calls;
    assert(operations >= 5);
    for (i = 1; i <= operations; i++) {
        reset_faults();
        fail_io_at = i;
        assert(ps5169_set(&ps) == -EIO);
        fail_io_at = 0;
        assert(ps5169_set(&ps) == 0); /* Full retry after partial I2C write. */
    }
    printf("mode-path failures and retries: %d positions PASS\n", operations);

    /* Every EQ/gain initialisation I/O position must fail probe's init helper. */
    ps.dp_eq_level = 1;
    ps.usb_rx_eq_level = 1;
    ps.usb_tx_eq_level = 1;
    reset_faults();
    assert(ps5169_init(&ps) == 0);
    operations = io_calls;
    assert(operations >= 12);
    for (i = 1; i <= operations; i++) {
        reset_faults();
        fail_io_at = i;
        assert(ps5169_init(&ps) == -EIO);
        fail_io_at = 0;
        assert(ps5169_init(&ps) == 0);
    }
    printf("probe EQ/gain failures and retries: %d positions PASS\n", operations);

    /* Failing local mode writes must not poison cached state. */
    ps.mode = TYPEC_STATE_SAFE;
    ps.orientation = TYPEC_ORIENTATION_NORMAL;
    ps.svid = 0;
    ps.hw_valid = true;
    reset_faults();
    fail_io_at = 1;
    assert(ps5169_retimer_set(&rt, &state) == -EIO);
    assert(ps.mode == TYPEC_STATE_SAFE && ps.svid == 0 && !ps.hw_valid);
    assert(downstream_mux_calls == 0);
    fail_io_at = 0;
    assert(ps5169_retimer_set(&rt, &state) == 0);
    assert(ps.mode == TYPEC_STATE_USB && ps.hw_valid && downstream_mux_calls == 1);
    printf("failed retimer write, unchanged request retry PASS\n");

    /* Downstream mux failure must return error and leave a retryable cache. */
    state.mode = TYPEC_DP_STATE_C;
    state.alt = &dp;
    reset_faults();
    fail_mux_at = 1;
    assert(ps5169_retimer_set(&rt, &state) == -EIO);
    assert(ps.mode == TYPEC_STATE_USB && ps.svid == 0 && !ps.hw_valid);
    fail_mux_at = 0;
    assert(ps5169_retimer_set(&rt, &state) == 0);
    assert(ps.mode == TYPEC_DP_STATE_C && ps.svid == USB_TYPEC_DP_SID && ps.hw_valid);
    printf("downstream mux failure, hardware rollback and retry PASS\n");

    /* Same DP mode with changed SVID is NOT an unchanged transition. */
    state.alt = &other;
    reset_faults();
    assert(ps5169_retimer_set(&rt, &state) == -EINVAL);
    assert(ps.svid == USB_TYPEC_DP_SID && !ps.hw_valid);
    state.alt = &dp;
    assert(ps5169_retimer_set(&rt, &state) == 0);
    assert(ps.svid == USB_TYPEC_DP_SID && ps.hw_valid);
    printf("same mode, changed SVID rejection and retry PASS\n");

    /* Failed orientation I/O restores committed orientation and retries. */
    ps.orientation = TYPEC_ORIENTATION_NORMAL;
    ps.hw_valid = true;
    reset_faults();
    fail_io_at = 1;
    assert(ps5169_switch_set(&sw, TYPEC_ORIENTATION_REVERSE) == -EIO);
    assert(ps.orientation == TYPEC_ORIENTATION_NORMAL && !ps.hw_valid);
    assert(downstream_sw_calls == 2); /* new and rollback */
    fail_io_at = 0;
    assert(ps5169_switch_set(&sw, TYPEC_ORIENTATION_REVERSE) == 0);
    assert(ps.orientation == TYPEC_ORIENTATION_REVERSE && ps.hw_valid);
    printf("failed switch I/O, downstream rollback and retry PASS\n");

    /* Downstream switch failure must not change the local orientation. */
    reset_faults();
    fail_sw_at = 1;
    assert(ps5169_switch_set(&sw, TYPEC_ORIENTATION_NORMAL) == -EIO);
    assert(ps.orientation == TYPEC_ORIENTATION_REVERSE && ps.hw_valid);
    assert(io_calls == 0);
    printf("downstream switch failure PASS\n");
    puts("PS5169 extracted production-function fault injection: PASS");
    return 0;
}
"""

with tempfile.TemporaryDirectory(prefix="ps5169-fault-") as directory:
    work = Path(directory)
    c_file = work / "ps5169-fault.c"
    executable = work / "ps5169-fault"
    c_file.write_text(preamble + "\n" + declarations + "\n" + functions + "\n" + tests)
    compile_args = ["gcc", "-std=gnu11", "-O0", "-g", "-Wall", "-Wextra",
                    "-Werror", "-Wno-unused-const-variable", str(c_file), "-o", str(executable)]
    print("Compiling extracted production driver functions with injected regmap/downstream callbacks", flush=True)
    subprocess.run(compile_args, check=True)
    subprocess.run([str(executable)], check=True)
