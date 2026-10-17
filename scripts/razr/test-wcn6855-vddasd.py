#!/usr/bin/env python3
"""Compile unmodified WCN PMU probe and bulk callbacks with fake kernel services.

Actual driver function bodies and variant data are extracted from the checked-out
kernel source. The regulator and two-consumer refcount simulator are mocks:
this is NOT a pwrseq-core KUnit, concurrency, regulator-core or hardware test.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
src = (root / "drivers/power/sequencing/pwrseq-qcom-wcn.c").read_text()

def definition(start):
    offset = src.find(start)
    if offset < 0:
        raise AssertionError("missing production definition: " + start)
    brace = src.find("{", offset)
    depth = 0
    for pos in range(brace, len(src)):
        if src[pos] == "{":
            depth += 1
        elif src[pos] == "}":
            depth -= 1
            if not depth:
                end = pos + 1
                if src[end:end + 1] == ";":
                    end += 1
                return src[offset:end]
    raise AssertionError("unbalanced definition: " + start)

starts = [
    "struct pwrseq_qcom_wcn_pdata {",
    "struct pwrseq_qcom_wcn_ctx {",
    "static const char *const pwrseq_wcn6750_vregs[] = {",
    "static const struct pwrseq_qcom_wcn_pdata pwrseq_wcn6750_of_data = {",
    "static const char *const pwrseq_wcn6855_vregs[] = {",
    "static const struct pwrseq_qcom_wcn_pdata pwrseq_wcn6855_of_data = {",
    "static int pwrseq_qcom_wcn_vregs_enable(",
    "static int pwrseq_qcom_wcn_vregs_disable(",
    "static int pwrseq_qcom_wcn_probe(",
]
if src.count(".deps = pwrseq_qcom_wcn6855_unit_deps") != 2:
    raise AssertionError("BT and WLAN must share the WCN6855 dependency units")
if src.count("&pwrseq_qcom_wcn_vregs_unit_data") < 2:
    raise AssertionError("common regulator unit no longer shared")
production = "\n\n".join(definition(start) for start in starts)

mocks = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define EPROBE_DEFER 517
#define GFP_KERNEL 0
#define GPIOD_OUT_LOW 0
#define GPIOD_ASIS 1
#define THIS_MODULE NULL
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define ERR_PTR(x) ((void *)(intptr_t)(x))
#define PTR_ERR(x) ((long)(intptr_t)(x))
#define IS_ERR(x) ((uintptr_t)(void *)(x) >= (uintptr_t)-4095)
#define CHECK(x) do { ++checks; if (!(x)) { \
    fprintf(stderr, "CHECK FAILED line %d: %s\n", __LINE__, #x); abort(); } } while (0)
struct device_node { int dummy; };
struct device { struct device_node *of_node; };
struct platform_device { struct device dev; };
struct pwrseq_device { void *drvdata; };
struct pwrseq_target_data { int dummy; };
struct regulator { int dummy; };
struct regulator_bulk_data { const char *supply; };
struct gpio_desc { int dummy; };
struct clk { int dummy; };
struct pwrseq_config {
    struct device *parent;
    void *owner, *drvdata;
    int (*match)(struct pwrseq_device *, struct device *);
    const struct pwrseq_target_data **targets;
};
static struct device_node node;
static struct gpio_desc gpio;
static struct pwrseq_device pwrseq;
static struct platform_device pdev;
static const struct pwrseq_target_data *pwrseq_qcom_wcn6855_targets[] = {NULL};
static const struct pwrseq_target_data *pwrseq_qcom_wcn_targets[] = {NULL};
static int checks, cases, put_count, requested, get_calls, registered, reg_on, ena_calls, dis_calls;
static int fail_index = -1, fail_get, fail_gpio;
static bool supply_property, malformed, use_wcn6750;
static struct pwrseq_qcom_wcn_ctx *current_ctx;
static int pwrseq_qcom_wcn_match(struct pwrseq_device *p, struct device *d)
{ (void)p; (void)d; return 0; }
static void *devm_kzalloc(struct device *d, size_t n, int flags)
{ (void)d; (void)flags; return calloc(1,n); }
static void *devm_kcalloc(struct device *d, size_t n, size_t size, int flags)
{ (void)d; (void)flags; return calloc(n,size); }
static const void *device_get_match_data(struct device *d)
{ (void)d; return use_wcn6750 ? (const void *)&pwrseq_wcn6750_of_data : (const void *)&pwrseq_wcn6855_of_data; }
static bool of_property_present(const struct device_node *n, const char *key)
{ CHECK(n==&node); CHECK(!strcmp(key,"vddasd-supply")); return supply_property; }
static struct device_node *of_parse_phandle(const struct device_node *n, const char *key, int arg)
{ CHECK(n==&node); CHECK(!strcmp(key,"vddasd-supply")); CHECK(arg==0); return malformed?NULL:&node; }
static void of_node_put(struct device_node *n)
{ CHECK(n==&node); ++put_count; }
static int dev_err_probe(struct device *d, int err, const char *fmt, ...)
{ (void)d; (void)fmt; return err; }
static int devm_regulator_bulk_get(struct device *d, int count, struct regulator_bulk_data *r)
{
    (void)d;
    ++get_calls; requested=count;
    CHECK(count==(use_wcn6750?7:supply_property?11:10));
    CHECK(!strcmp(r[0].supply,use_wcn6750?"vddaon":"vddio"));
    CHECK(!strcmp(r[count-1].supply, use_wcn6750?"vddrfa2p2":supply_property?"vddasd":"vddpcie1p9"));
    return fail_get;
}
static struct regulator *devm_regulator_get(struct device *d, const char *key)
{ (void)d; (void)key; abort(); }
static struct gpio_desc *devm_gpiod_get_optional(struct device *d, const char *key, int flags)
{ (void)d; (void)key; (void)flags; return fail_gpio?ERR_PTR(-EIO):&gpio; }
static int gpiod_get_value_cansleep(struct gpio_desc *g)
{ CHECK(g==&gpio); return 0; }
static int gpiod_direction_output(struct gpio_desc *g, int val)
{ CHECK(g==&gpio); CHECK(val==0); return 0; }
static struct clk *devm_clk_get_optional(struct device *d, const char *key)
{ (void)d; (void)key; return NULL; }
static struct pwrseq_device *devm_pwrseq_device_register(struct device *d,
                                                         const struct pwrseq_config *c)
{
    CHECK(d==&pdev.dev); CHECK(c->drvdata!=NULL);
    ++registered; pwrseq.drvdata=c->drvdata; current_ctx=c->drvdata;
    return &pwrseq;
}
static void *pwrseq_device_get_drvdata(struct pwrseq_device *p)
{ CHECK(p==&pwrseq); return p->drvdata; }
static int regulator_bulk_enable(int count, struct regulator_bulk_data *r)
{
    ++ena_calls; CHECK(count==requested); CHECK(r==current_ctx->regs);
    /* Regulator core mock: rollback previously enabled rails on failure. */
    for (int i=0;i<count;i++) {
        if (i==fail_index) { reg_on=0; return -EIO; }
        ++reg_on;
    }
    return 0;
}
static int regulator_bulk_disable(int count, struct regulator_bulk_data *r)
{
    ++dis_calls; CHECK(count==requested); CHECK(r==current_ctx->regs);
    CHECK(reg_on==count); reg_on=0; return 0;
}
"""

