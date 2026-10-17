// SPDX-License-Identifier: GPL-2.0
/*
 * Early console on a bootloader-provided framebuffer described in the DT.
 *
 * Ported from Xiaomi Pad 6 Pro source commit fe81794b5e1b8a3f11a165f3acbfdb8fa671dbb3.
 * Copyright and licensing retained from the original GPL-2.0 source.
 *
 * Retail phones and tablets usually bring out no debug UART, so on those the
 * panel is the only way to see anything a kernel prints. fbcon can do that, but
 * it only takes over at device_initcall, long after cmd-db, rpmh-rsc, clk-rpmh,
 * rpmhpd (core_initcall), smem (arch_initcall) and gcc (subsys_initcall) have
 * already run -- which on a fresh port is precisely where a boot tends to die,
 * and it dies with a blank screen and nothing to go on.
 *
 * This earlycon runs from parse_early_param(), before any of that, and prints
 * onto the framebuffer the bootloader left scanning out. Select it with
 * "earlycon=simplefb"; the geometry comes from the /chosen simple-framebuffer
 * node, so nothing has to be hardcoded.
 *
 * Two command line caveats, both of which silently defeat it:
 *   - a bare "earlycon" never reaches here when CONFIG_ACPI_SPCR_TABLE is
 *     enabled, because that takes the SPCR branch and returns before the DT is
 *     ever consulted. The name has to be spelled out.
 *   - "keep_bootcon" is required to keep this console past console_init(),
 *     which runs before every initcall. Without it the VT console registers
 *     there as CON_CONSDEV and printk unregisters every CON_BOOT console
 *     (kernel/printk/printk.c), so the whole window this driver exists to cover
 *     is dark again.
 *
 * Adapted from drivers/firmware/efi/earlycon.c, which does the same job from
 * screen_info. It scrolls the whole framebuffer up one text line at a time;
 * this one instead wipes and restarts from the top once it reaches the bottom.
 * Scrolling reads back write-combining memory, which is slow enough on a
 * 1800x2880x32 panel (20 MiB a line) to look like the hang we came to debug.
 * Reusing a line only clears that line, so the screen still holds
 * the most recent lines, with the last one written being where the kernel got
 * stuck.
 */

#include <linux/console.h>
#include <linux/font.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/libfdt.h>
#include <linux/of.h>
#include <linux/of_fdt.h>
#include <linux/overflow.h>
#include <linux/pgtable.h>
#include <linux/serial_core.h>
#include <linux/string.h>
#include <asm/early_ioremap.h>

#include "earlycon-simplefb-check.h"

/* Opaque in both a8r8g8b8 and x8r8g8b8: the alpha byte is set, not left zero. */
#define SIMPLEFB_WHITE	0xffffffffU
#define SIMPLEFB_BLACK	0xff000000U

static const struct font_desc *font;
static u64 fb_base;
static u64 fb_size;
static u32 fb_width, fb_height, fb_stride;
static u32 cur_x, cur_y;
static void *fb_mapped;
static bool fb_dead;
static struct console *simplefb_con;

/* The permanent WC mapping deliberately lives while keep_bootcon is active. */

static __ref void *simplefb_map(u64 start, unsigned long len)
{
	if (start > fb_size || len > fb_size - start || fb_dead)
		return NULL;

	if (fb_mapped)
		return (u8 *)fb_mapped + start;

	/* Before early_initcall only: after that failure is permanently fatal. */
	return early_memremap_prot(fb_base + start, len,
				   pgprot_val(pgprot_writecombine(PAGE_KERNEL)));
}

static __ref void simplefb_unmap(void *addr, unsigned long len)
{
	if (!fb_mapped)
		early_memunmap(addr, len);
}

/*
 * Transition before .init mappings disappear. If remap fails, later writes
 * stop rather than calling freed early_memremap code. If keep_bootcon remains
 * registered, retain the mapping: there is no safe late-unregister callback.
 */
static int __init simplefb_earlycon_remap(void)
{
	if (!simplefb_con || !console_is_registered(simplefb_con))
		return 0;

	fb_mapped = memremap(fb_base, fb_size, MEMREMAP_WC);
	if (!fb_mapped) {
		fb_dead = true;
		return -ENOMEM;
	}

	return 0;
}
early_initcall(simplefb_earlycon_remap);

