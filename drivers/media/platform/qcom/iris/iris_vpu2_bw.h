/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __IRIS_VPU2_BW_H__
#define __IRIS_VPU2_BW_H__

#include <linux/types.h>

enum iris_vpu2_bw_codec {
	IRIS_VPU2_BW_H264,
	IRIS_VPU2_BW_HEVC,
	IRIS_VPU2_BW_VP9,
};

enum iris_vpu2_bw_format {
	IRIS_VPU2_BW_NV12,
	IRIS_VPU2_BW_P010,
};

/* Offline model inputs, not a new V4L2 or firmware ABI. */
struct iris_vpu2_bw_input {
	enum iris_vpu2_bw_codec codec;
	enum iris_vpu2_bw_format format;
	u32 width;
	u32 height;
	u32 output_width;
	u32 output_height;
	u32 frame_rate;
	u32 operating_rate;
	u64 queued_bytes;
};

struct iris_vpu2_bw_output {
	u64 ddr_kbps;
	u64 llcc_kbps;
};

/*
 * Pure, unwired no-cache-discount reference model: CR=1, CF=4, four pipes,
 * split linear output. Rate=max(frame_rate, operating_rate), including 480
 * startup. Candidate admission and route-cap adequacy are caller concerns;
 * neither a zero queued size nor a successful result is an ICC unvote.
 *
 * Returns -EINVAL for invalid/model-unsupported inputs, -ERANGE for arithmetic
 * that cannot be represented in u64 Q16. Any non-NULL output is zero on error.
 * Success returns raw decimal kB/s, without a route clamp. No cache lifecycle,
 * performance policy or physical LLCC adequacy is established by this model.
 */
int iris_vpu2_calc_bw(const struct iris_vpu2_bw_input *input,
		      struct iris_vpu2_bw_output *output);

#endif
