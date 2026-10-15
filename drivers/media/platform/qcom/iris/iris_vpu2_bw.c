// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2020-2021, The Linux Foundation. All rights reserved.
 *
 * Decoder model derived from __calculate_decoder() in:
 * LowTension/android_kernel_xiaomi_sm8475@39382bf760ccf05a8fb4dddee7138ecac02d4a59
 * techpack/video/driver/variant/iris2/src/msm_vidc_power_iris2.c
 * SHA256 a1a76266d723a855ec0f9f404ec4f5d04b88c7f6194a97945fb67782e1e4b0d0
 *
 * This local adaptation selects only CR=1, CF=4, four-pipe, split linear
 * decode without cache discounts. Checked u64 Q16 primitives live separately
 * in iris_vpu2_bw_math.h. Keep source operation order/truncation, including
 * otherwise redundant division by Q16 one; no cancellation or saturation.
 * The empirical coefficients are vendor policy, not SM8475 characterization.
 */

#include "iris_vpu2_bw.h"
#include "iris_vpu2_bw_math.h"

/* Model restrictions only. Hardware admission is deliberately not applied. */
static int iris_vpu2_bw_validate(const struct iris_vpu2_bw_input *in)
{
	u32 align;

	if (in->codec != IRIS_VPU2_BW_H264 &&
	    in->codec != IRIS_VPU2_BW_HEVC && in->codec != IRIS_VPU2_BW_VP9)
		return -EINVAL;
	if (in->format != IRIS_VPU2_BW_NV12 && in->format != IRIS_VPU2_BW_P010)
		return -EINVAL;
	if (in->codec == IRIS_VPU2_BW_H264 && in->format != IRIS_VPU2_BW_NV12)
		return -EINVAL;
	if (in->width < 96 || in->width > 8192 ||
	    in->height < 96 || in->height > 8192)
		return -EINVAL;
	align = in->codec == IRIS_VPU2_BW_HEVC ? 32 : 16;
	if (in->width % align || in->height % align ||
	    in->output_width != ((in->width + 127) / 128) * 128 ||
	    in->output_height != ((in->height + 31) / 32) * 32)
		return -EINVAL;
	if (in->frame_rate < 30 || in->frame_rate > 480 || in->operating_rate > 480)
		return -EINVAL;
	return 0;
}

int iris_vpu2_calc_bw(const struct iris_vpu2_bw_input *in,
		      struct iris_vpu2_bw_output *out)
{
	u64 width, height, fps, lcu, count, bitrate, ratio, y8, y10, p010;
	u64 vsp_read, vsp_write, collocated, dpb_read, dpb_write, opb, line;
	u64 total, value, a, b, factor, kbps;
	int ret;

	if (!out)
		return -EINVAL;
	*out = (struct iris_vpu2_bw_output){};
	if (!in)
		return -EINVAL;
	ret = iris_vpu2_bw_validate(in);
	if (ret)
		return ret;
	width = in->width > 1280 ? in->width : 1280;
	height = in->height > 720 ? in->height : 720;
	fps = in->frame_rate > in->operating_rate ? in->frame_rate : in->operating_rate;
	lcu = in->codec == IRIS_VPU2_BW_H264 ? 16 : 32;

	/* Original (unfloored) dimensions determine DPB/OPB area ratio. */
	ret = iris_bw_mul((u64)in->width, in->height, &value);
	if (ret)
		return ret;
	ret = iris_bw_q16(value, &a);
	if (ret)
		return ret;
	ret = iris_bw_mul((u64)in->output_width, in->output_height, &value);
	if (ret)
		return ret;
	ret = iris_bw_q16(value, &b);
	if (ret)
		return ret;
	ret = iris_bw_qdiv(a, b, &ratio);
	if (ret)
		return ret;

	ret = iris_bw_mul((width + lcu - 1) / lcu,
			  (height + lcu - 1) / lcu, &count);
	if (ret)
		return ret;
	ret = iris_bw_mul(in->queued_bytes, fps, &value);
	if (ret)
		return ret;
	ret = iris_bw_mul(value, 8, &value);
	if (ret)
		return ret;
	ret = iris_bw_add(value, 1000000, &value);
	if (ret)
		return ret;
	value--; /* Positive after the checked addition; preserve n + d - 1. */
	bitrate = div64_u64(value, 1000000);
	ret = iris_bw_q16(bitrate, &a);
	if (ret)
		return ret;
	ret = iris_bw_qmul(a, 6 * IRIS_BW_Q16_ONE, &b);
	if (ret)
		return ret;
	ret = iris_bw_qdiv(b, 8 * IRIS_BW_Q16_ONE, &vsp_read);
	if (ret)
		return ret;
	ret = iris_bw_qmul(a, 4 * IRIS_BW_Q16_ONE, &b);
	if (ret)
		return ret;
	ret = iris_bw_qdiv(b, 8 * IRIS_BW_Q16_ONE, &vsp_write);
	if (ret)
		return ret;

