/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __IRIS_DMA_CONTEXT_H__
#define __IRIS_DMA_CONTEXT_H__

#include <linux/types.h>

struct device;
struct platform_driver;

enum iris_dma_role {
	IRIS_DMA_NONPIXEL,
	IRIS_DMA_PIXEL,
	IRIS_DMA_ROLE_COUNT,
};

extern struct platform_driver iris_dma_context_driver;
int iris_dma_contexts_get(struct device *dev, struct device **contexts);
bool iris_dma_range_valid(enum iris_dma_role role, u64 address, u64 size);

#endif
