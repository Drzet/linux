#!/usr/bin/env python3
"""Execute actual PS5169 probe/remove C bodies under deterministic fault injection.

The production driver is read at its pinned checkout revision and its actual
functions and initialization data are compiled into a host harness with fake
kernel services. This exercises ordering/error unwinding without asserting
electrical or devres/driver-model validation. No handwritten probe clone.
"""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "drivers/usb/typec/mux/ps5169.c").read_text()


def extract(name, rettype):
    match = re.search(r"static " + rettype + r"\s+" + name + r"\s*\(", source)
    if not match:
        raise RuntimeError("missing actual production function: " + name)
    start = match.start()
    open_brace = source.index("{", start)
    level = 0
    for pos in range(open_brace, len(source)):
        if source[pos] == "{":
            level += 1
        elif source[pos] == "}":
            level -= 1
            if not level:
                return source[start:pos+1]
    raise RuntimeError("unbalanced source: " + name)


data = source[source.index("/* Useful constants */"):
              source.index("static const struct regmap_config")]
functions = "\n\n".join(extract(name, typ) for name, typ in (
    ("ps5169_write", "int"),
    ("ps5169_set", "int"),
    ("ps5169_eq_from_dt", "int"),
    ("ps5169_init", "int"),
    ("ps5169_probe", "int"),
    ("ps5169_remove", "void"),
))
# The full mock intentionally calls the exact probe and remove, with no
# paraphrased lifecycle state machine.
stub = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <limits.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint16_t __le16;
typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define U32_MAX UINT32_MAX
#define GFP_KERNEL 0
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define ERR_PTR(i) ((void *)(intptr_t)(i))
#define TYPEC_STATE_SAFE 0
#define TYPEC_STATE_USB 1
#define TYPEC_DP_STATE_C 2
#define TYPEC_DP_STATE_D 3
#define TYPEC_DP_STATE_E 4
#define USB_TYPEC_DP_SID 0xff01
#define GPIOD_OUT_LOW 0
#define EPROBE_DEFER 517
#define le16_to_cpu(x) (x)
enum typec_orientation { TYPEC_ORIENTATION_NONE, TYPEC_ORIENTATION_NORMAL, TYPEC_ORIENTATION_REVERSE };
struct device_node { int ignored; };
struct device { struct device_node *of_node; };
struct i2c_client { struct device dev; void *data; };
struct gpio_desc { int last; };
struct regmap { int dummy; };
struct mutex { int dummy; };
struct regulator_bulk_data { const char *supply; };
struct typec_switch { int dummy; };
struct typec_mux { int dummy; };
struct typec_switch_dev { int dummy; };
struct typec_retimer { int dummy; };
struct typec_altmode { unsigned int svid; };
struct typec_switch_desc {
    void *drvdata;
    void *fwnode;
    int (*set)(struct typec_switch_dev *, enum typec_orientation);
};
struct typec_retimer_state { struct typec_altmode *alt; void *data; unsigned long mode; };
struct typec_retimer_desc {
    void *drvdata;
    void *fwnode;
    int (*set)(struct typec_retimer *, struct typec_retimer_state *);
};
struct ps5169_regmap_config { int dummy; };
static const struct ps5169_regmap_config ps5169_regmap = {0};

enum stage {
    NO_FAULT, FAIL_REG_GET, FAIL_GPIO_GET, FAIL_MAP_GET,
    FAIL_SWITCH_GET, FAIL_MUX_GET, FAIL_SUPPLY_ENABLE,
    FAIL_ID_READ, FAIL_REV_READ, FAIL_AUX_BRIDGE,
    FAIL_SWITCH_REGISTER, FAIL_RETIMER_REGISTER, FAIL_WRONG_ID
};
static enum stage fail_stage;
static int powered, switch_gets, switch_puts, mux_gets, mux_puts;
static int sw_registered, sw_unregistered, rt_registered, rt_unregistered;
static int sleep_10ms, gpio_high, gpio_low, io_writes, fail_write_at;
static int raw_reads, source_oneli = 1;
static struct gpio_desc fake_enable;
static struct regmap fake_regmap;
static struct typec_switch fake_switch;
static struct typec_mux fake_mux;
static struct typec_switch_dev fake_switch_dev;
static struct typec_retimer fake_retimer;
static struct redriver *last_redriver; /* set by i2c_set_clientdata */
static struct ps5169_reg_init observed_profile[27];
static int observed_count;
static unsigned int delay_after[27];

