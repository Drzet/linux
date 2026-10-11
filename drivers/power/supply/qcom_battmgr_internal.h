/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Qualcomm battery manager PMIC GLINK wire-value conversion helpers.
 * The same helper is used by the production callback and KUnit tests.
 */
#ifndef _QCOM_BATTMGR_INTERNAL_H
#define _QCOM_BATTMGR_INTERNAL_H

#include <linux/byteorder/generic.h>
#include <linux/kernel.h>
#include <linux/types.h>

static inline int qcom_battmgr_decode_temperature(__le32 raw)
{
	/*
	 * Firmware returns temperature in hundredths of a degree Celsius;
	 * power_supply expects tenths of a degree. The 32-bit wire value is
	 * signed and negative temperatures must keep their sign.
	 */
	return DIV_ROUND_CLOSEST((s32)le32_to_cpu(raw), 10);
}

#endif /* _QCOM_BATTMGR_INTERNAL_H */
