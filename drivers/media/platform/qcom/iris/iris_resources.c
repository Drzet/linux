// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2022-2024 Qualcomm Innovation Center, Inc. All rights reserved.
 */

#include <dt-bindings/power/qcom,rpmhpd.h>
#include <linux/clk.h>
#include <linux/devfreq.h>
#include <linux/interconnect.h>
#include <linux/pm_domain.h>
#include <linux/pm_opp.h>
#include <linux/pm_runtime.h>
#include <linux/reset.h>

#include "iris_core.h"
#include "iris_instance.h"
#include "iris_resources.h"

#define BW_THRESHOLD 50000

int iris_set_icc_bw(struct iris_core *core, unsigned long icc_bw)
{
	unsigned long bw_kbps = 0, bw_prev = 0;
	const struct icc_info *icc_tbl;
	int ret = 0, i;

	icc_tbl = core->iris_platform_data->icc_tbl;

	for (i = 0; i < core->icc_count; i++) {
		if (!strcmp(core->icc_tbl[i].name, "video-mem")) {
			bw_kbps = icc_bw;
			bw_prev = core->power.icc_bw;

			bw_kbps = clamp_t(typeof(bw_kbps), bw_kbps,
					  icc_tbl[i].bw_min_kbps, icc_tbl[i].bw_max_kbps);

			if (abs(bw_kbps - bw_prev) < BW_THRESHOLD && bw_prev)
				return ret;

			core->icc_tbl[i].avg_bw = bw_kbps;

			core->power.icc_bw = bw_kbps;
			break;
		}
	}

	return icc_bulk_set_bw(core->icc_count, core->icc_tbl);
}

int iris_unset_icc_bw(struct iris_core *core)
{
	u32 i;

	core->power.icc_bw = 0;

	for (i = 0; i < core->icc_count; i++) {
		core->icc_tbl[i].avg_bw = 0;
		core->icc_tbl[i].peak_bw = 0;
	}

	return icc_bulk_set_bw(core->icc_count, core->icc_tbl);
}

int iris_opp_set_rate(struct device *dev, unsigned long freq)
{
	struct dev_pm_opp *opp __free(put_opp) =
		devfreq_recommended_opp(dev, &freq, 0);

	if (IS_ERR(opp))
		return PTR_ERR(opp);

	return dev_pm_opp_set_opp(dev, opp);
}

int iris_enable_power_domains(struct iris_core *core, struct device *pd_dev)
{
	int ret;

	ret = iris_sm8475_policy(core) ? iris_sm8475_boot_opp(core) :
		iris_opp_set_rate(core->dev, ULONG_MAX);
	if (ret)
		return ret;

	return pm_runtime_resume_and_get(pd_dev);
}

int iris_disable_power_domains(struct iris_core *core, struct device *pd_dev)
{
	int ret;
	int pm_ret;

	ret = iris_sm8475_policy(core) ? 0 : iris_opp_set_rate(core->dev, 0);

	pm_ret = pm_runtime_put_sync(pd_dev);
	if (iris_sm8475_policy(core) && pm_ret < 0)
		iris_sm8475_fault(core, pm_ret);
	if (!ret)
		ret = pm_ret;

	return ret;
}

static struct clk *iris_get_clk_by_type(struct iris_core *core, enum platform_clk_type clk_type)
{
	const struct platform_clk_data *clk_tbl;
	u32 clk_cnt, i, j;

	clk_tbl = core->iris_platform_data->clk_tbl;
	clk_cnt = core->iris_platform_data->clk_tbl_size;

	for (i = 0; i < clk_cnt; i++) {
		if (clk_tbl[i].clk_type == clk_type) {
			for (j = 0; core->clock_tbl && j < core->clk_count; j++) {
				if (!strcmp(core->clock_tbl[j].id, clk_tbl[i].clk_name))
					return core->clock_tbl[j].clk;
			}
		}
	}

	return NULL;
}

int iris_prepare_enable_clock(struct iris_core *core, enum platform_clk_type clk_type)
{
	struct clk *clock;

	clock = iris_get_clk_by_type(core, clk_type);
	if (!clock)
		return -ENOENT;

	return clk_prepare_enable(clock);
}

int iris_disable_unprepare_clock(struct iris_core *core, enum platform_clk_type clk_type)
{
	struct clk *clock;

	clock = iris_get_clk_by_type(core, clk_type);
	if (!clock)
		return -EINVAL;

	clk_disable_unprepare(clock);

	return 0;
}

