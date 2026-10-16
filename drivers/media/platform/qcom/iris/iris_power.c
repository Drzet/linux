// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2022-2024 Qualcomm Innovation Center, Inc. All rights reserved.
 */

#include <linux/overflow.h>
#include <linux/pm_opp.h>
#include <linux/pm_runtime.h>
#include <media/v4l2-mem2mem.h>

#include "iris_buffer.h"
#include "iris_instance.h"
#include "iris_power.h"
#include "iris_resources.h"
#include "iris_vpu_common.h"

static u32 iris_calc_bw(struct iris_inst *inst, struct icc_vote_data *data)
{
	const struct bw_info *bw_tbl = NULL;
	struct iris_core *core = inst->core;
	u32 num_rows, i, mbs, mbps;
	u32 icc_bw = 0;

	mbs = DIV_ROUND_UP(data->height, 16) * DIV_ROUND_UP(data->width, 16);
	mbps = mbs * data->fps;
	if (mbps == 0)
		goto exit;

	bw_tbl = core->iris_platform_data->bw_tbl_dec;
	num_rows = core->iris_platform_data->bw_tbl_dec_size;

	for (i = 0; i < num_rows; i++) {
		if (i != 0 && mbps > bw_tbl[i].mbs_per_sec)
			break;

		icc_bw = bw_tbl[i].bw_ddr;
	}

exit:
	return icc_bw;
}

static int iris_set_interconnects(struct iris_inst *inst)
{
	struct iris_core *core = inst->core;
	struct iris_inst *instance;
	u64 total_bw_ddr = 0;
	int ret;

	mutex_lock(&core->lock);
	list_for_each_entry(instance, &core->instances, list) {
		if (!instance->max_input_data_size)
			continue;

		total_bw_ddr += instance->power.icc_bw;
	}

	ret = iris_set_icc_bw(core, total_bw_ddr);

	mutex_unlock(&core->lock);

	return ret;
}

static int iris_vote_interconnects(struct iris_inst *inst)
{
	struct icc_vote_data *vote_data = &inst->icc_data;
	struct v4l2_format *inp_f = inst->fmt_src;

	vote_data->width = inp_f->fmt.pix_mp.width;
	vote_data->height = inp_f->fmt.pix_mp.height;
	vote_data->fps = DEFAULT_FPS;

	inst->power.icc_bw = iris_calc_bw(inst, vote_data);

	return iris_set_interconnects(inst);
}

static int iris_set_clocks(struct iris_inst *inst)
{
	struct iris_core *core = inst->core;
	struct iris_inst *instance;
	u64 freq = 0;
	int ret;

	mutex_lock(&core->lock);
	list_for_each_entry(instance, &core->instances, list) {
		if (!instance->max_input_data_size)
			continue;

		freq += instance->power.min_freq;
	}

	core->power.clk_freq = freq;
	ret = iris_opp_set_rate(core->dev, freq);
	mutex_unlock(&core->lock);

	return ret;
}

static int iris_scale_clocks(struct iris_inst *inst)
{
	const struct vpu_ops *vpu_ops = inst->core->iris_platform_data->vpu_ops;
	struct v4l2_m2m_ctx *m2m_ctx = inst->m2m_ctx;
	struct v4l2_m2m_buffer *buffer, *n;
	struct iris_buffer *buf;
	size_t data_size = 0;

	v4l2_m2m_for_each_src_buf_safe(m2m_ctx, buffer, n) {
		buf = to_iris_buffer(&buffer->vb);
		data_size = max(data_size, buf->data_size);
	}

	inst->max_input_data_size = data_size;
	if (!inst->max_input_data_size)
		return 0;

	inst->power.min_freq = vpu_ops->calc_freq(inst, inst->max_input_data_size);

	return iris_set_clocks(inst);
}

int iris_scale_power(struct iris_inst *inst)
{
	struct iris_core *core = inst->core;
	int ret;

	if (iris_sm8475_policy(core))
		return iris_sm8475_admit(inst);

	if (pm_runtime_suspended(core->dev)) {
		ret = pm_runtime_resume_and_get(core->dev);
		if (ret < 0)
			return ret;

		pm_runtime_put_autosuspend(core->dev);
	}

	ret = iris_scale_clocks(inst);
	if (ret)
		return ret;

	return iris_vote_interconnects(inst);
}

