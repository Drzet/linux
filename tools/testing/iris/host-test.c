// SPDX-License-Identifier: GPL-2.0-only
/* Provider shims, not a kernel/SMMU emulator. Production functions included below. */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <libfdt.h>

typedef uint64_t u64;
typedef uint32_t u32;
typedef uint32_t __be32;
typedef uint64_t phys_addr_t;
#define U64_MAX UINT64_MAX
#define EPROBE_DEFER 517
#define OF_BAD_ADDR UINT64_MAX
#define OF_MAX_ADDR_CELLS 4
#define OF_CHECK_COUNTS(a,s) ((a) > 0 && (a) <= 4 && (s) > 0)
#define DMA_BIT_MASK(n) ((1ULL << (n)) - 1)
#define pr_debug(...) ((void)0)
#define pr_err(...) ((void)0)
#define of_dump_addr(...) ((void)0)
#define unreachable() abort()
#define no_free_ptr(p) (p)
#define LOGIC_PIO_CPU_MMIO 0
struct logic_pio_hwaddr { int flags; };
static void *find_io_range_by_fwnode(void *p) { return NULL; }
#define cpu_to_be32 cpu_to_fdt32
#define be32_to_cpup(p) fdt32_to_cpu(*(p))
struct device_node { int offset, refs, fwnode; };
static struct device_node nodes[100];
static int node_count;
static void *blob;
static struct device_node *node_at(int off) {
 if (off < 0) return NULL;
 for (int i = 0; i < node_count; i++) if (nodes[i].offset == off) return &nodes[i];
 assert(node_count < 100); nodes[node_count].offset = off; return &nodes[node_count++];
}
static struct device_node *of_node_get(struct device_node *n) { if (n) n->refs++; return n; }
static void of_node_put(struct device_node *n) { if (n) { assert(n->refs > 0); n->refs--; } }
static void node_cleanup(struct device_node **n) { of_node_put(*n); }
#define __free(x) __attribute__((cleanup(node_cleanup)))
static struct device_node *of_get_parent(const struct device_node *n) {
 return of_node_get(node_at(fdt_parent_offset(blob, n->offset)));
}
static const void *of_get_property(const struct device_node *n, const char *p, void *len) {
 return fdt_getprop(blob, n->offset, p, len);
}
static bool of_property_present(struct device_node *n, const char *p) {
 return of_get_property(n, p, NULL) != NULL;
}
static u64 of_read_number(const __be32 *p, int count) {
 u64 v = 0; while (count--) v = (v << 32) | be32_to_cpup(p++); return v;
}
static int cells(const struct device_node *n, const char *name) {
 const __be32 *p = of_get_property(n, name, NULL); assert(p); return be32_to_cpup(p);
}
static int of_n_addr_cells(struct device_node *n) {
 struct device_node *p = of_get_parent(n); int v = cells(p, "#address-cells"); of_node_put(p); return v;
}
static int of_n_size_cells(struct device_node *n) {
 struct device_node *p = of_get_parent(n); int v = cells(p, "#size-cells"); of_node_put(p); return v;
}
static int of_bus_n_addr_cells(struct device_node *n) { return cells(n, "#address-cells"); }
static int of_bus_n_size_cells(struct device_node *n) { return cells(n, "#size-cells"); }
static struct device_node *of_find_node_by_phandle(u32 p) {
 return of_node_get(node_at(fdt_node_offset_by_phandle(blob, p)));
}
static struct device_node *of_parse_phandle(struct device_node *n, const char *p, int i) {
 int len; const __be32 *v = of_get_property(n, p, &len);
 return v && len >= (i + 1) * 4 ? of_find_node_by_phandle(be32_to_cpup(v + i)) : NULL;
}
struct of_phandle_args { struct device_node *np; int args_count; u32 args[4]; };
static int of_parse_phandle_with_args(const struct device_node *n, const char *p,
                                     const char *count, int i, struct of_phandle_args *a) {
 int len; const __be32 *v = of_get_property(n, p, &len);
 if (!v || i || len < 4) return -EINVAL;
 a->np = of_find_node_by_phandle(be32_to_cpup(v));
 if (!a->np) return -EINVAL;
 a->args_count = cells(a->np, count);
 assert(len == (a->args_count + 1) * 4);
 for (int j = 0; j < a->args_count; j++) a->args[j] = be32_to_cpup(v + j + 1);
 return 0;
}
static int of_count_phandle_with_args(struct device_node *n, const char *p, const char *count) {
 int len; const void *v = of_get_property(n, p, &len);
 return !v ? -ENOENT : len / (count ? 12 : 4);
}
static int of_property_match_string(const struct device_node *n, const char *p, const char *s) {
 return fdt_stringlist_search(blob, n->offset, p, s);
}
static bool of_device_is_compatible(struct device_node *n, const char *s) {
 return !fdt_node_check_compatible(blob, n->offset, s);
}
static bool of_dma_is_coherent(struct device_node *n) { return of_property_present(n, "dma-coherent"); }
static bool available = true;
static bool of_device_is_available(struct device_node *n) { return available; }
#include "of-production.h"

struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(x) struct list_head x = { &(x), &(x) }
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define list_for_each_entry(p,h,m) \
 for (p=container_of((h)->next,__typeof__(*p),m); &(p)->m != (h); p=container_of((p)->m.next,__typeof__(*p),m))
struct iommu_resv_region { u64 start, length; int type; struct list_head list; };
#define IOMMU_RESV_RESERVED 1
#define IOMMU_DOMAIN_DMA 3
#define IOMMU_DOMAIN_DMA_FQ 11
struct iommu_domain { int type; };
struct iommu_group { int refs; };
struct device_driver { const char *name; const void *of_match_table; };
struct device { struct device_node *of_node; struct device_driver *driver;
 void *data; int refs, locked; bool bound; struct iommu_domain *domain; struct iommu_group *group; };
struct platform_device { struct device dev; };
struct platform_driver { int (*probe)(struct platform_device *); void (*remove)(struct platform_device *); struct device_driver driver; };
struct of_device_id { const char *compatible; const void *data; };
#define MODULE_DEVICE_TABLE(...)
#define DL_FLAG_PM_RUNTIME 1
#define DL_FLAG_AUTOREMOVE_CONSUMER 2
#include "iris_dma_context.h"
static struct platform_device devices[2];
static struct iommu_domain domains[2];
static struct iommu_group groups[2];
static int missing_device, link_fail, action_fail, mask_fail, omit_reservation;
static int links, actions;
static void (*cleanup[2])(void *);
static void *cleanup_data[2];
static struct platform_device *of_find_device_by_node(struct device_node *n) {
 for (int i=0;i<2;i++) if (devices[i].dev.of_node==n) {
  if (missing_device == i+1) return NULL;
  devices[i].dev.refs++; return &devices[i];
 } return NULL;
}
static void put_device(void *v) { struct device *d=v; assert(d->refs>0); d->refs--; }
static void device_lock(struct device *d) { assert(!d->locked); d->locked=1; }
static void device_unlock(struct device *d) { assert(d->locked); d->locked=0; }
static bool device_is_bound(struct device *d) { assert(d->locked); return d->bound; }
static void *dev_get_drvdata(struct device *d) { return d->data; }
static void platform_set_drvdata(struct platform_device *p, void *data) { p->dev.data=data; }
static void *device_link_add(struct device *d, struct device *s, u32 flags) {
 assert(s->locked); assert(flags==(DL_FLAG_PM_RUNTIME|DL_FLAG_AUTOREMOVE_CONSUMER));
 if (link_fail==links+1) return NULL;
 links++; return s;
}
static int devm_add_action_or_reset(struct device *d, void (*f)(void *), void *v) {
 if (action_fail==actions+1) { f(v); return -ENOMEM; }
 cleanup[actions]=f; cleanup_data[actions++]=v; return 0;
}
static struct iommu_domain *iommu_get_domain_for_dev(struct device *d) { return d->domain; }
static struct iommu_group *iommu_group_get(struct device *d) { if(d->group) d->group->refs++; return d->group; }
static void iommu_group_put(struct iommu_group *g) { if(g) { assert(g->refs>0); g->refs--; } }
static int dma_set_mask_and_coherent(struct device *d,u64 mask) { assert(mask==UINT32_MAX); return mask_fail?-EIO:0; }
#define dev_err_probe(d,r,...) (r)
static const void *of_device_get_match_data(struct device *d);
static void iommu_get_resv_regions(struct device *d, struct list_head *h) {
 static struct iommu_resv_region r[2];
 for(int i=0;i<2;i++) {
  if(omit_reservation==i+1) continue;
  struct device_node *n=of_parse_phandle(d->of_node,"memory-region",i);
  const __be32 *map=of_get_property(n,"iommu-addresses",NULL);
  r[i].start=of_read_number(map+1,2); r[i].length=of_read_number(map+3,2);
  r[i].type=IOMMU_RESV_RESERVED; of_node_put(n);
  r[i].list.next=h; r[i].list.prev=h->prev; h->prev->next=&r[i].list; h->prev=&r[i].list;
 }
}
static void iommu_put_resv_regions(struct device *d, struct list_head *h) { h->next=h->prev=h; }
#include "iris-production.h"
static const void *of_device_get_match_data(struct device *d) {
 return of_device_is_compatible(d->of_node,"qcom,sm8475-iris-nonpixel")?&iris_nonpixel:&iris_pixel;
}
static void unwind(void) {
 while(actions) { --actions; cleanup[actions](cleanup_data[actions]); }
 links=0; /* Simulated driver-core failed-probe/unbind processing, not tested here. */
 for(int i=0;i<2;i++) { assert(!devices[i].dev.refs); assert(!groups[i].refs); assert(!devices[i].dev.locked); }
 for(int i=0;i<node_count;i++) assert(!nodes[i].refs);
}
static void reset(void) {
 unwind(); missing_device=link_fail=action_fail=mask_fail=omit_reservation=0; available=true;
 for(int i=0;i<2;i++) {
  devices[i].dev.driver=&iris_dma_context_driver.driver;
  devices[i].dev.bound=true; domains[i].type=IOMMU_DOMAIN_DMA;
  devices[i].dev.domain=&domains[i]; devices[i].dev.group=&groups[i];
  devices[i].dev.data=(void *)(i?&iris_pixel:&iris_nonpixel);
 }
}
static int checks;
#define CHECK(x) do { assert(x); checks++; } while(0)
static void setcell(struct device_node *n,const char *p,int index,u32 value) {
 int len; __be32 *v=(__be32 *)of_get_property(n,p,&len); assert(v && len>index*4); v[index]=cpu_to_be32(value);
}
int main(int argc,char **argv) {
 assert(argc==2); FILE *f=fopen(argv[1],"rb"); assert(f); fseek(f,0,SEEK_END); long len=ftell(f); rewind(f);
 blob=malloc(len); assert(fread(blob,1,len,f)==(size_t)len); fclose(f); assert(!fdt_check_header(blob));
 devices[0].dev.of_node=node_at(fdt_path_offset(blob,"/soc@0/dma-nonpixel"));
 devices[1].dev.of_node=node_at(fdt_path_offset(blob,"/soc@0/dma-pixel"));
 struct device controller={.of_node=node_at(fdt_path_offset(blob,"/soc@0/video-codec@aa00000"))};
 struct device *result[2]; reset();
 for(int role=0;role<2;role++) {
  u64 floor=role?0x100000:0x25800000;
  CHECK(iris_dma_range_valid(role,floor,1)); CHECK(!iris_dma_range_valid(role,floor-1,1));
  CHECK(iris_dma_range_valid(role,IRIS_DMA_END-1,1)); CHECK(!iris_dma_range_valid(role,IRIS_DMA_END-1,2));
  CHECK(!iris_dma_range_valid(role,IRIS_DMA_END,1)); CHECK(!iris_dma_range_valid(role,floor,0));
  CHECK(!iris_dma_range_valid(role,floor,UINT64_MAX)); CHECK(!iris_dma_range_valid(role,UINT64_MAX,2));
  CHECK(iris_dma_range_valid(role,floor,IRIS_DMA_END-floor));
  CHECK(iris_dma_probe(&devices[role])==0);
 }
 CHECK(!iris_dma_range_valid(-1,0,1)); CHECK(!iris_dma_range_valid(2,0,1));
 CHECK(iris_dma_contexts_get(&controller,result)==0); CHECK(actions==2 && links==2); reset();
 for(int i=1;i<=2;i++) {
  missing_device=i; CHECK(iris_dma_contexts_get(&controller,result)==-EPROBE_DEFER); reset();
  devices[i-1].dev.bound=false; CHECK(iris_dma_contexts_get(&controller,result)==-EPROBE_DEFER); reset();
  link_fail=i; CHECK(iris_dma_contexts_get(&controller,result)==-ENOMEM); reset();
  action_fail=i; CHECK(iris_dma_contexts_get(&controller,result)==-ENOMEM); reset();
  devices[i-1].dev.data=(void *)(i==1?&iris_pixel:&iris_nonpixel);
  CHECK(iris_dma_contexts_get(&controller,result)==-EINVAL); reset();
 }
 devices[1].dev.domain=devices[0].dev.domain; CHECK(iris_dma_contexts_get(&controller,result)==-EINVAL); reset();
 devices[1].dev.group=devices[0].dev.group; CHECK(iris_dma_contexts_get(&controller,result)==-EINVAL); reset();
 struct device_driver foreign={0}; devices[0].dev.driver=&foreign;
 CHECK(iris_dma_contexts_get(&controller,result)==-EINVAL); reset();
 available=false; CHECK(iris_dma_contexts_get(&controller,result)==-EINVAL); reset();
 const __be32 *cp=of_get_property(controller.of_node,"qcom,dma-consumers",NULL);
 u32 np=be32_to_cpup(cp),px=be32_to_cpup(cp+1);
 setcell(controller.of_node,"qcom,dma-consumers",1,np); CHECK(iris_dma_contexts_get(&controller,result)==-EINVAL);
 setcell(controller.of_node,"qcom,dma-consumers",1,px); reset();
 setcell(controller.of_node,"qcom,dma-consumers",0,px); setcell(controller.of_node,"qcom,dma-consumers",1,np);
 CHECK(iris_dma_contexts_get(&controller,result)==-EINVAL);
 setcell(controller.of_node,"qcom,dma-consumers",0,np); setcell(controller.of_node,"qcom,dma-consumers",1,px); reset();
 CHECK(iris_dma_contexts_get(&controller,result)==0); reset(); /* defer retry */
 struct device *d=&devices[0].dev;
 struct device_node *low=node_at(fdt_path_offset(blob,"/reserved-memory/nonpixel-low"));
 setcell(low,"iommu-addresses",0,px); CHECK(iris_dma_reservation(d,0,0,iris_nonpixel.start)==-EINVAL);
 setcell(low,"iommu-addresses",0,np);
 setcell(low,"iommu-addresses",4,0); CHECK(iris_dma_reservation(d,0,0,iris_nonpixel.start)==-EINVAL);
 setcell(low,"iommu-addresses",4,iris_nonpixel.start);
 struct device_node *soc=node_at(fdt_path_offset(blob,"/soc@0"));
 setcell(soc,"dma-ranges",1,0xf0000000); CHECK(iris_dma_reservation(d,0,0,iris_nonpixel.start)==-EINVAL);
 setcell(soc,"dma-ranges",1,0);
 setcell(soc,"dma-ranges",3,0x1000); CHECK(iris_dma_reservation(d,0,0,iris_nonpixel.start)==-EINVAL);
 setcell(soc,"dma-ranges",3,0);
 struct device_node *high=node_at(fdt_path_offset(blob,"/reserved-memory/nonpixel-high"));
 setcell(high,"iommu-addresses",4,0x1fffffff); CHECK(iris_dma_reservations(d,&iris_nonpixel)==-EINVAL);
 setcell(high,"iommu-addresses",4,0x20000000);
 for(int i=1;i<=2;i++) { omit_reservation=i; CHECK(iris_dma_probe(&devices[0])==-EINVAL); reset(); }
 mask_fail=1; CHECK(iris_dma_probe(&devices[0])==-EIO); reset();
 domains[0].type=0; CHECK(iris_dma_probe(&devices[0])==-EINVAL); reset();
 devices[0].dev.group=NULL; CHECK(iris_dma_probe(&devices[0])==-EINVAL); reset();
 setcell(d->of_node,"iommus",1,0x2181); CHECK(iris_dma_probe(&devices[0])==-EINVAL);
 setcell(d->of_node,"iommus",1,0x2180); reset();
 iris_dma_remove(&devices[0]); CHECK(!devices[0].dev.data); reset();
 CHECK(iris_dma_probe(&devices[0])==0); reset();
 printf("PASS: %d checks; production Iris functions + pinned generic OF translation; provider/core shims\n",checks);
 free(blob); return 0;
}