struct device *iris_get_cb_dev(struct iris_inst *inst, enum iris_buffer_type buffer_type)
{
	struct iris_core *core = inst->core;
	struct device *dev = NULL;

	switch (buffer_type) {
	case BUF_INPUT:
		if (inst->domain == DECODER)
			dev = core->np_dev;
		else
			dev = core->p_dev;
		break;
	case BUF_OUTPUT:
		if (inst->domain == DECODER)
			dev = core->p_dev;
		else
			dev = core->np_dev;
		break;
	case BUF_DPB:
	case BUF_PARTIAL:
	case BUF_SCRATCH_1:
	case BUF_SCRATCH_2:
	case BUF_VPSS:
		dev = core->p_dev;
		break;
	case BUF_BIN:
	case BUF_ARP:
	case BUF_COMV:
	case BUF_LINE:
	case BUF_NON_COMV:
	case BUF_PERSIST:
		dev = core->np_dev;
		break;
	default:
		dev_err(core->dev, "invalid buffer type: %d\n", buffer_type);
	}

	return dev ? dev : core->dev;
}

/* Iris order is MXC, MMCX (the VIDEOCC consumer order is different). */
static const struct {
	unsigned long hz;
	u32 mxc;
	u32 mmcx;
} iris_sm8475_rows[] = {
	{ 240000000, RPMH_REGULATOR_LEVEL_SVS, RPMH_REGULATOR_LEVEL_LOW_SVS },
	{ 338000000, RPMH_REGULATOR_LEVEL_SVS, RPMH_REGULATOR_LEVEL_SVS },
	{ 366000000, RPMH_REGULATOR_LEVEL_SVS_L1, RPMH_REGULATOR_LEVEL_SVS_L1 },
	{ 444000000, RPMH_REGULATOR_LEVEL_NOM, RPMH_REGULATOR_LEVEL_NOM },
};

static struct icc_bulk_data *iris_sm8475_path(struct iris_core *core, const char *name)
{
	u32 i;

	for (i = 0; i < core->icc_count; i++)
		if (!strcmp(core->icc_tbl[i].name, name))
			return &core->icc_tbl[i];
	return NULL;
}

static int iris_sm8475_icc(struct iris_core *core, const char *name, u64 bw)
{
	struct icc_bulk_data *path = iris_sm8475_path(core, name);

	if (!path || IS_ERR_OR_NULL(path->path))
		return -ENODEV;
	if (bw > U32_MAX)
		return -ERANGE;
	return icc_set_bw(path->path, (u32)bw, 0);
}

static int iris_sm8475_check_opp(struct dev_pm_opp *opp, unsigned long freq)
{
	u32 i;

	for (i = 0; i < ARRAY_SIZE(iris_sm8475_rows); i++) {
		if (iris_sm8475_rows[i].hz != freq)
			continue;
		if (dev_pm_opp_get_required_pstate(opp, 0) != iris_sm8475_rows[i].mxc ||
		    dev_pm_opp_get_required_pstate(opp, 1) != iris_sm8475_rows[i].mmcx)
			return -EINVAL;
		return 0;
	}
	return -ERANGE;
}

int iris_sm8475_select_opp(struct iris_core *core, struct iris_resource_vector *vec)
{
	struct clk *clk = iris_get_clk_by_type(core, IRIS_HW_CLK);
	struct dev_pm_opp *opp;
	unsigned long freq;
	long rounded;
	int ret;

	if (vec->freq > 444000000 || vec->freq > ULONG_MAX)
		return -ERANGE;
	if (!clk || !core->opp_pmdomain_tbl || core->opp_pmdomain_tbl->num_pds != 2)
		return -ENODEV;
	freq = max_t(unsigned long, vec->freq, vec->opp);
	/* Strict ceiling, without devfreq's above-maximum floor fallback. */
	for (;;) {
		opp = dev_pm_opp_find_freq_ceil(core->dev, &freq);
		if (IS_ERR(opp))
			return PTR_ERR(opp);
		ret = iris_sm8475_check_opp(opp, freq);
		dev_pm_opp_put(opp);
		if (ret)
			return ret;
		rounded = clk_round_rate(clk, freq);
		if (rounded < 0)
			return rounded;
		if ((u64)rounded < vec->freq || !rounded)
			return -ERANGE;
		if (rounded <= freq)
			break;
		if (rounded > 444000000)
			return -ERANGE;
		freq = rounded;
	}
	vec->opp = freq;
	vec->rounded = rounded;
	return 0;
}

/*
 * Inspect the existing RO-divider providers; do not program a divider or guess
 * MVS0C's divisor. MVS0 /3 is [INFERRED, USER-ACCEPTED FOR DEVELOPMENT].
 */