bool iris_sm8475_policy(struct iris_core *core)
{
	return core->iris_platform_data->checked_vpu2_policy;
}

/* Caller owns inst->lock. Never inspect another instance's mutable formats. */
static int iris_sm8475_workload(struct iris_inst *inst, u64 *mbpf, u64 *mbps)
{
	struct platform_inst_caps *caps = inst->core->iris_platform_data->inst_caps;
	u32 width = inst->fmt_src->fmt.pix_mp.width;
	u32 height = inst->fmt_src->fmt.pix_mp.height;
	u32 fps = max(inst->frame_rate, inst->operating_rate);
	u64 w, h;

	if (width < caps->min_frame_width || width > caps->max_frame_width ||
	    height < caps->min_frame_height || height > caps->max_frame_height ||
	    inst->crop.width > caps->max_frame_width ||
	    inst->crop.height > caps->max_frame_height ||
	    inst->frame_rate < DEFAULT_FPS || inst->frame_rate > MAXIMUM_FPS ||
	    inst->operating_rate > MAXIMUM_FPS)
		return -EINVAL;

	w = DIV_ROUND_UP(max(width, inst->crop.width), 16);
	h = DIV_ROUND_UP(max(height, inst->crop.height), 16);
	if (check_mul_overflow(w, h, mbpf) || check_mul_overflow(*mbpf, (u64)fps, mbps))
		return -ERANGE;
	if (*mbpf > caps->max_mbpf)
		return -ENOMEM;

	return 0;
}

void iris_sm8475_configure(struct iris_inst *inst)
{
	u64 mbpf = 0, mbps = 0;
	int ret;

	if (!iris_sm8475_policy(inst->core))
		return;

	ret = iris_sm8475_workload(inst, &mbpf, &mbps);
	mutex_lock(&inst->core->lock);
	inst->admission_mbpf = mbpf;
	inst->admission_mbps = mbps;
	inst->admission_error = ret;
	mutex_unlock(&inst->core->lock);
}

int iris_sm8475_adapt(struct iris_inst *inst, u64 bytes, struct iris_reservation *res)
{
	struct iris_vpu2_bw_input *in = &res->input;
	struct iris_vpu2_bw_output out;
	u32 depth = inst->fw_caps[BIT_DEPTH].value;
	int ret;

	memset(res, 0, sizeof(*res));
	if (inst->domain != DECODER || !iris_split_mode_enabled(inst))
		return -EINVAL;

	switch (inst->codec) {
	case V4L2_PIX_FMT_H264:
		in->codec = IRIS_VPU2_BW_H264;
		break;
	case V4L2_PIX_FMT_HEVC:
		in->codec = IRIS_VPU2_BW_HEVC;
		break;
	case V4L2_PIX_FMT_VP9:
		in->codec = IRIS_VPU2_BW_VP9;
		break;
	default:
		return -EINVAL;
	}

	switch (inst->fmt_dst->fmt.pix_mp.pixelformat) {
	case V4L2_PIX_FMT_NV12:
		if (depth != BIT_DEPTH_8)
			return -EINVAL;
		in->format = IRIS_VPU2_BW_NV12;
		break;
	case V4L2_PIX_FMT_P010:
		if (depth != BIT_DEPTH_10)
			return -EINVAL;
		in->format = IRIS_VPU2_BW_P010;
		break;
	default:
		return -EINVAL;
	}

	in->width = inst->fmt_src->fmt.pix_mp.width;
	in->height = inst->fmt_src->fmt.pix_mp.height;
	in->output_width = inst->fmt_dst->fmt.pix_mp.width;
	in->output_height = inst->fmt_dst->fmt.pix_mp.height;
	in->frame_rate = inst->frame_rate;
	in->operating_rate = inst->operating_rate;
	in->queued_bytes = bytes;
	ret = iris_vpu2_calc_bw(in, &out);
	if (ret)
		return ret;

	ret = iris_sm8475_workload(inst, &res->mbpf, &res->mbps);
	if (ret)
		return ret;
	ret = iris_sm8475_frequency(max(in->width, inst->crop.width),
				    max(in->height, inst->crop.height),
				    max(in->frame_rate, in->operating_rate), bytes,
				    &res->demand.freq);
	if (ret)
		return ret;

	res->demand.ddr = out.ddr_kbps;
	res->demand.llcc = out.llcc_kbps;
	res->state = IRIS_RES_RESERVED;

	return 0;
}