	ret = iris_bw_mul(count, lcu == 16 ? 16 : 64, &value);
	if (ret)
		return ret;
	ret = iris_bw_mul(value, fps, &value);
	if (ret)
		return ret;
	ret = iris_bw_q16(value, &a);
	if (ret)
		return ret;
	ret = iris_bw_qdiv(a, 1000000 * IRIS_BW_Q16_ONE, &collocated);
	if (ret)
		return ret;
	ret = iris_bw_mul(width, height, &value);
	if (ret)
		return ret;
	ret = iris_bw_mul(value, fps, &value);
	if (ret)
		return ret;
	ret = iris_bw_q16(value, &a);
	if (ret)
		return ret;
	ret = iris_bw_qdiv(a, 1000000 * IRIS_BW_Q16_ONE, &y8);
	if (ret)
		return ret;
	y10 = 0;
	p010 = 0;
	if (in->format == IRIS_VPU2_BW_P010) {
		ret = iris_bw_qmul(y8, 256 * IRIS_BW_Q16_ONE, &a);
		if (ret)
			return ret;
		ret = iris_bw_qdiv(a, 192 * IRIS_BW_Q16_ONE, &y10);
		if (ret)
			return ret;
		ret = iris_bw_mul(y8, 2, &p010);
		if (ret)
			return ret;
	}

	/* Keep nested source factors and each rounding point. CR=1, CF=4. */
	value = in->format == IRIS_VPU2_BW_NV12 ? y8 : y10;
	ret = iris_bw_qmul(IRIS_BW_Q16_ONE + 32768, 4 * IRIS_BW_Q16_ONE, &factor);
	if (ret)
		return ret;
	ret = iris_bw_qmul(value, factor, &a);
	if (ret)
		return ret;
	ret = iris_bw_qdiv(a, IRIS_BW_Q16_ONE, &dpb_read);
	if (ret)
		return ret;
	ret = iris_bw_qmul(IRIS_BW_Q16_ONE + 32768,
			   IRIS_BW_Q16_ONE + ((5 * IRIS_BW_Q16_ONE) / 100), &factor);
	if (ret)
		return ret;
	ret = iris_bw_qmul(value, factor, &a);
	if (ret)
		return ret;
	ret = iris_bw_qdiv(a, IRIS_BW_Q16_ONE, &dpb_write);
	if (ret)
		return ret;
	value = in->format == IRIS_VPU2_BW_NV12 ? y8 : p010;
	ret = iris_bw_qmul(IRIS_BW_Q16_ONE + 32768, value, &a);
	if (ret)
		return ret;
	ret = iris_bw_qmul(ratio, IRIS_BW_Q16_ONE, &b);
	if (ret)
		return ret;
	ret = iris_bw_qdiv(a, b, &opb);
	if (ret)
		return ret;
	ret = iris_bw_mul(lcu == 16 ? 128 : 64, count, &value);
	if (ret)
		return ret;
	ret = iris_bw_mul(value, fps, &value);
	if (ret)
		return ret;
	ret = iris_bw_q16(value, &a);
	if (ret)
		return ret;
	ret = iris_bw_qdiv(a, 1000000 * IRIS_BW_Q16_ONE, &line);
	if (ret)
		return ret;

	/* Source left-to-right addition order; opb_read is zero. */
	ret = iris_bw_add(vsp_read, vsp_write, &total);
	if (ret)
		return ret;
	ret = iris_bw_add(total, collocated, &total);
	if (ret)
		return ret;
	ret = iris_bw_add(total, collocated, &total);
	if (ret)
		return ret;
	ret = iris_bw_add(total, dpb_read, &total);
	if (ret)
		return ret;
	ret = iris_bw_add(total, dpb_write, &total);
	if (ret)
		return ret;
	ret = iris_bw_add(total, opb, &total);
	if (ret)
		return ret;
	ret = iris_bw_add(total, line, &total);
	if (ret)
		return ret;
	ret = iris_bw_add(total, line, &total);
	if (ret)
		return ret;
	ret = iris_bw_qmul(total, IRIS_BW_Q16_ONE + ((3 * IRIS_BW_Q16_ONE) / 100), &a);
	if (ret)
		return ret;
	ret = iris_bw_round(a, &value);
	if (ret)
		return ret;
	ret = iris_bw_kbps(value, &kbps);
	if (ret)
		return ret;
	/* Cache-discount-off makes the two source totals identical. */
	out->ddr_kbps = kbps;
	out->llcc_kbps = kbps;
	return 0;
}