static int iris_sm8475_clock_tree(struct iris_core *core, unsigned long opp,
				  bool readback, unsigned long *actual)
{
	struct clk *hw = iris_get_clk_by_type(core, IRIS_HW_CLK);
	struct clk *ctrl = iris_get_clk_by_type(core, IRIS_CTRL_CLK);
	struct clk *hw_div, *ctrl_div, *source, *ctrl_source;
	unsigned long parent_rate, ctrl_rate;
	u32 div;

	if (!hw || !ctrl)
		return -ENODEV;
	hw_div = clk_get_parent(hw);
	ctrl_div = clk_get_parent(ctrl);
	if (!hw_div || !ctrl_div)
		return -ENODEV;
	source = clk_get_parent(hw_div);
	ctrl_source = clk_get_parent(ctrl_div);
	if (!source || !ctrl_source || !clk_is_match(source, ctrl_source))
		return -EINVAL;
	parent_rate = clk_get_rate(source);
	ctrl_rate = clk_get_rate(ctrl);
	*actual = clk_get_rate(hw);
	if (!parent_rate || !ctrl_rate || !*actual)
		return -ERANGE;
	if (!readback) {
		/* Both divider registers in videocc-sm8450 have width 4. */
		for (div = 1; div <= 16; div++)
			if (DIV_ROUND_UP(parent_rate, div) == ctrl_rate)
				break;
		if (div > 16)
			return -ERANGE;
		if (core->resources.ctrl_div && core->resources.ctrl_div != div)
			return -ERANGE;
		core->resources.ctrl_div = div;
		return 0;
	}
	if (parent_rate != opp * 3 || *actual != opp ||
	    clk_get_rate(hw_div) != *actual ||
	    !core->resources.ctrl_div ||
	    DIV_ROUND_UP(parent_rate, core->resources.ctrl_div) != ctrl_rate ||
	    clk_get_rate(ctrl_div) != ctrl_rate)
		return -ERANGE;
	return 0;
}

static int iris_sm8475_clock(struct iris_core *core, struct iris_resource_vector *vec)
{
	struct dev_pm_opp *opp;
	unsigned long before;
	int ret;

	if (!vec->opp) {
		ret = dev_pm_opp_set_opp(core->dev, NULL);
		if (ret)
			core->resources.opp_uncertain = true;
		return ret;
	}
	ret = iris_sm8475_clock_tree(core, vec->opp, false, &before);
	if (ret)
		return ret;
	opp = dev_pm_opp_find_freq_exact(core->dev, vec->opp, true);
	if (IS_ERR(opp))
		return PTR_ERR(opp);
	ret = iris_sm8475_check_opp(opp, vec->opp);
	if (!ret) {
		ret = dev_pm_opp_set_opp(core->dev, opp);
		if (ret)
			core->resources.opp_uncertain = true;
	}
	dev_pm_opp_put(opp);
	if (ret)
		return ret;
	ret = iris_sm8475_clock_tree(core, vec->opp, true, &vec->actual);
	if (ret)
		return ret;
	if (vec->actual < vec->freq || vec->actual != vec->rounded)
		return -ERANGE;
	return 0;
}

/* All repairs, including the provider which failed, are attempted. */
static int iris_sm8475_restore(struct iris_core *core,
			       struct iris_resource_vector *old,
			       const struct iris_resource_vector *bridge)
{
	int *errors = core->resources.repair_errors;
	int ret = 0, i;

	errors[0] = iris_sm8475_icc(core, "cpu-cfg", max(old->cnoc, bridge->cnoc));
	errors[1] = iris_sm8475_icc(core, "video-mem", max(old->ddr, bridge->ddr));
	errors[2] = iris_sm8475_icc(core, "video-llcc", max(old->llcc, bridge->llcc));
	if (errors[0] || errors[1] || errors[2])
		errors[3] = -EIO; /* A repair must not raise clocks over unknown bandwidth. */
	else
		errors[3] = iris_sm8475_clock(core, old);
	/* Do not lower bandwidth if the old clock could not be restored. */
	if (!errors[3]) {
		errors[4] = iris_sm8475_icc(core, "video-llcc", old->llcc);
		errors[5] = iris_sm8475_icc(core, "video-mem", old->ddr);
		errors[6] = iris_sm8475_icc(core, "cpu-cfg", old->cnoc);
	}
	for (i = 0; i < 7; i++)
		if (!ret && errors[i])
			ret = errors[i];
	/*
	 * OPP _set_opp() caches current_opp only after complete success. After a
	 * partial failure, a same-old-OPP request may be a no-op. Clock readback
	 * alone cannot prove the required domains repaired. Keep the fault held.
	 */
	if (!ret && core->resources.opp_uncertain)
		ret = -EIO;
	return ret;
}

