/* SPDX-License-Identifier: GPL-2.0-only */
/* Local checked arithmetic adaptation; no platform or workload policy here. */
#ifndef __IRIS_VPU2_BW_MATH_H__
#define __IRIS_VPU2_BW_MATH_H__

#include <linux/errno.h>
#include <linux/limits.h>
#include <linux/math64.h>
#include <linux/overflow.h>
#include <linux/types.h>

#define IRIS_BW_Q16_ONE 65536ULL

static inline int iris_bw_add(u64 a, u64 b, u64 *out)
{
	*out = 0;
	if (check_add_overflow(a, b, out)) {
		*out = 0;
		return -ERANGE;
	}
	return 0;
}

static inline int iris_bw_mul(u64 a, u64 b, u64 *out)
{
	*out = 0;
	if (check_mul_overflow(a, b, out)) {
		*out = 0;
		return -ERANGE;
	}
	return 0;
}

static inline int iris_bw_q16(u64 value, u64 *out)
{
	*out = 0;
	if (value > (U64_MAX >> 16))
		return -ERANGE;
	*out = value << 16;
	return 0;
}

static inline int iris_bw_qmul(u64 a, u64 b, u64 *out)
{
	u64 product;
	int ret;

	*out = 0;
	ret = iris_bw_mul(a, b, &product);
	if (ret)
		return ret;
	*out = product >> 16;
	return 0;
}

static inline int iris_bw_qdiv(u64 a, u64 b, u64 *out)
{
	u64 numerator;
	int ret;

	*out = 0;
	if (!b)
		return -EINVAL;
	ret = iris_bw_q16(a, &numerator);
	if (ret)
		return ret;
	*out = div64_u64(numerator, b);
	return 0;
}

static inline int iris_bw_round(u64 value, u64 *out)
{
	/* Preserve Qualcomm fp_round's threshold, 65535 / 2 == 32767. */
	return iris_bw_add(value >> 16, (value & 65535) >= 32767, out);
}

static inline int iris_bw_kbps(u64 mbps, u64 *out)
{
	return iris_bw_mul(mbps, 1000, out);
}

#endif