static int __init simplefb_earlycon_unmap(void)
{
	if (fb_mapped && !console_is_registered(simplefb_con)) {
		memunmap(fb_mapped);
		fb_mapped = NULL;
		fb_dead = true;
	}
	return 0;
}
late_initcall(simplefb_earlycon_unmap);

static bool simplefb_clear_lines(u32 y, u32 lines)
{
	u32 i;

	if (y > fb_height || lines > fb_height - y)
		return false;

	for (i = 0; i < lines; i++) {
		void *dst = simplefb_map((u64)(y + i) * fb_stride, fb_stride);

		if (!dst)
			return false;

		memset32(dst, SIMPLEFB_BLACK, fb_stride / 4);
		simplefb_unmap(dst, fb_stride);
	}

	return true;
}

static void simplefb_write_char(u32 *dst, unsigned char c, unsigned int h)
{
	const u8 *src;
	int m, n, bytes;
	u8 x;

	bytes = BITS_TO_BYTES(font->width);
	if (c >= font->charcount)
		c = '?' < font->charcount ? '?' : 0;
	src = font->data + c * font->height * bytes + h * bytes;

	for (m = 0; m < font->width; m++) {
		n = m % 8;
		x = *(src + m / 8);
		if ((x >> (7 - n)) & 1)
			*dst = SIMPLEFB_WHITE;
		else
			*dst = SIMPLEFB_BLACK;
		dst++;
	}
}

/* Start over at the top rather than scrolling; see the file comment. */
static void simplefb_next_line(void)
{
	cur_x = 0;
	cur_y = simplefb_early_next_y(cur_y, fb_height, font->height);
}

static void simplefb_earlycon_write(struct console *con, const char *str,
				    unsigned int num)
{
	while (num && !fb_dead) {
		const char *nl;
		unsigned int linemax, count, h;

		/* Make a full glyph fit before any drawing or arithmetic. */
		if (cur_x > fb_width - font->width)
			simplefb_next_line();
		linemax = (fb_width - cur_x) / font->width;
		if (!linemax)
			return; /* invariant violation, never spin in printk */

		nl = memchr(str, '\n', num);
		count = min_t(unsigned int, nl ? nl - str : num, linemax);

		if (!count && nl == str) {
			simplefb_next_line();
			str++;
			num--;
			continue;
		}
		if (!count)
			return;

		/* Reuse one line at a time: no WC framebuffer readback/scroll. */
		if (!cur_x && !simplefb_clear_lines(cur_y, font->height))
			return;

		for (h = 0; h < font->height; h++) {
			u64 row;
			u32 *dst;
			unsigned int n;

			if (!simplefb_early_glyph_offset(
				    &(struct simplefb_early_layout) {
					.width = fb_width, .height = fb_height,
					.stride = fb_stride,
				    }, cur_x, cur_y + h, count * font->width,
				    1, &row))
				return;
			dst = simplefb_map(row, (unsigned long)count *
					  font->width * sizeof(*dst));
			if (!dst)
				return;
			for (n = 0; n < count; n++)
				simplefb_write_char(dst + n * font->width,
						    (unsigned char)str[n], h);
			simplefb_unmap(dst, (unsigned long)count *
				       font->width * sizeof(*dst));
		}

		cur_x += count * font->width;
		str += count;
		num -= count;

		if (num && *str == '\n') {
			simplefb_next_line();
			str++;
			num--;
		} else if (cur_x > fb_width - font->width) {
			simplefb_next_line();
		}
	}
}

/*
 * The framebuffer is described under /chosen, which is where a bootloader that
 * hands over a live display puts it. Read it straight out of the flat tree:
 * this runs before unflatten_device_tree(), and it keeps the node's address in
 * one place rather than duplicating it on the command line.
 */
