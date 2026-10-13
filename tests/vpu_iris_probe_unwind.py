#!/usr/bin/env python3
"""Execute production iris_probe()/iris_remove() error paths using scoped host mocks.

The actual functions, plus child init/create/deinit, are extracted unchanged.
Mocks cover only external acquisition, V4L2, and runtime-PM interfaces.
"""
import pathlib
import re
import subprocess
import tempfile
import vpu_iris_cb_lifecycle as child

CORE=r"""struct iris_core {
 struct device *dev;
 struct iris_platform_data *iris_platform_data;
 struct device *np_dev, *p_dev;
 int state, lock, core_init_done, sys_error_handler, irq;
 void *response_packet,*reg_base,*ubwc_cfg;
 int instances, v4l2_dev;
 struct video_device *vdev_dec,*vdev_enc;
};"""
header=re.sub(r"struct iris_core \{.*?\};",CORE,child.HEADER,count=1,flags=re.S)
header=header.replace("#include <stdint.h>","#include <stdint.h>\n#include <errno.h>\ntypedef uint64_t u64;")
mocks=r"""
#define IRIS_CORE_DEINIT 0
#define IFACEQ_CORE_PKT_SIZE 128
#define IRQF_TRIGGER_HIGH 1
#define IRQF_NO_AUTOEN 2
#define AUTOSUSPEND_DELAY_VALUE 2000
#define GFP_KERNEL 0
#define DECODER 1
#define ENCODER 2
enum domain_type { decoder=DECODER, encoder=ENCODER };
struct video_device { int registered; };
static struct video_device decoder_dev,encoder_dev;
static struct iris_core *active_core;
static struct iris_platform_data platform_data={ .dma_mask=DMA_BIT_MASK(32) };
static int fail_step, sm8475, v4l2_regs, v4l2_unregs;
static int video_regs, video_unregs, pm_enables, core_deinits;
static void *devm_kzalloc(struct device *dev,size_t sz,int flags) {
 (void)dev; (void)flags; return calloc(1,sz);
}
#define mutex_init(p) ((void)(p))
#define mutex_destroy(p) ((void)(p))
#define init_completion(p) ((void)(p))
#define INIT_LIST_HEAD(p) ((void)(p))
#define INIT_DELAYED_WORK(p,fn) ((void)(p))
static void *devm_platform_ioremap_resource(struct platform_device *pdev,int index) {
 (void)pdev;(void)index;return &platform_data;
}
static int platform_get_irq(struct platform_device *pdev,int index) {
 (void)pdev;(void)index;return 5;
}
static struct iris_platform_data *of_device_get_match_data(struct device *dev) {
 (void)dev;return &platform_data;
}
static void *qcom_ubwc_config_get_data(void) { return &platform_data; }
#define devm_request_threaded_irq(...) (0)
static void iris_init_ops(struct iris_core *core) { (void)core; }
static int iris_init_resources(struct iris_core *core) { (void)core;return 0; }
static int v4l2_device_register(struct device *dev,int *v) {
 (void)dev;(void)v;
 if(fail_step==1)return -EIO;
 v4l2_regs++;
 return 0;
}
static void v4l2_device_unregister(int *v) { (void)v;v4l2_unregs++; }
static int iris_register_video_device(struct iris_core *core,enum domain_type domain) {
 if(domain==DECODER) {
  if(fail_step==2)return -EIO;
  core->vdev_dec=&decoder_dev;
 } else {
  if(fail_step==3)return -EIO;
  core->vdev_enc=&encoder_dev;
 }
 video_regs++;
 return 0;
}
static void video_unregister_device(struct video_device *video) {
 if(!video)abort();
 video_unregs++;
}
static void platform_set_drvdata(struct platform_device *pdev,struct iris_core *core) {
 (void)pdev; active_core=core;
}
static struct iris_core *platform_get_drvdata(struct platform_device *pdev) {
 (void)pdev;return active_core;
}
static int device_iommu_mapped(struct device *dev) {
 (void)dev;return fail_step==4;
}
static int dma_set_mask_and_coherent(struct device *dev,unsigned long long mask) {
 (void)dev;(void)mask;return fail_step==4?-EIO:0;
}
static void pm_runtime_set_autosuspend_delay(struct device *dev,int delay) {
 (void)dev;(void)delay;
}
static void pm_runtime_use_autosuspend(struct device *dev) {(void)dev;}
static int devm_pm_runtime_enable(struct device *dev) {
 (void)dev;pm_enables++;return fail_step==5?-EIO:0;
}
static void iris_core_deinit(struct iris_core *core) { (void)core;core_deinits++; }
static int of_device_is_compatible(struct device_node *node,const char *compatible) {
 (void)node;(void)compatible;return sm8475;
}
#define dev_err_probe(dev,code,fmt,...) (code)
"""
tests=r"""
static int failures, scenarios;
#define CHECK(x) do { if(!(x)){ \
 fprintf(stderr,"FAIL %s:%d: %s\n",__FILE__,__LINE__,#x);failures++; \
 } } while(0)
static void run_case(const char *title,int target_step,int np,int pixel,int fail_child,
                     int fail_closed,int expected_rc,int expected_registered) {
 struct platform_device pdev = { .dev={.name="iris",.of_node=&root_node} };
 int ret;
 (void)title;scenarios++;
 memset(created,0,sizeof(created));
 memset(nodes,0,sizeof(nodes));
 nodes[0].name="non-pixel";nodes[1].name="pixel";
 present[0]=np;present[1]=pixel;
 fail_at=fail_child;call_index=registered=unregistered=0;
 bad_unreg=put_count=get_count=0;
 fail_step=target_step;sm8475=fail_closed;
 v4l2_regs=v4l2_unregs=video_regs=video_unregs=pm_enables=core_deinits=0;
 active_core=NULL;
 ret=iris_probe(&pdev);
 CHECK(ret==expected_rc);
 CHECK(registered==expected_registered);
 CHECK(nodes[0].refs==0 && nodes[1].refs==0);
 CHECK(get_count==put_count);
 CHECK(!bad_unreg);
 if(!ret) {
  CHECK(active_core!=NULL);
  iris_remove(&pdev);
 }
 CHECK(registered==unregistered);
 CHECK(video_regs==video_unregs);
 CHECK(v4l2_regs==v4l2_unregs);
 CHECK(!bad_unreg);
}
int main(void) {
 run_case("ordinary success",0,1,1,0,0,0,2);
 run_case("legacy absent",0,0,0,0,0,0,0);
 run_case("first child fails",0,1,1,1,0,-ENOMEM,0);
 run_case("second child fails",0,1,1,2,0,-ENOMEM,1);
 run_case("v4l2 device registration failure",1,1,1,0,0,-EIO,2);
 run_case("decoder registration failure",2,1,1,0,0,-EIO,2);
 run_case("encoder registration failure",3,1,1,0,0,-EIO,2);
 run_case("parent DMA mask failure",4,1,1,0,0,-EIO,2);
 run_case("pm runtime enable failure",5,1,1,0,0,-EIO,2);
 run_case("SM8475 fail closed",0,1,1,0,1,-EOPNOTSUPP,0);
 printf("Iris production probe/unwind: %d scenarios, %s\n",
        scenarios,failures?"FAIL":"PASS");
 return failures?1:0;
}
"""
def main():
    names=["iris_create_cb_dev","iris_init_cb_devs","iris_deinit_cb_devs",
           "iris_remove","iris_probe"]
    code=header+mocks+"\n".join(child.extract(name) for name in names)+tests
    with tempfile.TemporaryDirectory() as t:
        src=pathlib.Path(t)/"probe_unwind.c"
        binary=pathlib.Path(t)/"probe_unwind"
        src.write_text(code)
        subprocess.run(["cc","-std=gnu11","-Wall","-Wextra","-Werror",
                        str(src),"-o",str(binary)],check=True)
        subprocess.run([str(binary)],check=True)
if __name__=="__main__":main()
