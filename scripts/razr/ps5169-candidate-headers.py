#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Standalone build repair for Esteban Urrutia's PS5169 2026-08-10 v3 RFC.

CI obtains the original series via Patchew/git am. This local adjustment only
adds direct Linux headers that the upstream reviewer requested; hardware
reset semantics and board-specific tuning are intentionally untouched.
"""
from pathlib import Path

source = Path("drivers/usb/typec/mux/ps5169.c")
original = source.read_text()
anchor = "#include <linux/i2c.h>\n"
assert original.count(anchor) == 1, "Unexpected original PS5169 source"
headers = (
    "#include <linux/bitops.h>\n"
    "#include <linux/errno.h>\n"
    "#include <linux/gpio/consumer.h>\n"
    "#include <linux/kernel.h>\n"
    "#include <linux/module.h>\n"
    "#include <linux/mutex.h>\n"
    "#include <linux/property.h>\n"
    "#include <linux/regulator/consumer.h>\n"
    "#include <linux/slab.h>\n"
    "#include <linux/types.h>\n"
    "#include <linux/usb/typec.h>\n"
)
source.write_text(original.replace(anchor, headers + anchor, 1))