cases_code = r"""
static void reset(bool present, bool bad_phandle, bool wcn6750)
{
    supply_property=present; malformed=bad_phandle; use_wcn6750=wcn6750;
    memset(&pdev,0,sizeof(pdev)); pdev.dev.of_node=&node;
    memset(&pwrseq,0,sizeof(pwrseq)); current_ctx=NULL;
    put_count=requested=get_calls=registered=reg_on=ena_calls=dis_calls=0;
    fail_get=fail_gpio=0; fail_index=-1;
}
static int probe(void) { return pwrseq_qcom_wcn_probe(&pdev); }
static int shared_users;
static bool active[2];
/* Deliberately mocked consumer/core unit refcount. Tests use real driver
 * probe and vregs callbacks, NOT pwrseq-core's actual state lock or graph. */
static int turn_on(int id)
{
    int ret=0;
    CHECK(!active[id]);
    if (shared_users==0) {
        ret=pwrseq_qcom_wcn_vregs_enable(&pwrseq);
        if (ret) return ret;
    }
    ++shared_users; active[id]=true;
    return 0;
}
static void turn_off(int id)
{
    CHECK(active[id]); CHECK(shared_users>0);
    active[id]=false;
    if (!--shared_users) CHECK(!pwrseq_qcom_wcn_vregs_disable(&pwrseq));
}
static void both_orders(bool present, int first)
{
    reset(present,false,false);
    CHECK(probe()==0); CHECK(requested==(present?11:10));
    CHECK(get_calls==1 && registered==1 && put_count==(present?1:0));
    shared_users=0; active[0]=active[1]=false;
    CHECK(turn_on(first)==0); CHECK(ena_calls==1);
    CHECK(turn_on(1-first)==0); CHECK(ena_calls==1);
    turn_off(first); CHECK(reg_on==requested && dis_calls==0);
    turn_off(1-first); CHECK(reg_on==0 && dis_calls==1);
    ++cases;
}
static void failed_second_target(bool present)
{
    reset(present,false,false);
    CHECK(probe()==0);
    shared_users=0; active[0]=active[1]=false;
    CHECK(turn_on(0)==0);
    /* Model failure in the second target's independent BT/WLAN GPIO
     * after successful shared dependency acquisition. */
    CHECK(shared_users==1 && reg_on==requested);
    CHECK(!active[1]); CHECK(ena_calls==1);
    turn_off(0); CHECK(!shared_users && !reg_on && dis_calls==1);
    ++cases;
}
static void lookup_errors(void)
{
    reset(true,true,false);
    CHECK(probe()==-EINVAL); CHECK(!get_calls && !registered && !put_count); ++cases;
    reset(true,false,false); fail_get=-EPROBE_DEFER;
    CHECK(probe()==-EPROBE_DEFER); CHECK(get_calls==1 && !registered && put_count==1); ++cases;
    reset(true,false,false); fail_get=-ENODEV;
    CHECK(probe()==-ENODEV); CHECK(get_calls==1 && !registered); ++cases;
    reset(true,false,false); fail_gpio=1;
    CHECK(probe()==-EIO); CHECK(get_calls==1 && !registered); ++cases;
}
static void power_enable_errors(bool present)
{
    reset(present,false,false); CHECK(probe()==0);
    fail_index=2; CHECK(pwrseq_qcom_wcn_vregs_enable(&pwrseq)==-EIO);
    CHECK(!reg_on && ena_calls==1 && !dis_calls); ++cases;
    fail_index=requested-1; CHECK(pwrseq_qcom_wcn_vregs_enable(&pwrseq)==-EIO);
    CHECK(!reg_on && ena_calls==2 && !dis_calls); ++cases;
    fail_index=-1; CHECK(pwrseq_qcom_wcn_vregs_enable(&pwrseq)==0);
    CHECK(reg_on==requested);
    CHECK(pwrseq_qcom_wcn_vregs_disable(&pwrseq)==0);
    CHECK(!reg_on && dis_calls==1); ++cases;
}
static void other_variant(void)
{
    reset(true,false,true);
    CHECK(probe()==0);
    /* WCN6750 has its own mandatory vddasd inside its original seven rails;
     * the new WCN6855-only optional field must have no effect. */
    CHECK(requested==7 && put_count==0 && registered==1);
    CHECK(pwrseq_qcom_wcn_vregs_enable(&pwrseq)==0);
    CHECK(reg_on==7);
    CHECK(pwrseq_qcom_wcn_vregs_disable(&pwrseq)==0); ++cases;
}
int main(void)
{
    both_orders(false,0); both_orders(false,1);
    both_orders(true,0); both_orders(true,1);
    failed_second_target(false); failed_second_target(true);
    lookup_errors();
    power_enable_errors(false); power_enable_errors(true);
    other_variant();
    printf("WCN6855_PRODUCTION_HOST_PASS %d cases, %d assertions (mocked regulator/core)\n",cases,checks);
    return 0;
}
"""

