#!/usr/bin/env python3
"""Execute extracted production of_iommu_derive_resv_regions() against scoped host mocks.

This does not exercise DT decoding, IOMMU hardware, or oneli at runtime.
"""
import pathlib
import re
import subprocess
import tempfile

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "drivers/iommu/of_iommu.c"

def extract():
    code = SOURCE.read_text()
    m = re.search(r"static void of_iommu_derive_resv_regions\s*\([^;]*?\)\s*\{", code, re.S)
    if not m:
        raise RuntimeError("production helper not found")
    level = 0
    for i in range(m.end() - 1, len(code)):
        if code[i] == "{":
            level += 1
        elif code[i] == "}":
            level -= 1
            if not level:
                return code[m.start():i+1]
    raise RuntimeError("unbalanced helper")

HEAD = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <inttypes.h>
typedef uint64_t phys_addr_t;
typedef uint32_t __be32;
#define PHYS_ADDR_MAX UINT64_MAX
#define IOMMU_READ 1
#define IOMMU_WRITE 2
#define IOMMU_CACHE 4
#define IOMMU_RESV_RESERVED 8
#define GFP_KERNEL 0
#define MAX_INPUT 8
struct device_node { int id; };
struct device { struct device_node *of_node; };
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(name) struct list_head name = { &(name), &(name) }
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
#define list_empty(h) ((h)->next == (h))
static void list_add(struct list_head *n, struct list_head *prev) {
 n->next=prev->next; n->prev=prev; prev->next->prev=n; prev->next=n;
}
static void list_add_tail(struct list_head *n, struct list_head *head) {
 list_add(n,head->prev);
}
static void list_del(struct list_head *n) {
 n->next->prev=n->prev; n->prev->next=n->next;
}
#define list_for_each_entry(pos,head,member) \
 for (struct list_head *_p=(head)->next; _p!=(head) && \
   ((pos)=container_of(_p,__typeof__(*(pos)),member),1); _p=_p->next)
#define list_for_each_entry_safe(pos,n,head,member) \
 for (struct list_head *_p=(head)->next,*_n; _p!=(head) && \
   ((_n=_p->next),(pos)=container_of(_p,__typeof__(*(pos)),member), \
   (n)=_n==(head)?NULL:container_of(_n,__typeof__(*(n)),member),1); _p=_n)
struct iommu_resv_region {
 struct list_head list;
 phys_addr_t start;
 size_t length;
 int prot;
 int type;
};
struct input_tuple { phys_addr_t start; size_t length; int fail_translation; };
static struct input_tuple tuples[MAX_INPUT];
static __be32 map_cells[MAX_INPUT];
static int map_count, missing, coherent, range_fail_at, region_fail_at;
static int range_calls, range_ok, range_frees, region_calls;
static const __be32 *of_get_property(struct device_node *node,const char *name,int *size) {
 (void)node; (void)name;
 if(missing) return NULL;
 *size=map_count*sizeof(__be32);
 return map_cells;
}
static const __be32 *of_translate_dma_region(struct device_node *node,
 const __be32 *ptr, phys_addr_t *start, size_t *length) {
 (void)node;
 unsigned i=*ptr;
 if(i>=MAX_INPUT || tuples[i].fail_translation) return NULL;
 *start=tuples[i].start;
 *length=tuples[i].length;
 return ptr+1;
}
static int of_dma_is_coherent(struct device_node *node) {
 (void)node; return coherent;
}
static void *range_alloc(size_t size) {
 if(++range_calls==range_fail_at) return NULL;
 void *p=calloc(1,size);
 if(p) range_ok++;
 return p;
}
#define kmalloc_obj(obj) range_alloc(sizeof(obj))
static void kfree(void *p) { if(p){range_frees++;free(p);} }
static struct iommu_resv_region *iommu_alloc_resv_region(
 phys_addr_t start,size_t length,int prot,int type,int flags) {
 (void)flags;
 if(++region_calls==region_fail_at) return NULL;
 struct iommu_resv_region *r=calloc(1,sizeof(*r));
 if(!r) return NULL;
 r->start=start; r->length=length; r->prot=prot; r->type=type;
 return r;
}
#define dev_err(...) ((void)0)
"""
TAIL = r"""
static int failures, tested;
#define CHECK(x) do { if(!(x)) { \
 fprintf(stderr,"FAIL %s:%d: %s\n",__FILE__,__LINE__,#x); failures++; \
 } } while(0)
