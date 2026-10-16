/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Copyright (c) 2022-2024 Qualcomm Innovation Center, Inc. All rights reserved.
 */

#ifndef __IRIS_POWER_H__
#define __IRIS_POWER_H__

#include "iris_vpu2_bw.h"

struct iris_inst;
struct iris_core;

/* SM8475 development policy, not physical bandwidth/clock calibration. */
struct iris_resource_vector {
	u64 ddr;
	u64 llcc;
	u64 freq;
	u32 cnoc;
	unsigned long opp;
	unsigned long rounded;
	unsigned long actual;
};

enum iris_reservation_state {
	IRIS_RES_INACTIVE,
	IRIS_RES_RESERVED,
	IRIS_RES_ERROR,
};

struct iris_reservation {
	enum iris_reservation_state state;
	struct iris_vpu2_bw_input input;
	struct iris_resource_vector demand;
	u64 mbpf;
	u64 mbps;
	bool pm_held;
	bool closed;
};

struct iris_resource_state {
	struct iris_resource_vector applied;
	bool uncertain;
	bool opp_uncertain;
	bool fault_pm_held;
	u32 ctrl_div;
	int error;
	int rollback_error;
	int repair_errors[7];
};

bool iris_sm8475_policy(struct iris_core *core);
void iris_sm8475_configure(struct iris_inst *inst);
int iris_sm8475_adapt(struct iris_inst *inst, u64 bytes, struct iris_reservation *res);
int iris_sm8475_admit(struct iris_inst *inst);
int iris_sm8475_close_session(struct iris_inst *inst);
int iris_sm8475_release(struct iris_inst *inst);
void iris_sm8475_fault(struct iris_core *core, int error);
int iris_sm8475_recover_idle(struct iris_core *core);
int iris_sm8475_resume(struct iris_core *core);
int iris_sm8475_suspend(struct iris_core *core);
int iris_sm8475_frequency(u32 width, u32 height, u32 fps, u64 bytes, u64 *freq);

int iris_scale_power(struct iris_inst *inst);

#endif
