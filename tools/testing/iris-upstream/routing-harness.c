// SPDX-License-Identifier: GPL-2.0-only
/* Production iris_get_cb_dev() included verbatim by routing-test.py. */
#include <assert.h>
#include <stdio.h>

struct device { int id; };
struct iris_core {
	struct device *dev;
	struct device *np_dev;
	struct device *p_dev;
};
enum domain_type { DECODER, ENCODER };
struct iris_inst {
	struct iris_core *core;
	enum domain_type domain;
};
#define dev_err(...) ((void)0)
#include "production-routing.h"

static unsigned int checked;

static void check(struct iris_inst *inst, enum iris_buffer_type type,
		  struct device *expected)
{
	assert(iris_get_cb_dev(inst, type) == expected);
	checked++;
}

int main(void)
{
	struct device parent = { 0 }, nonpixel = { 1 }, pixel = { 2 };
	struct iris_core core = { .dev = &parent, .np_dev = &nonpixel,
				  .p_dev = &pixel };
	struct iris_inst decoder = { .core = &core, .domain = DECODER };
	struct iris_inst encoder = { .core = &core, .domain = ENCODER };
	enum iris_buffer_type pix_internal[] = { BUF_DPB, BUF_PARTIAL,
		BUF_SCRATCH_1, BUF_SCRATCH_2, BUF_VPSS };
	enum iris_buffer_type nonpix_internal[] = { BUF_BIN, BUF_ARP,
		BUF_COMV, BUF_LINE, BUF_NON_COMV, BUF_PERSIST };
	unsigned int i;

	check(&decoder, BUF_INPUT, &nonpixel);
	check(&decoder, BUF_OUTPUT, &pixel);
	check(&encoder, BUF_INPUT, &pixel);
	check(&encoder, BUF_OUTPUT, &nonpixel);
	for (i = 0; i < sizeof(pix_internal) / sizeof(*pix_internal); i++) {
		check(&decoder, pix_internal[i], &pixel);
		check(&encoder, pix_internal[i], &pixel);
	}
	for (i = 0; i < sizeof(nonpix_internal) / sizeof(*nonpix_internal); i++) {
		check(&decoder, nonpix_internal[i], &nonpixel);
		check(&encoder, nonpix_internal[i], &nonpixel);
	}
	check(&decoder, BUF_TYPE_MAX, &parent);
	/* Platforms without these subnodes retain original parent DMA behavior. */
	core.np_dev = NULL;
	core.p_dev = NULL;
	for (int t = BUF_INPUT; t < BUF_TYPE_MAX; t++) {
		check(&decoder, t, &parent);
		check(&encoder, t, &parent);
	}
	printf("PASS %u real production routing checks\n", checked);
	return 0;
}