void iris_sm8475_fault(struct iris_core *core, int error)
{
	struct iris_inst *inst;

	lockdep_assert_held(&core->lock);
	if (!core->resources.fault_pm_held) {
		pm_runtime_get_noresume(core->dev);
		core->resources.fault_pm_held = true;
	}
	core->resources.uncertain = true;
	core->resources.error = error;
	list_for_each_entry(inst, &core->instances, list) {
		if (inst->reservation.state != IRIS_RES_INACTIVE)
			inst->reservation.state = IRIS_RES_ERROR;
		iris_vb2_queue_error(inst);
	}
}

/* core->lock serializes the list, reservations, providers and PM callbacks. */
static int iris_sm8475_aggregate(struct iris_inst *changing,
				 const struct iris_reservation *proposal,
				 struct iris_resource_vector *total)
{
	struct iris_core *core = changing->core;
	const struct iris_resource_vector *demand;
	struct iris_inst *inst;
	u64 mbpf = 0, mbps = 0;
	u32 count = 0;
	bool found = false;

	memset(total, 0, sizeof(*total));
	total->cnoc = 1000;
	list_for_each_entry(inst, &core->instances, list) {
		count++;
		found |= inst == changing;
		if (proposal->state != IRIS_RES_INACTIVE && inst->admission_error)
			return inst->admission_error;
		if (check_add_overflow(mbpf, inst->admission_mbpf, &mbpf) ||
		    check_add_overflow(mbps, inst->admission_mbps, &mbps))
			return -ERANGE;
		if (inst == changing) {
			if (proposal->state == IRIS_RES_INACTIVE)
				continue;
			demand = &proposal->demand;
		} else {
			if (inst->reservation.state == IRIS_RES_INACTIVE)
				continue;
			demand = &inst->reservation.demand;
		}
		if (check_add_overflow(total->ddr, demand->ddr, &total->ddr) ||
		    check_add_overflow(total->llcc, demand->llcc, &total->llcc) ||
		    check_add_overflow(total->freq, demand->freq, &total->freq))
			return -ERANGE;
	}
	if (!found)
		return -EINVAL;
	if (proposal->state != IRIS_RES_INACTIVE &&
	    (count > core->iris_platform_data->max_session_count ||
	    mbpf > core->iris_platform_data->max_core_mbpf ||
	    mbps > core->iris_platform_data->max_core_mbps))
		return -ENOMEM;
	if (total->ddr > 15000000 || total->llcc > 15000000 || total->freq > ULONG_MAX)
		return -ERANGE;
	if (total->freq) {
		total->ddr = max_t(u64, total->ddr, 1000);
		total->llcc = max_t(u64, total->llcc, 1000);
	}

	return iris_sm8475_select_opp(core, total);
}

int iris_sm8475_admit(struct iris_inst *inst)
{
	struct iris_core *core = inst->core;
	struct iris_resource_vector total;
	struct iris_reservation proposed;
	struct v4l2_m2m_buffer *buffer, *n;
	u64 bytes = 0;
	bool keep_pm = false;
	int ret;

	lockdep_assert_held(&inst->lock);
	/* Includes DEFERRED and QUEUED/HFI-owned input still on the ready list. */
	v4l2_m2m_for_each_src_buf_safe(inst->m2m_ctx, buffer, n) {
		struct iris_buffer *buf = to_iris_buffer(&buffer->vb);

		bytes = max_t(u64, bytes, buf->data_size);
	}
	/* Never reduce a reservation because firmware consumed a ready buffer. */
	bytes = max(bytes, inst->reservation.input.queued_bytes);
	ret = iris_sm8475_adapt(inst, bytes, &proposed);
	if (ret)
		return ret;
	iris_sm8475_configure(inst);

	/* Resume takes core->lock: PM get MUST precede it. */
	ret = pm_runtime_resume_and_get(core->dev);
	if (ret < 0)
		return ret;
	mutex_lock(&core->lock);
	if (core->resources.uncertain || inst->reservation.closed ||
	    inst->reservation.state == IRIS_RES_ERROR) {
		ret = -EIO;
		goto unlock;
	}
	proposed.demand.ddr = max(proposed.demand.ddr, inst->reservation.demand.ddr);
	proposed.demand.llcc = max(proposed.demand.llcc, inst->reservation.demand.llcc);
	proposed.demand.freq = max(proposed.demand.freq, inst->reservation.demand.freq);
	ret = iris_sm8475_aggregate(inst, &proposed, &total);
	if (ret)
		goto unlock;
	ret = iris_sm8475_apply(core, &total);
	if (ret)
		goto unlock;
	keep_pm = !inst->reservation.pm_held;
	proposed.pm_held = true;
	inst->reservation = proposed;
unlock:
	mutex_unlock(&core->lock);
	if (!keep_pm)
		pm_runtime_put_autosuspend(core->dev);

	return ret;
}