static struct device_node node;
static struct device dev={ .of_node=&node };
static void setup(int count) {
 int i;
 memset(tuples,0,sizeof(tuples));
 map_count=count; missing=coherent=range_fail_at=region_fail_at=0;
 range_calls=range_ok=range_frees=region_calls=0;
 for(i=0;i<count;i++) map_cells[i]=i;
}
static void execute(const char *name, int number,
 const phys_addr_t *starts, const size_t *lengths) {
 LIST_HEAD(list);
 struct list_head *p,*next;
 int count=0;
 (void)name; tested++;
 of_iommu_derive_resv_regions(&dev,&list);
 for(p=list.next;p!=&list;p=next) {
  struct iommu_resv_region *r=container_of(p,struct iommu_resv_region,list);
  next=p->next;
  CHECK(count<number);
  if(count<number) {
   CHECK(r->start==starts[count]);
   CHECK(r->length==lengths[count]);
  }
  CHECK(r->type==IOMMU_RESV_RESERVED);
  CHECK(r->prot==(IOMMU_READ|IOMMU_WRITE|(coherent?IOMMU_CACHE:0)));
  list_del(p); free(r); count++;
 }
 CHECK(count==number);
 CHECK(range_ok==range_frees);
}
int main(void) {
 const phys_addr_t lower_nonpixel[]={0,0xe0000000ULL};
 const size_t sizes_nonpixel[]={0x25800000ULL,UINT64_MAX-0xe0000000ULL+1};
 const phys_addr_t lower_pixel[]={0,0xe0000000ULL};
 const size_t sizes_pixel[]={0x00100000ULL,UINT64_MAX-0xe0000000ULL+1};
 const phys_addr_t tail[]={0xe0000000ULL};
 const size_t tail_size[]={UINT64_MAX-0xe0000000ULL+1};
 const phys_addr_t front[]={0};
 const size_t front_size[]={0x25800000ULL};
 setup(0); missing=1; execute("missing property",0,NULL,NULL);
 setup(1); tuples[0]=(struct input_tuple){0x25800000ULL,0xba800000ULL,0};
 execute("nonpixel window",2,lower_nonpixel,sizes_nonpixel);
 setup(1); tuples[0]=(struct input_tuple){0x100000ULL,0xdff00000ULL,0};
 execute("pixel window",2,lower_pixel,sizes_pixel);
 setup(1); tuples[0]=(struct input_tuple){0x25800000ULL,0xba800000ULL,0};
 coherent=1; execute("coherent",2,lower_nonpixel,sizes_nonpixel);
 setup(1); tuples[0]=(struct input_tuple){0x25800000ULL,0,0};
 execute("zero length skipped",0,NULL,NULL);
 setup(1); tuples[0]=(struct input_tuple){UINT64_MAX-1,8,0};
 execute("overflow skipped",0,NULL,NULL);
 setup(1); tuples[0].fail_translation=1;
 execute("translation fails",0,NULL,NULL);
 setup(1); tuples[0]=(struct input_tuple){0x25800000ULL,0xba800000ULL,0};
 range_fail_at=1; execute("range allocation fails",0,NULL,NULL);
 setup(1); tuples[0]=(struct input_tuple){0x25800000ULL,0xba800000ULL,0};
 region_fail_at=1; execute("first reservation allocation fails",1,tail,tail_size);
 setup(1); tuples[0]=(struct input_tuple){0x25800000ULL,0xba800000ULL,0};
 region_fail_at=2; execute("second reservation allocation fails",1,front,front_size);
 setup(2); tuples[0]=(struct input_tuple){0x25800000ULL,0xba800000ULL,0};
 tuples[1].fail_translation=1;
 execute("translation after valid range",2,lower_nonpixel,sizes_nonpixel);
 setup(2); tuples[0]=(struct input_tuple){0x25800000ULL,0xba800000ULL,0};
 tuples[1]=(struct input_tuple){0x100000ULL,0xdff00000ULL,0};
 /* union becomes [0x100000,0xe0000000), including the lower range */
 execute("unsorted overlapping ranges",2,lower_pixel,sizes_pixel);
 printf("IOMMU extracted production reservation tests: %d cases, %s\n",
  tested,failures?"FAIL":"PASS");
 return failures?1:0;
}
"""
def main():
    with tempfile.TemporaryDirectory() as tmp:
        src=pathlib.Path(tmp)/"of_ranges.c"
        binary=pathlib.Path(tmp)/"of_ranges"
        src.write_text(HEAD+"\n"+extract()+"\n"+TAIL)
        subprocess.run(["cc","-std=gnu11","-Wall","-Wextra","-Werror",str(src),"-o",str(binary)],check=True)
        subprocess.run([str(binary)],check=True)
if __name__=="__main__":
    main()