int iris_sm8475_apply(struct iris_core *core, struct iris_resource_vector *vec)
{
	struct iris_resource_vector old = core->resources.applied;
	struct iris_resource_vector bridge = *vec;
	int ret, rollback;

	lockdep_assert_held(&core->lock);
	if (core->resources.uncertain)
		return -EIO;
	if (vec->ddr > 15000000 || vec->llcc > 15000000 || vec->cnoc > 1000)
		return -ERANGE;
	ret = iris_sm8475_select_opp(core, vec);
	if (ret)
		return ret;
	bridge = *vec;
	bridge.cnoc = max(old.cnoc, vec->cnoc);
	bridge.ddr = max(old.ddr, vec->ddr);
	bridge.llcc = max(old.llcc, vec->llcc);
	bridge.freq = max(old.freq, vec->freq);
	bridge.opp = max(old.opp, vec->opp);
	ret = iris_sm8475_select_opp(core, &bridge);
	if (ret)
		return ret;
	ret = iris_sm8475_icc(core, "cpu-cfg", bridge.cnoc);
	if (ret)
		goto restore;
	ret = iris_sm8475_icc(core, "video-mem", bridge.ddr);
	if (ret)
		goto restore;
	ret = iris_sm8475_icc(core, "video-llcc", bridge.llcc);
	if (ret)
		goto restore;
	ret = iris_sm8475_clock(core, &bridge);
	if (ret)
		goto restore;
	/* Reductions are used only by confirmed-close cleanup, never queue drain. */
	ret = iris_sm8475_clock(core, vec);
	if (ret)
		goto restore;
	ret = iris_sm8475_icc(core, "video-llcc", vec->llcc);
	if (ret)
		goto restore;
	ret = iris_sm8475_icc(core, "video-mem", vec->ddr);
	if (ret)
		goto restore;
	ret = iris_sm8475_icc(core, "cpu-cfg", vec->cnoc);
	if (ret)
		goto restore;
	core->resources.applied = *vec;
	return 0;
restore:
	memset(core->resources.repair_errors, 0, sizeof(core->resources.repair_errors));
	rollback = iris_sm8475_restore(core, &old, &bridge);
	core->resources.error = ret;
	core->resources.rollback_error = rollback;
	if (rollback)
		iris_sm8475_fault(core, ret);
	return ret;
}

int iris_sm8475_unvote(struct iris_core *core)
{
	struct iris_resource_vector zero = { 0 };
	int errors[4], i, ret = 0;

	/* Caller established quiescence. Even partial unvote attempts all paths. */
	errors[0] = iris_sm8475_clock(core, &zero);
	errors[1] = iris_sm8475_icc(core, "video-llcc", 0);
	errors[2] = iris_sm8475_icc(core, "video-mem", 0);
	errors[3] = iris_sm8475_icc(core, "cpu-cfg", 0);
	for (i = 0; i < 4; i++) {
		core->resources.repair_errors[i] = errors[i];
		if (!ret && errors[i])
			ret = errors[i];
	}
	if (!ret && core->resources.opp_uncertain)
		ret = -EIO;
	if (ret)
		iris_sm8475_fault(core, ret);
	else
		core->resources.applied = zero;
	return ret;
}

int iris_sm8475_config_vote(struct iris_core *core)
{
	int ret = iris_sm8475_icc(core, "cpu-cfg", 1000);

	if (!ret) {
		core->resources.applied.cnoc = 1000;
	} else {
		int repair = iris_sm8475_icc(core, "cpu-cfg", core->resources.applied.cnoc);

		core->resources.error = ret;
		core->resources.rollback_error = repair;
		if (repair)
			iris_sm8475_fault(core, ret);
	}
	return ret;
}

int iris_sm8475_boot_opp(struct iris_core *core)
{
	struct dev_pm_opp *opp;
	int ret;

	opp = dev_pm_opp_find_freq_exact(core->dev, 444000000, true);
	if (IS_ERR(opp))
		return PTR_ERR(opp);
	ret = iris_sm8475_check_opp(opp, 444000000);
	if (!ret) {
		ret = dev_pm_opp_set_opp(core->dev, opp);
		if (ret)
			core->resources.opp_uncertain = true;
	}
	dev_pm_opp_put(opp);
	return ret;
}