static void reset_fake(void)
{
    fail_stage = NO_FAULT;
    powered = switch_gets = switch_puts = mux_gets = mux_puts = 0;
    sw_registered = sw_unregistered = rt_registered = rt_unregistered = 0;
    sleep_10ms = gpio_high = gpio_low = io_writes = fail_write_at = raw_reads = 0;
    observed_count = 0;
    memset(observed_profile, 0, sizeof(observed_profile));
    memset(delay_after, 0, sizeof(delay_after));
    last_redriver = NULL;
    fake_enable.last = 0;
    source_oneli = 1;
}

static void *devm_kzalloc(struct device *d, size_t n, int flags)
{ (void)d; (void)flags; return calloc(1,n); }
static int dev_err_probe(struct device *d, int ret, const char *fmt, ...)
{ (void)d; (void)fmt; return ret; }
#define dev_err(d,fmt,...) ((void)(d))
#define dev_warn(d,fmt,...) ((void)(d))
#define dev_dbg(d,fmt,...) ((void)(d))
static void mutex_init(struct mutex *m) { (void)m; }
static int devm_regulator_bulk_get(struct device *d, int n, struct regulator_bulk_data *b)
{ (void)d; (void)n; (void)b; return fail_stage == FAIL_REG_GET ? -EIO : 0; }
static int regulator_bulk_enable(int n, struct regulator_bulk_data *b)
{ (void)n; (void)b; if (fail_stage == FAIL_SUPPLY_ENABLE) return -EIO; powered=1; return 0; }
static int regulator_bulk_disable(int n, struct regulator_bulk_data *b)
{ (void)n; (void)b; assert(powered); powered=0; return 0; }
static struct gpio_desc *devm_gpiod_get_optional(struct device *d, const char *name, int flags)
{ (void)d; (void)name; (void)flags; return fail_stage == FAIL_GPIO_GET ? ERR_PTR(-EIO) : &fake_enable; }
static struct gpio_desc *devm_gpiod_get(struct device *d, const char *name, int flags)
{ (void)d; (void)name; (void)flags; return &fake_enable; }
static void gpiod_set_value_cansleep(struct gpio_desc *gpio, int v)
{ assert(gpio); gpio->last=v; if (v) gpio_high++; else gpio_low++; }
static void usleep_range(unsigned int lo, unsigned int hi)
{
    assert(hi >= lo);
    if (observed_count > 0 && observed_count <= 27)
        delay_after[observed_count-1] = lo;
}
static void msleep(unsigned int ms)
{ assert(ms == 10); sleep_10ms++; }
static struct regmap *devm_regmap_init_i2c(struct i2c_client *i, const void *c)
{ (void)i; (void)c; return fail_stage == FAIL_MAP_GET ? ERR_PTR(-EIO) : &fake_regmap; }
static struct typec_switch *typec_switch_get(struct device *d)
{ (void)d; if (fail_stage == FAIL_SWITCH_GET) return ERR_PTR(-EIO); switch_gets++; return &fake_switch; }
static struct typec_mux *typec_mux_get(struct device *d)
{ (void)d; if (fail_stage == FAIL_MUX_GET) return ERR_PTR(-EIO); mux_gets++; return &fake_mux; }
static void typec_switch_put(struct typec_switch *s)
{ (void)s; switch_puts++; }
static void typec_mux_put(struct typec_mux *m)
{ (void)m; mux_puts++; }
static int regmap_write(struct regmap *r, unsigned int reg, unsigned int val)
{
    (void)r;
    io_writes++;
    if (io_writes == fail_write_at) return -EIO;
    if (observed_count < 27) {
        observed_profile[observed_count].reg = reg;
        observed_profile[observed_count].value = val;
        observed_count++;
    }
    return 0;
}
static int regmap_update_bits(struct regmap *r, unsigned int reg, unsigned int mask, unsigned int val)
{ (void)reg; (void)mask; (void)val; return regmap_write(r, 0xff, 0xff); }
static int regmap_set_bits(struct regmap *r, unsigned int reg, unsigned int mask)
{ return regmap_update_bits(r, reg, mask, mask); }
static int regmap_clear_bits(struct regmap *r, unsigned int reg, unsigned int mask)
{ return regmap_update_bits(r, reg, mask, 0); }
static int regmap_assign_bits(struct regmap *r, unsigned int reg, unsigned int mask, bool val)
{ return regmap_update_bits(r, reg, mask, val ? mask : 0); }
static int regmap_raw_read(struct regmap *r, unsigned int reg, void *dest, size_t n)
{
    (void)r;
    assert(n == sizeof(__le16));
    raw_reads++;
    if ((raw_reads == 1 && fail_stage == FAIL_ID_READ) ||
        (raw_reads == 2 && fail_stage == FAIL_REV_READ)) return -EIO;
    *(uint16_t *)dest = (reg == PS5169_CHIP_ID_REG) ?
        (fail_stage == FAIL_WRONG_ID ? 0x0000 : PS5169_CHIP_ID_VAL) : 0x0010;
    return 0;
}
static bool of_device_is_compatible(struct device_node *n, const char *c)
{ (void)n; (void)c; return source_oneli; }
static bool device_property_present(const struct device *d, const char *s)
{ (void)d; (void)s; return false; }
static bool device_property_read_bool(const struct device *d, const char *s)
{ (void)d; (void)s; return false; }
static int device_property_read_u32_array(const struct device *d, const char *s, u32 *out, size_t n)
{ (void)d; (void)s; (void)out; (void)n; return -ENODATA; }
static int drm_aux_bridge_register(struct device *d)
{ (void)d; return fail_stage == FAIL_AUX_BRIDGE ? -EIO : 0; }
static void *dev_fwnode(struct device *d) { (void)d; return NULL; }
static int ps5169_switch_set(struct typec_switch_dev *d, enum typec_orientation o)
{ (void)d; (void)o; return 0; }
static int ps5169_retimer_set(struct typec_retimer *d, struct typec_retimer_state *s)
{ (void)d; (void)s; return 0; }
static struct typec_switch_dev *typec_switch_register(struct device *d, const struct typec_switch_desc *desc)
{
    (void)d; (void)desc;
    if (fail_stage == FAIL_SWITCH_REGISTER) return ERR_PTR(-EIO);
    sw_registered++; return &fake_switch_dev;
}
static struct typec_retimer *typec_retimer_register(struct device *d, const struct typec_retimer_desc *desc)
{
    (void)d; (void)desc;
    if (fail_stage == FAIL_RETIMER_REGISTER) return ERR_PTR(-EIO);
    rt_registered++; return &fake_retimer;
}
static void typec_switch_unregister(struct typec_switch_dev *s)
{ (void)s; sw_unregistered++; }
static void typec_retimer_unregister(struct typec_retimer *r)
{ (void)r; rt_unregistered++; }
static void i2c_set_clientdata(struct i2c_client *i, void *d)
{ i->data=d; last_redriver=d; }
static void *i2c_get_clientdata(struct i2c_client *i) { return i->data; }
"""

# Note: ps5169_reg_init is declared in driver data. Its members are used
# in stub above, so move the actual data declarations before fake services.
split_at = stub.index("enum stage {")
preamble = stub[:split_at]
services = stub[split_at:]

tests = r"""
static void assert_clean(enum stage failed)
{
    assert(!powered);
    assert(!fake_enable.last);
    assert(switch_gets == switch_puts);
    assert(mux_gets == mux_puts);
    assert(sw_registered == sw_unregistered);
    assert(rt_registered == rt_unregistered);
    assert(!last_redriver);
    if (failed >= FAIL_ID_READ) {
        assert(gpio_high == 1 && gpio_low == 1);
        assert(sleep_10ms == 1);
    }
}
static const struct ps5169_reg_init reference[27] = {
    {0x9d,0x80,10000},{0x9d,0x00,0},{0x40,0x80,0},
    {0x8d,0x01,0},{0x90,0x01,0},{0x51,0x87,0},
    {0x50,0x20,0},{0x54,0x11,0},{0x5d,0x66,0},
    {0x52,0x50,0},{0x55,0x00,0},{0x56,0x00,0},
    {0x57,0x00,0},{0x58,0x00,0},{0x59,0x00,0},
    {0x5a,0x00,0},{0x5b,0x00,0},{0x5e,0x07,0},
    {0x5f,0x00,0},{0x60,0x00,0},{0x61,0x03,0},
    {0x65,0x40,0},{0x66,0x00,0},{0x67,0x03,0},
    {0x75,0x0c,0},{0x77,0x00,0},{0x78,0x7c,0}
};
int main(void)
{
    struct i2c_client client = {0};
    int i, ret;
    const enum stage failures[] = {
        FAIL_REG_GET, FAIL_GPIO_GET, FAIL_MAP_GET,
        FAIL_SWITCH_GET, FAIL_MUX_GET, FAIL_SUPPLY_ENABLE,
        FAIL_ID_READ, FAIL_REV_READ, FAIL_AUX_BRIDGE,
        FAIL_SWITCH_REGISTER, FAIL_RETIMER_REGISTER, FAIL_WRONG_ID
    };
    for (i = 0; i < (int)ARRAY_SIZE(failures); i++) {
        reset_fake();
        client.data = NULL;
        fail_stage = failures[i];
        ret = ps5169_probe(&client);
        assert(ret == (fail_stage == FAIL_WRONG_ID ? -EINVAL : -EIO));
        assert_clean(failures[i]);
    }
    puts("production ps5169_probe: acquisition, ID, aux, switch, retimer failure unwind PASS");

    /* All stock writes must match the pinned 27-triplet reference in order.
       The one 10ms register delay is independent of the GPIO enable delay. */
    reset_fake();
    client.data = NULL;
    assert(ps5169_probe(&client) == 0);
    assert(powered && fake_enable.last == 1 && sleep_10ms == 1);
    assert(io_writes >= 30);
    assert(observed_count == 27);
    for (i = 0; i < 27; i++) {
        assert(observed_profile[i].reg == reference[i].reg);
        assert(observed_profile[i].value == reference[i].value);
        assert(delay_after[i] == reference[i].delay_us);
    }
    assert(last_redriver && last_redriver->hw_valid);
    ps5169_remove(&client);
    assert(!powered && !fake_enable.last);
    assert(switch_gets == switch_puts && mux_gets == mux_puts);
    assert(sw_registered == sw_unregistered && rt_registered == rt_unregistered);
    puts("production ps5169_probe: 27 ordered stock writes, 10ms GPIO/delay and remove PASS");

    /* Fail every register write after partial power/enable; the probe must
       propagate EIO, undo supplies and GPIO, avoid registration, then retry. */
    for (i = 1; i <= 30; i++) {
        reset_fake();
        client.data = NULL;
        fail_write_at = i;
        assert(ps5169_probe(&client) == -EIO);
        assert_clean(NO_FAULT);
        assert(!sw_registered && !rt_registered);
        reset_fake();
        client.data = NULL;
        assert(ps5169_probe(&client) == 0);
        ps5169_remove(&client);
        assert(!powered && !fake_enable.last);
    }
    puts("production ps5169_probe: 30 partial-write failures, cleanup and reprobe PASS");
    puts("PS5169 production lifecycle fault injection PASS (host mocks, not kernel/hardware)");
    return 0;
}
"""

with tempfile.TemporaryDirectory(prefix="ps5169-probe-") as directory:
    c = Path(directory) / "probe.c"
    exe = Path(directory) / "probe"
    c.write_text(preamble + "\n" + data + "\n" + services + "\n" + functions + "\n" + tests)
    subprocess.run(
        ["gcc", "-std=gnu11", "-O0", "-g", "-Wall", "-Wextra",
         "-Werror", "-Wno-unused-const-variable", "-Wno-unused-function",
         str(c), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
