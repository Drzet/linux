#!/usr/bin/env python3
"""Execute the unmodified gpio-sbu-mux.c function bodies with mocked kernel APIs.

This is a host control-flow test, NOT a kernel concurrency or hardware test.
Use: python3 scripts/razr/test-gpio-sbu-supply.py [kernel-source-root]
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
source_path = root / "drivers/usb/typec/mux/gpio-sbu-mux.c"
source = source_path.read_text()
source = re.sub(r"^#include <linux/[^>]+>\n", "", source, flags=re.MULTILINE)

mock = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define GFP_KERNEL 0
#define GPIOD_OUT_LOW 0
#define EPROBE_DEFER 517
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(p) ((long)(intptr_t)(p))
#define IS_ERR(p) ((uintptr_t)(void *)(p) >= (uintptr_t)-4095)
#define MODULE_DEVICE_TABLE(a,b)
#define MODULE_DESCRIPTION(a)
#define MODULE_LICENSE(a)
#define module_platform_driver(a)

struct mutex { int locked; };
static void mutex_init(struct mutex *m) { m->locked = 0; }
static void mutex_lock(struct mutex *m) { assert(!m->locked); m->locked = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->locked); m->locked = 0; }

enum typec_orientation { TYPEC_ORIENTATION_NONE, TYPEC_ORIENTATION_NORMAL, TYPEC_ORIENTATION_REVERSE };
enum { TYPEC_STATE_SAFE=10, TYPEC_STATE_USB=11, TYPEC_DP_STATE_C=12, TYPEC_DP_STATE_D=13, TYPEC_DP_STATE_E=14 };
struct typec_mux_state { int mode; };
struct typec_switch_dev { void *data; int (*set)(struct typec_switch_dev *, enum typec_orientation); };
struct typec_mux_dev { void *data; int (*set)(struct typec_mux_dev *, struct typec_mux_state *); };
struct typec_switch_desc { void *drvdata; void *fwnode; int (*set)(struct typec_switch_dev *, enum typec_orientation); };
struct typec_mux_desc { void *drvdata; void *fwnode; int (*set)(struct typec_mux_dev *, struct typec_mux_state *); };
struct regulator { int id; };
struct gpio_desc { int index; bool logical_value; };
struct device { int id; };
struct platform_device { struct device dev; void *drvdata; };
struct of_device_id { const char *compatible; };
struct platform_driver {
    int (*probe)(struct platform_device *);
    void (*remove)(struct platform_device *);
    struct { const char *name; const struct of_device_id *of_match_table; } driver;
};

enum fault {
    F_NONE, F_GPIO_ENABLE, F_GPIO_SELECT, F_REG_GET_DEFER, F_REG_GET_MISSING,
    F_REG_ENABLE, F_ACTION, F_SW_REGISTER, F_MUX_REGISTER
};
enum event {
    E_GPIO_ENABLE_INIT=1, E_GPIO_SELECT_INIT, E_GET_REG, E_REG_ENABLE,
    E_ADD_ACTION, E_SW_REGISTER, E_MUX_REGISTER, E_SW_UNREGISTER,
    E_MUX_UNREGISTER, E_OE_OFF, E_OE_ON, E_SELECT_ZERO, E_SELECT_ONE, E_REG_DISABLE
};
static enum fault fail;
static struct regulator regulator;
static struct gpio_desc en_gpio, sel_gpio;
static struct typec_switch_dev sw;
static struct typec_mux_dev mux;
static struct platform_device pdev;
static void (*managed_action)(void *);
static void *managed_data;
static int events[256], events_count;
static int reg_refs, reg_gets, reg_enables, reg_disables;
static int sw_live, mux_live, sw_calls, mux_calls, warn_count, assert_count, cases;
static bool supply_property;
static void event(int kind) { assert(events_count < 256); events[events_count++] = kind; }
static int event_position(int kind) {
    for (int i=0; i<events_count; ++i) if(events[i]==kind) return i;
    return -1;
}
#define CHECK(e) do { ++assert_count; if(!(e)) { fprintf(stderr, "CHECK FAILED %s:%d: %s\n", __FILE__, __LINE__, #e); abort(); } } while(0)
static void reset(bool supply, enum fault which) {
    memset(&pdev, 0, sizeof(pdev)); memset(&en_gpio,0,sizeof(en_gpio));
    memset(&sel_gpio,0,sizeof(sel_gpio)); memset(&sw,0,sizeof(sw));
    memset(&mux,0,sizeof(mux)); managed_action=NULL; managed_data=NULL;
    events_count=reg_refs=reg_gets=reg_enables=reg_disables=0;
    sw_live=mux_live=sw_calls=mux_calls=warn_count=0;
    supply_property=supply; fail=which;
}
static void *devm_kzalloc(struct device *dev, size_t n, int gfp) {
    (void)dev; (void)gfp; return calloc(1,n);
}
static int dev_err_probe(struct device *dev, int err, const char *fmt, ...) {
    (void)dev; (void)fmt; return err;
}
static void dev_warn(struct device *dev, const char *fmt, ...) {
    (void)dev; (void)fmt; ++warn_count;
}
static void *dev_fwnode(struct device *dev) { return dev; }
static bool device_property_present(struct device *dev, const char *key) {
    (void)dev; CHECK(!strcmp(key, "vcc-supply")); return supply_property;
}
static struct gpio_desc *devm_gpiod_get_optional(struct device *dev, const char *name, int flags) {
    (void)dev; CHECK(!strcmp(name,"enable")); CHECK(flags==GPIOD_OUT_LOW);
    event(E_GPIO_ENABLE_INIT); if(fail==F_GPIO_ENABLE) return ERR_PTR(-EIO);
    en_gpio.index=1; en_gpio.logical_value=false; return &en_gpio;
}
static struct gpio_desc *devm_gpiod_get(struct device *dev, const char *name, int flags) {
    (void)dev; CHECK(!strcmp(name,"select")); CHECK(flags==GPIOD_OUT_LOW);
    event(E_GPIO_SELECT_INIT); if(fail==F_GPIO_SELECT) return ERR_PTR(-EIO);
    sel_gpio.index=2; sel_gpio.logical_value=false; return &sel_gpio;
}
static void gpiod_set_value_cansleep(struct gpio_desc *g, bool value) {
    CHECK(g==&en_gpio || g==&sel_gpio);
    if (g==&en_gpio) {
        if(value && supply_property) CHECK(reg_refs>0);
        event(value?E_OE_ON:E_OE_OFF);
    } else event(value?E_SELECT_ONE:E_SELECT_ZERO);
    g->logical_value=value;
}
static struct regulator *devm_regulator_get_optional(struct device *dev, const char *name) {
    (void)dev; CHECK(!strcmp(name,"vcc")); CHECK(supply_property);
    event(E_GET_REG); ++reg_gets;
    if(fail==F_REG_GET_DEFER) return ERR_PTR(-EPROBE_DEFER);
    if(fail==F_REG_GET_MISSING) return ERR_PTR(-ENODEV);
    return &regulator;
}
static int regulator_enable(struct regulator *r) {
    CHECK(r==&regulator); CHECK(!en_gpio.logical_value);
    event(E_REG_ENABLE); ++reg_enables;
    if(fail==F_REG_ENABLE) return -EIO;
    ++reg_refs; return 0;
}
static int regulator_disable(struct regulator *r) {
    CHECK(r==&regulator); CHECK(reg_refs>0);
    event(E_REG_DISABLE); ++reg_disables; --reg_refs;
    return 0;
}
static int devm_add_action_or_reset(struct device *dev, void (*fn)(void *), void *data) {
    (void)dev; event(E_ADD_ACTION);
    if(fail==F_ACTION) { fn(data); return -ENOMEM; }
    CHECK(!managed_action); managed_action=fn; managed_data=data; return 0;
}
static void release_managed(void) {
    if (managed_action) { void (*fn)(void *)=managed_action; managed_action=NULL; fn(managed_data); }
}
static void *typec_switch_get_drvdata(struct typec_switch_dev *d) { return d->data; }
static void *typec_mux_get_drvdata(struct typec_mux_dev *d) { return d->data; }
static struct typec_switch_dev *typec_switch_register(struct device *dev, const struct typec_switch_desc *desc) {
    (void)dev; event(E_SW_REGISTER);
    if(fail==F_SW_REGISTER) return ERR_PTR(-EIO);
    CHECK(!sw_live); sw_live=1; sw.data=desc->drvdata; sw.set=desc->set;
    if(supply_property) CHECK(reg_refs==1);
    return &sw;
}
static void typec_switch_unregister(struct typec_switch_dev *d) {
    CHECK(d==&sw && sw_live==1); CHECK(reg_refs==1 || !supply_property);
    event(E_SW_UNREGISTER); sw_live=0;
}
static struct typec_mux_dev *typec_mux_register(struct device *dev, const struct typec_mux_desc *desc) {
    (void)dev; event(E_MUX_REGISTER);
    if(fail==F_MUX_REGISTER) return ERR_PTR(-EIO);
    CHECK(!mux_live); mux_live=1; mux.data=desc->drvdata; mux.set=desc->set;
    if(supply_property) CHECK(reg_refs==1);
    return &mux;
}
static void typec_mux_unregister(struct typec_mux_dev *d) {
    CHECK(d==&mux && mux_live==1); CHECK(reg_refs==1 || !supply_property);
    event(E_MUX_UNREGISTER); mux_live=0;
}
static void platform_set_drvdata(struct platform_device *d, void *data) { d->drvdata=data; }
static void *platform_get_drvdata(struct platform_device *d) { return d->drvdata; }
"""
cases_src = r"""
static int attach(int mode, enum typec_orientation orientation) {
    struct typec_mux_state st = { .mode=mode };
    ++sw_calls;
    int ret=sw.set(&sw,orientation);
    if(ret) return ret;
    ++mux_calls;
    return mux.set(&mux,&st);
}
static void probe_good(bool powered) {
    reset(powered,F_NONE);
    CHECK(gpio_sbu_mux_probe(&pdev)==0);
    CHECK(sw_live && mux_live);
    CHECK(reg_refs==(powered?1:0));
    if(powered) {
        CHECK(reg_gets==1 && reg_enables==1 && reg_disables==0);
        CHECK(event_position(E_REG_ENABLE)<event_position(E_SW_REGISTER));
        CHECK(event_position(E_REG_ENABLE)<event_position(E_MUX_REGISTER));
        CHECK(event_position(E_GPIO_ENABLE_INIT)<event_position(E_REG_ENABLE));
    } else CHECK(reg_gets==0 && reg_enables==0);
}
static void finish_success(bool powered) {
    gpio_sbu_mux_remove(&pdev);
    CHECK(!sw_live && !mux_live);
    CHECK(!en_gpio.logical_value);
    if(powered) {
        CHECK(reg_refs==1);
        CHECK(event_position(E_OE_OFF)<event_position(E_REG_DISABLE) ||
              event_position(E_REG_DISABLE)<0);
    }
    CHECK(event_position(E_MUX_UNREGISTER)<event_position(E_SW_UNREGISTER));
    release_managed();
    CHECK(!reg_refs);
    CHECK(reg_disables==(powered?1:0));
}
static void test_legacy(void) {
    probe_good(false);
    CHECK(attach(TYPEC_DP_STATE_C,TYPEC_ORIENTATION_REVERSE)==0);
    CHECK(en_gpio.logical_value && sel_gpio.logical_value);
    CHECK(attach(TYPEC_STATE_USB,TYPEC_ORIENTATION_NONE)==0);
    CHECK(!en_gpio.logical_value);
    finish_success(false);
}
static void test_bound_lifetime(void) {
    probe_good(true);
    CHECK(attach(TYPEC_DP_STATE_C,TYPEC_ORIENTATION_NORMAL)==0);
    CHECK(en_gpio.logical_value && !sel_gpio.logical_value);
    CHECK(attach(TYPEC_DP_STATE_D,TYPEC_ORIENTATION_REVERSE)==0);
    CHECK(en_gpio.logical_value && sel_gpio.logical_value);
    CHECK(attach(TYPEC_STATE_SAFE,TYPEC_ORIENTATION_NONE)==0);
    CHECK(!en_gpio.logical_value && sel_gpio.logical_value);
    CHECK(reg_refs==1); /* Both detach and PTN off must not release this AUX vote. */
    CHECK(attach(TYPEC_DP_STATE_E,TYPEC_ORIENTATION_NORMAL)==0);
    CHECK(en_gpio.logical_value && !sel_gpio.logical_value);
    finish_success(true);
}
static void test_remove_blocks_callbacks(void) {
    struct typec_mux_state st={.mode=TYPEC_DP_STATE_C};
    probe_good(true);
    CHECK(attach(TYPEC_DP_STATE_C,TYPEC_ORIENTATION_NORMAL)==0);
    gpio_sbu_mux_disconnect(pdev.drvdata); /* Race surrogate: begin remove */
    CHECK(!en_gpio.logical_value);
    CHECK(sw.set(&sw,TYPEC_ORIENTATION_REVERSE)==-ENODEV);
    CHECK(mux.set(&mux,&st)==-ENODEV);
    CHECK(!en_gpio.logical_value);
    gpio_sbu_mux_remove(&pdev);
    release_managed();
    CHECK(reg_refs==0 && reg_disables==1);
}
static void test_one_fault(enum fault f, int expected) {
    reset(true,f);
    CHECK(gpio_sbu_mux_probe(&pdev)==expected);
    CHECK(!sw_live && !mux_live);
    CHECK(!en_gpio.logical_value);
    release_managed();
    CHECK(reg_refs==0);
    CHECK(reg_disables==(f==F_ACTION || f==F_SW_REGISTER || f==F_MUX_REGISTER ? 1:0));
    if(f==F_REG_GET_DEFER || f==F_REG_GET_MISSING || f==F_REG_ENABLE ||
       f==F_GPIO_ENABLE || f==F_GPIO_SELECT) CHECK(event_position(E_SW_REGISTER)<0);
    if(f==F_MUX_REGISTER) CHECK(event_position(E_SW_UNREGISTER)>=0);
    if(f==F_SW_REGISTER || f==F_MUX_REGISTER)
        CHECK(event_position(E_REG_DISABLE)>event_position(E_SW_UNREGISTER) ||
              f==F_SW_REGISTER);
}
static void run_case(void (*fn)(void), const char *name) {
    fn(); ++cases; printf("PASS %s\n",name);
}
int main(void) {
    run_case(test_legacy,"property-absent legacy");
    run_case(test_bound_lifetime,"property-present attach, detach, PTN off and remove");
    run_case(test_remove_blocks_callbacks,"remove disables OE and blocks callbacks");
    test_one_fault(F_GPIO_ENABLE,-EIO); ++cases;
    test_one_fault(F_GPIO_SELECT,-EIO); ++cases;
    test_one_fault(F_REG_GET_DEFER,-EPROBE_DEFER); ++cases;
    test_one_fault(F_REG_GET_MISSING,-ENODEV); ++cases;
    test_one_fault(F_REG_ENABLE,-EIO); ++cases;
    test_one_fault(F_ACTION,-ENOMEM); ++cases;
    test_one_fault(F_SW_REGISTER,-EIO); ++cases;
    test_one_fault(F_MUX_REGISTER,-EIO); ++cases;
    printf("GPIO_SBU_PRODUCTION_HOST_PASS %d cases, %d assertions\n", cases, assert_count);
    return 0;
}
"""
with tempfile.TemporaryDirectory(prefix="gpio-sbu-supply-") as tmp:
    tmp = Path(tmp)
    c_path = tmp / "host-test.c"
    binary = tmp / "host-test"
    c_path.write_text(mock + '\n#line 1 "gpio-sbu-mux.c"\n' + source +
                      '\n#line 1 "gpio-sbu-host-cases.c"\n' + cases_src)
    cmd = ["cc", "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
           "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-parameter",
           "-fsanitize=undefined", "-fno-omit-frame-pointer",
           str(c_path), "-o", str(binary)]
    subprocess.run(cmd, check=True)
    subprocess.run([str(binary)], check=True)