with tempfile.TemporaryDirectory(prefix="wcn6855-vddasd-") as temp:
    test = Path(temp) / "host.c"
    binary = Path(temp) / "host"
    # pdata declaration needs to precede variant data, but mocks supply
    # underlying kernel types first. Keep declaration/function order intact.
    structs = "\n\n".join(definition(s) for s in starts[:2])
    variant_data = "\n\n".join(definition(s) for s in starts[2:6])
    callbacks = "\n\n".join(definition(s) for s in starts[6:])
    # The mocks refer to variant data, so split mock declarations before
    # functions that consume pdata's static constants.
    marker = "static const void *device_get_match_data("
    pre, post = mocks.split(marker, 1)
    test.write_text(pre.replace(
        "static struct pwrseq_qcom_wcn_ctx *current_ctx;\n",
        "static struct pwrseq_qcom_wcn_ctx *current_ctx;\n"
    ).replace(
        "static int pwrseq_qcom_wcn_match(", "__MOCK_MATCH__("
    ).replace(
        "__MOCK_MATCH__(", "static int pwrseq_qcom_wcn_match("
    ).replace(
        "struct pwrseq_qcom_wcn_ctx *current_ctx;\n",
        "struct pwrseq_qcom_wcn_ctx *current_ctx;\n"
    ).replace(
        "static struct pwrseq_qcom_wcn_ctx *current_ctx;\n",
        "static struct pwrseq_qcom_wcn_ctx *current_ctx;\n"
    ) + "\n" + structs + "\n" + variant_data +
    "\nstatic const void *device_get_match_data(" + post +
    "\n#line 1 \"pwrseq-qcom-wcn.c\"\n" + callbacks +
    "\n#line 1 \"wcn6855-host-cases.c\"\n" + cases_code)
    subprocess.run(["cc","-std=gnu11","-O1","-g","-Wall","-Wextra",
                    "-Werror","-Wno-unused-function","-Wno-unused-variable",
                    "-Wno-unused-parameter","-fsanitize=undefined",
                    "-fno-omit-frame-pointer",str(test),"-o",str(binary)],
                   check=True)
    subprocess.run([str(binary)],check=True)
