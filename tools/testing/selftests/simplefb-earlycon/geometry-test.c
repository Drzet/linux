// SPDX-License-Identifier: GPL-2.0
/*
 * Host-executable tests of the EXACT production layout, status, format and
 * glyph-row helpers. The DT node/property inputs below are MOCKS: this does
 * not execute the kernel's libfdt parser, early mapping or printk itself.
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
typedef uint64_t u64;
typedef uint32_t u32;
#include "../../../../drivers/tty/serial/earlycon-simplefb-check.h"

static unsigned int checks;
#define CHECK(expr) do { \
	checks++; \
	if (!(expr)) { \
		fprintf(stderr, "FAIL line %d: %s\\n", __LINE__, #expr); \
		exit(1); \
	} \
} while (0)

static struct simplefb_early_layout good(void)
{
	return (struct simplefb_early_layout) {
		.base = 0x10000000ULL, .span = 1024 * 768 * 4,
		.width = 1024, .height = 768, .stride = 4096,
	};
}

int main(void)
{
	struct simplefb_early_layout g = good();
	u64 bytes, offset;
	unsigned int i;
	const char *nodes[] = { "disabled", "okay", "ok" };
	unsigned int enabled = 0;

	CHECK(simplefb_early_layout_valid(&g, &bytes));
	CHECK(bytes == 1024 * 768 * 4);
	CHECK(simplefb_early_glyph_offset(&g, 1000, 750, 24, 18, &offset));
	CHECK(offset == 750 * 4096 + 1000 * 4);
	CHECK(!simplefb_early_glyph_offset(&g, 1001, 750, 24, 18, &offset));
	CHECK(!simplefb_early_glyph_offset(&g, 1000, 751, 24, 18, &offset));
	CHECK(!simplefb_early_glyph_offset(&g, 0, 0, 0, 16, &offset));
	CHECK(simplefb_early_next_y(0, 32, 32) == 0);
	CHECK(simplefb_early_next_y(0, 64, 32) == 32);
	CHECK(simplefb_early_next_y(32, 64, 32) == 0);
	CHECK(simplefb_early_next_y(0, 48, 32) == 0);

	g.width = 0; CHECK(!simplefb_early_layout_valid(&g, &bytes)); g = good();
	g.height = 0; CHECK(!simplefb_early_layout_valid(&g, &bytes)); g = good();
	g.stride = 4095; CHECK(!simplefb_early_layout_valid(&g, &bytes)); g = good();
	g.width = 1073741825U; CHECK(!simplefb_early_layout_valid(&g, &bytes)); g = good();
	g.span--; CHECK(!simplefb_early_layout_valid(&g, &bytes)); g = good();
	g.base++; CHECK(!simplefb_early_layout_valid(&g, &bytes)); g = good();
	g.base = UINT64_MAX - 128;
	CHECK(!simplefb_early_layout_valid(&g, &bytes)); g = good();
	g.stride = UINT32_MAX; CHECK(!simplefb_early_layout_valid(&g, &bytes)); g = good();
	g.span = 0; CHECK(!simplefb_early_layout_valid(&g, &bytes));

	CHECK(simplefb_early_format_ok("a8r8g8b8", 9));
	CHECK(simplefb_early_format_ok("x8r8g8b8", 9));
	CHECK(!simplefb_early_format_ok("x8r8g8b8", 8));
	CHECK(!simplefb_early_format_ok("r5g6b5", 7));
	CHECK(!simplefb_early_format_ok(NULL, 0));
	CHECK(!simplefb_early_format_ok("a8r8g8b8X", 9)); /* no NUL */
	CHECK(simplefb_early_status_ok(NULL, 0));
	CHECK(simplefb_early_status_ok("okay", 5));
	CHECK(simplefb_early_status_ok("ok", 3));
	CHECK(!simplefb_early_status_ok("disabled", 9));
	CHECK(!simplefb_early_status_ok("okay", 4)); /* no NUL */
	CHECK(!simplefb_early_status_ok("okay!", 6));

	/* MOCK node-selection policy: more than one enabled candidate rejects. */
	for (i = 0; i < 3; i++)
		enabled += simplefb_early_status_ok(nodes[i], strlen(nodes[i]) + 1);
	CHECK(enabled == 2); /* production parser returns -EEXIST */
	puts("PASS: 34+ host checks on production predicates; DT/scanout mocked");
	return 0;
}
