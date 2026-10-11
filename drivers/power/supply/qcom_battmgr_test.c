// SPDX-License-Identifier: GPL-2.0-only
/*
 * Qualcomm PMIC GLINK battery manager conversion tests.
 * Exercises the exact decoder called by qcom_battmgr_sm8350_callback().
 */
#include <kunit/test.h>
#include <linux/module.h>
#include <linux/types.h>

#include "qcom_battmgr_internal.h"

static void qcom_battmgr_temp_positive_test(struct kunit *test)
{
	KUNIT_EXPECT_EQ(test, qcom_battmgr_decode_temperature(cpu_to_le32(253)), 25);
	KUNIT_EXPECT_EQ(test, qcom_battmgr_decode_temperature(cpu_to_le32(5)), 1);
	KUNIT_EXPECT_EQ(test, qcom_battmgr_decode_temperature(cpu_to_le32(4)), 0);
}

static void qcom_battmgr_temp_negative_test(struct kunit *test)
{
	KUNIT_EXPECT_EQ(test,
			qcom_battmgr_decode_temperature(cpu_to_le32((u32)-201)), -20);
	KUNIT_EXPECT_EQ(test,
			qcom_battmgr_decode_temperature(cpu_to_le32((u32)-5)), -1);
	KUNIT_EXPECT_EQ(test,
			qcom_battmgr_decode_temperature(cpu_to_le32((u32)-4)), 0);
}

static void qcom_battmgr_temp_zero_test(struct kunit *test)
{
	KUNIT_EXPECT_EQ(test, qcom_battmgr_decode_temperature(cpu_to_le32(0)), 0);
	KUNIT_EXPECT_EQ(test, qcom_battmgr_decode_temperature(cpu_to_le32(250)), 25);
}

static struct kunit_case qcom_battmgr_test_cases[] = {
	KUNIT_CASE(qcom_battmgr_temp_positive_test),
	KUNIT_CASE(qcom_battmgr_temp_negative_test),
	KUNIT_CASE(qcom_battmgr_temp_zero_test),
	{}
};

static struct kunit_suite qcom_battmgr_test_suite = {
	.name = "qcom-battmgr-temperature",
	.test_cases = qcom_battmgr_test_cases,
};

kunit_test_suite(qcom_battmgr_test_suite);

MODULE_LICENSE("GPL");
