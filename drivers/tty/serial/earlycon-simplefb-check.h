/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Bounds and flat-DT string helpers shared with earlycon-simplefb.c.
 * Adapted for the isolated oneli earlycon review from the Xiaomi Pad 6 Pro
 * earlycon (yzddmr6/linux-sm8450-liuqin@fe81794b5e1b8a3f11a165f3acbfdb8fa671dbb3).
 *
 * Intentionally uses caller-provided kernel integer/string types so the exact
 * production predicates can also be compiled by the host test harness.
 */
#ifndef _EARLYCON_SIMPLEFB_CHECK_H
#define _EARLYCON_SIMPLEFB_CHECK_H

struct simplefb_early_layout {
	u64 base;
	u64 span;
	u32 width;
	u32 height;
	u32 stride;
};

/* DT properties are not trusted to be NUL-terminated. */
static inline bool simplefb_early_format_ok(const char *s, int len)
{
	return s && len == 8 + 1 &&
		(!memcmp(s, "a8r8g8b8", 9) || !memcmp(s, "x8r8g8b8", 9));
}

static inline bool simplefb_early_status_ok(const char *s, int len)
{
	if (!s)
		return true; /* absent status means enabled */

	return (len == 5 && !memcmp(s, "okay", 5)) ||
	       (len == 3 && !memcmp(s, "ok", 3));
}

static inline bool simplefb_early_layout_valid(const struct simplefb_early_layout *g,
						u64 *bytes)
{
	u64 extent;

	if (!g->width || !g->height || !g->stride || !g->span ||
	    (g->base & 3) || (g->stride & 3) ||
	    (u64)g->width * 4 > g->stride)
		return false;

	extent = (u64)g->stride * g->height;
	if (!extent || extent > g->span || extent > (u64)(size_t)-1)
		return false;

	/* Both the described reg extent and the actual framebuffer must fit. */
	if (g->base > (u64)-1 - g->span)
		return false;

	*bytes = extent;
	return true;
}

static inline bool simplefb_early_glyph_offset(const struct simplefb_early_layout *g,
						u32 x, u32 y, u32 glyph_width,
						u32 glyph_height, u64 *offset)
{
	if (!glyph_width || !glyph_height || x > g->width ||
	    y > g->height || glyph_width > g->width - x ||
	    glyph_height > g->height - y)
		return false;

	*offset = (u64)y * g->stride + (u64)x * 4;
	return true;
}

/* Wrap safely even when the framebuffer holds fewer than two glyph rows. */
static inline u32 simplefb_early_next_y(u32 y, u32 height, u32 glyph_height)
{
	if (!glyph_height || glyph_height > height ||
	    y > height - glyph_height ||
	    height - glyph_height - y < glyph_height)
		return 0;
	return y + glyph_height;
}

#endif
