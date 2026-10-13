#!/usr/bin/env python3
"""Compile and execute unchanged Iris context-child production functions.

Host mocks model only OF child references and platform registrations.
Not a kernel bus, IOMMU, DMA or handset runtime test.
"""
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "drivers/media/platform/qcom/iris/iris_probe.c"

def extract(name):
    code = SOURCE.read_text()
    match = re.search(r"\b" + re.escape(name) + r"\s*\([^;]*?\)\s*\{", code, re.S)
    if not match:
        raise RuntimeError(f"function missing: {name}")
    start = code.rfind("\n", 0, match.start()) + 1
    depth = 0
    for index in range(match.end() - 1, len(code)):
        if code[index] == "{":
            depth += 1
        elif code[index] == "}":
            depth -= 1
            if depth == 0:
                return code[start:index + 1]
    raise RuntimeError("unbalanced source")

HEADER = r"""
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#define DMA_BIT_MASK(n) ((1ULL << (n)) - 1)
#define PLATFORM_DEVID_AUTO (-1)
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define PTR_ERR(p) ((long)(intptr_t)(p))
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define ERR_CAST(p) ((void *)(p))
struct fwnode_handle { int unused; };
struct device_node { const char *name; struct fwnode_handle fwnode; int refs; };
struct device { const char *name; };
struct platform_device { struct device dev; int registered; };
struct platform_device_info {
    unsigned long long dma_mask;
    struct fwnode_handle *fwnode;
    const char *name;
    int id;
    struct device *parent;
};
struct iris_platform_data { unsigned long long dma_mask; };
struct iris_core {
    struct device *dev;
    struct iris_platform_data *iris_platform_data;
    struct device *np_dev;
    struct device *p_dev;
};
static struct device_node nodes[2] = {
    { .name = "non-pixel" }, { .name = "pixel" }
};
static struct platform_device created[2];
static int present[2], fail_at, call_index, registered, unregistered;
static int bad_unreg, put_count, get_count;
static struct device_node *of_get_child_by_name(struct device_node *root, const char *name);
static struct device_node root_node;
static struct device_node *of_get_child_by_name(struct device_node *root, const char *name)
{
    int i;
    (void)root;
    for (i = 0; i < 2; ++i)
        if (present[i] && !strcmp(nodes[i].name, name)) {
            nodes[i].refs++; get_count++;
            return &nodes[i];
        }
    return NULL;
}
static struct device_node *dev_of_node(struct device *dev) { (void)dev; return &root_node; }
#define of_node(d) dev_of_node(d)
static void of_node_put(struct device_node *node) {
    if (--node->refs < 0) abort();
    put_count++;
}
static struct platform_device *platform_device_register_full(const struct platform_device_info *info)
{
    int i = (!strcmp(info->name, "pixel")) ? 1 : 0;
    call_index++;
    if (fail_at == call_index) return ERR_PTR(-12);
    created[i].dev.name = info->name;
    created[i].registered = 1;
    registered++;
    return &created[i];
}
static void platform_device_unregister(struct platform_device *pdev) {
    if (!pdev->registered) { bad_unreg++; return; }
    pdev->registered = 0;
    unregistered++;
}
#define to_platform_device(dev) ((struct platform_device *)(dev))
static void dma_set_max_seg_size(struct device *dev, unsigned long long size)
{ (void)dev; (void)size; }
static void dma_set_seg_boundary(struct device *dev, unsigned long long size)
{ (void)dev; (void)size; }
"""
# Match only the production structure access; kernel stores the OF node
# in dev->of_node, which this minimal host struct also exposes.
HEADER = HEADER.replace('struct device { const char *name; };',
                        'struct device { const char *name; struct device_node *of_node; };')
HEADER = HEADER.replace('static struct device_node *dev_of_node(struct device *dev) { (void)dev; return &root_node; }\n#define of_node(d) dev_of_node(d)\n', '')
TESTS = r"""
static int failures;
#define CHECK(ok) do { if (!(ok)) { \
 fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #ok); failures++; \
 } } while (0)
static void reset(void) {
    memset(created, 0, sizeof(created));
    memset(nodes, 0, sizeof(nodes));
    nodes[0].name = "non-pixel"; nodes[1].name = "pixel";
    present[0] = present[1] = 1;
    fail_at = call_index = registered = unregistered = 0;
    bad_unreg = put_count = get_count = 0;
}
static void scenario(int have_np, int have_p, int error_at,
                     int expected_ret, int expected_reg, int expected_unreg)
{
    struct iris_platform_data platform = { .dma_mask = DMA_BIT_MASK(32) };
    struct device parent = { .name = "iris", .of_node = &root_node };
    struct iris_core core = { .dev = &parent, .iris_platform_data = &platform };
    int result;
    reset();
    present[0] = have_np; present[1] = have_p;
    fail_at = error_at;
    result = iris_init_cb_devs(&core);
    CHECK(result == expected_ret);
    CHECK(registered == expected_reg);
    CHECK(unregistered == expected_unreg);
    CHECK(get_count == put_count);
    CHECK(nodes[0].refs == 0 && nodes[1].refs == 0);
    CHECK(bad_unreg == 0);
    if (result == 0) {
        iris_deinit_cb_devs(&core);
        CHECK(unregistered == registered);
        CHECK(bad_unreg == 0);
    } else {
        CHECK(registered == unregistered);
    }
}
int main(void)
{
    scenario(0, 0, 0, 0, 0, 0);   /* legacy absent children */
    scenario(0, 1, 0, 0, 1, 0);   /* only pixel present */
    scenario(1, 0, 0, 0, 1, 0);   /* only nonpixel present */
    scenario(1, 1, 0, 0, 2, 0);   /* both present */
    scenario(1, 1, 1, -12, 0, 0); /* first registration fails */
    scenario(1, 1, 2, -12, 1, 1); /* second fails, first unwinds */
    printf("Iris production context-child tests: %s\n",
           failures ? "FAIL" : "PASS");
    return failures ? 1 : 0;
}
"""
def main():
    code = HEADER + "\n".join(extract(name) for name in (
        "iris_create_cb_dev", "iris_init_cb_devs", "iris_deinit_cb_devs")) + TESTS
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "iris_cb_test.c"
        binary = pathlib.Path(tmp) / "iris_cb_test"
        src.write_text(code)
        subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                        str(src), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
if __name__ == "__main__":
    main()