/* Only a confirmed CLOSE response proves all retained session work quiescent. */
int iris_sm8475_release(struct iris_inst *inst)
{
	struct iris_core *core = inst->core;
	/* CLOSE is firmware quiescence, independent of the released demand. */
	struct iris_reservation inactive = { .state = IRIS_RES_INACTIVE, .closed = true };
	struct iris_resource_vector total;
	bool put_pm = false;
	int ret;

	if (!iris_sm8475_policy(core))
		return 0;
	if (inst->reservation.closed && !inst->reservation.pm_held &&
	    inst->reservation.state == IRIS_RES_INACTIVE)
		return 0;

	ret = pm_runtime_resume_and_get(core->dev);
	if (ret < 0)
		return ret;
	mutex_lock(&core->lock);
	if (!inst->reservation.closed || core->resources.uncertain) {
		ret = -EIO;
		goto unlock;
	}
	/* Removal cannot be blocked by a now-invalid configured instance. */
	inst->admission_mbpf = 0;
	inst->admission_mbps = 0;
	inst->admission_error = 0;
	ret = iris_sm8475_aggregate(inst, &inactive, &total);
	if (ret)
		goto fault;
	ret = iris_sm8475_apply(core, &total);
	if (ret)
		goto fault;
	put_pm = inst->reservation.pm_held;
	inst->reservation = inactive;
	goto unlock;
fault:
	iris_sm8475_fault(core, ret);
unlock:
	mutex_unlock(&core->lock);
	if (put_pm)
		pm_runtime_put_autosuspend(core->dev);
	pm_runtime_put_autosuspend(core->dev);

	return ret;
}

int iris_sm8475_suspend(struct iris_core *core)
{
	struct iris_inst *inst;

	if (!iris_sm8475_policy(core))
		return 0;
	if (core->resources.uncertain)
		return -EIO;
	list_for_each_entry(inst, &core->instances, list)
		if (inst->reservation.state != IRIS_RES_INACTIVE)
			return -EBUSY;

	return 0;
}

int iris_sm8475_resume(struct iris_core *core)
{
	struct iris_resource_vector vec = core->resources.applied;

	if (!iris_sm8475_policy(core))
		return 0;
	if (core->resources.uncertain)
		return -EIO;
	vec.cnoc = 1000;
	return iris_sm8475_apply(core, &vec);
}

/* Explicit recovery only: no timeout, drain or queue-empty inference is accepted. */
int iris_sm8475_recover_idle(struct iris_core *core)
{
	struct iris_inst *inst;
	u32 puts = 0;
	int ret;

	if (!iris_sm8475_policy(core))
		return -EINVAL;
	ret = pm_runtime_resume_and_get(core->dev);
	if (ret < 0)
		return ret;
	mutex_lock(&core->lock);
	list_for_each_entry(inst, &core->instances, list) {
		if (inst->reservation.state != IRIS_RES_INACTIVE && !inst->reservation.closed) {
			ret = -EBUSY;
			goto unlock;
		}
	}
	ret = iris_sm8475_unvote(core);
	if (ret)
		goto unlock;
	list_for_each_entry(inst, &core->instances, list) {
		puts += inst->reservation.pm_held;
		inst->reservation.pm_held = false;
		inst->reservation.state = IRIS_RES_INACTIVE;
		memset(&inst->reservation.demand, 0, sizeof(inst->reservation.demand));
	}
	puts += core->resources.fault_pm_held;
	core->resources.fault_pm_held = false;
	core->resources.uncertain = false;
unlock:
	mutex_unlock(&core->lock);
	while (puts--)
		pm_runtime_put_autosuspend(core->dev);
	pm_runtime_put_autosuspend(core->dev);
	return ret;
}