static int __init simplefb_earlycon_scan_dt(void)
{
	const void *fdt = initial_boot_params;
	const __be32 *prop, *reg;
	const char *format, *status;
	struct simplefb_early_layout g = {};
	u32 addr_cells, size_cells;
	u64 addr, size = 0, bytes;
	int chosen, parent, root, offset, node = -1;
	int count = 0, len, i;

	root = of_get_flat_dt_root();
	chosen = of_get_flat_dt_subnode_by_name(root, "chosen");
	if (chosen < 0)
		return -ENODEV;

	for (offset = fdt_first_subnode(fdt, chosen);
	     offset >= 0;
	     offset = fdt_next_subnode(fdt, offset)) {
		if (!of_flat_dt_is_compatible(offset, "simple-framebuffer"))
			continue;
		status = of_get_flat_dt_prop(offset, "status", &len);
		if (!simplefb_early_status_ok(status, len))
			continue;
		node = offset;
		if (++count > 1)
			return -EEXIST; /* never silently choose a different display */
	}
	if (!count)
		return -ENODEV;

	/*
	 * Cells apply to the framebuffer's parent (/chosen), falling back to
	 * inherited root cells. Accept one address/size tuple only.
	 */
	parent = fdt_parent_offset(fdt, chosen);
	if (parent < 0)
		return -EINVAL;
	prop = of_get_flat_dt_prop(chosen, "#address-cells", &len);
	if (!prop)
		prop = of_get_flat_dt_prop(parent, "#address-cells", &len);
	if (!prop || len != sizeof(*prop))
		return -EINVAL;
	addr_cells = be32_to_cpup(prop);

	prop = of_get_flat_dt_prop(chosen, "#size-cells", &len);
	if (!prop)
		prop = of_get_flat_dt_prop(parent, "#size-cells", &len);
	if (!prop || len != sizeof(*prop))
		return -EINVAL;
	size_cells = be32_to_cpup(prop);

	if (addr_cells < 1 || addr_cells > 2 ||
	    size_cells < 1 || size_cells > 2)
		return -EINVAL;

	reg = of_get_flat_dt_prop(node, "reg", &len);
	if (!reg || len != (addr_cells + size_cells) * sizeof(*reg))
		return -EINVAL;
	for (i = 0; i < size_cells; i++)
		size = (size << 32) | be32_to_cpu(reg[addr_cells + i]);

	addr = of_flat_dt_translate_address(node);
	if (addr == OF_BAD_ADDR)
		return -ENXIO;
	g.base = addr;
	g.span = size;

	prop = of_get_flat_dt_prop(node, "width", &len);
	if (!prop || len != sizeof(*prop))
		return -EINVAL;
	g.width = be32_to_cpup(prop);

	prop = of_get_flat_dt_prop(node, "height", &len);
	if (!prop || len != sizeof(*prop))
		return -EINVAL;
	g.height = be32_to_cpup(prop);

	prop = of_get_flat_dt_prop(node, "stride", &len);
	if (!prop || len != sizeof(*prop))
		return -EINVAL;
	g.stride = be32_to_cpup(prop);

	format = of_get_flat_dt_prop(node, "format", &len);
	if (!simplefb_early_format_ok(format, len) ||
	    !simplefb_early_layout_valid(&g, &bytes))
		return -EINVAL;

	/* Only publish validated coordinates to the write callback. */
	fb_base = g.base;
	fb_size = bytes;
	fb_width = g.width;
	fb_height = g.height;
	fb_stride = g.stride;
	return 0;
}

static int __init simplefb_earlycon_setup(struct earlycon_device *device,
					  const char *opt)
{
	int ret;

	ret = simplefb_earlycon_scan_dt();
	if (ret)
		return ret;

	/*
	 * The only way to read this screen is a camera pointed at a 300 ppi
	 * panel, where an 8x16 glyph is well under a millimetre tall. Ask for
	 * the largest font by name and fall back rather than trusting
	 * get_default_font()'s scoring to pick it.
	 */
	font = find_font("TER16x32");
	if (!font)
		font = get_default_font(fb_width, fb_height, NULL, NULL);
	if (!font)
		return -ENODEV;

	/* get_default_font() scores; also verify usable font storage. */
	if (!font->data || !font->width || !font->height ||
	    !font->charcount || fb_width < font->width ||
	    fb_height < font->height)
		return -ENODEV;

	/* Clear only lines we actually write; never wipe the whole splash. */
	cur_x = 0;
	cur_y = 0;

	device->con->write = simplefb_earlycon_write;
	simplefb_con = device->con;

	return 0;
}
EARLYCON_DECLARE(simplefb, simplefb_earlycon_setup);
