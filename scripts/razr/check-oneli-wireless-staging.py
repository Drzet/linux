#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check the *compiled* enabled WCN6855 candidate topology and resolved Kconfig.

Uses dtc's fdtget, no access to hardware/firmware; does not execute physical radios.
"""
import argparse
import pathlib
import subprocess


def main():
    p = argparse.ArgumentParser()
    p.add_argument("dtb", type=pathlib.Path)
    p.add_argument("config", type=pathlib.Path)
    a = p.parse_args()

    def get(path, prop, kind="x"):
        cmd = ["fdtget", "-t", kind, str(a.dtb), path, prop]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def n(path, prop):
        return [int(x, 16) for x in get(path, prop).split()]

    def has(path, prop):
        return bool(n(path, prop))

    def check(expr, message):
        if not expr:
            raise AssertionError(message)

    soc = "/soc@0"
    rsc = soc + "/rsc@17a00000"
    tlmm = soc + "/pinctrl@f100000"
    pmu = "/wcn6855-pmu"
    pcie = soc + "/pcie@1c00000"
    wifi = pcie + "/pcie@0/wifi@0"
    qup2 = soc + "/geniqup@8c0000"
    uart20 = qup2 + "/serial@894000"
    uart7 = soc + "/geniqup@9c0000/serial@99c000"
    bt = uart20 + "/bluetooth"
    rails = {
        "s10b": (rsc + "/regulators-0/smps10", 1800000, 1800000, 320000, 8000),
        "s11b": (rsc + "/regulators-0/smps11", 966000, 1170000, 320000, 8000),
        "s12b": (rsc + "/regulators-0/smps12", 1350000, 2024000, 320000, 8000),
        "s1c": (rsc + "/regulators-1/smps1", 1900000, 2024000, 320000, 16000),
        "bobc": (rsc + "/regulators-1/bob", None, None, None, None),
        "s2e": (rsc + "/regulators-3/smps2", 1012000, 1040000, 300000, 4000),
        "l7e": (rsc + "/regulators-3/ldo7", 2800000, 2800000, 1504000, 8000),
    }
    phandles = {}
    for name, (path, low, high, base, step) in rails.items():
        phandles[name] = n(path, "phandle")[0]
        if low is None:
            # BOB is not a programmable fixed-voltage battery substitute.
            continue
        check(n(path, "regulator-min-microvolt") == [low], name + " min")
        check(n(path, "regulator-max-microvolt") == [high], name + " max")
        first = base + max(0, (low - base + step - 1) // step) * step
        last = base + ((high - base) // step) * step
        check(low <= first <= last <= high, f"{name}: no selector within range")
        print(f"SELECTOR {name}: {first}..{last} uV (requested {low}..{high})")

    supply = {
        "vddio-supply": "s10b",
        "vddaon-supply": "s11b",
        "vddpmu-supply": "s12b",
        "vddpmumx-supply": "s2e",
        "vddpmucx-supply": "s11b",
        "vddrfa0p95-supply": "s11b",
        "vddrfa1p3-supply": "s12b",
        "vddrfa1p9-supply": "s1c",
        "vddpcie1p3-supply": "s12b",
        "vddpcie1p9-supply": "s1c",
        "vddasd-supply": "l7e",
    }
    for prop, name in supply.items():
        check(n(pmu, prop) == [phandles[name]], f"{prop} must reference {name}")
    check(len(supply) == 11, "ten inputs plus one shared ASD")

    for root, prop, name in [
        (rsc + "/regulators-0", "vdd-l2-l7-supply", "bobc"),
        (rsc + "/regulators-0", "vdd-l3-l5-supply", "s11b"),
        (rsc + "/regulators-0", "vdd-l6-l9-l10-supply", "s12b"),
        (rsc + "/regulators-1", "vdd-l2-l8-supply", "s1c"),
        (rsc + "/regulators-3", "vdd-l7-bob-supply", "bobc"),
        (rsc + "/regulators-1", "vdd-l10-supply", "s12b"),
    ]:
        check(n(root, prop) == [phandles[name]], prop + " upstream graph")

    # The complete chosen radio chain is active, including its providers.
    for node in [
        rsc + "/regulators-0",
        rsc + "/regulators-1",
        rsc + "/regulators-3",
        pmu,
        pcie,
        soc + "/phy@1c06000",
        wifi,
        qup2,
        uart20,
        bt,
    ]:
        check(get(node, "status", "s") == "okay", node + " unexpectedly disabled")
    # WLAN_EN must not be forced low by pinctrl before GPIOD_ASIS probe.
    gpio80 = tlmm + "/oneli-radio-wlan-en-state"
    for property_name in ("output-low", "output-high"):
        forced = subprocess.run(["fdtget", str(a.dtb), gpio80, property_name],
                                capture_output=True, text=True)
        check(forced.returncode != 0, "WLAN_EN level forced by pinctrl")
    check(get(gpio80, "pins", "s") == "gpio80", "WLAN_EN TLMM state")
    # RPMh uses for_each_available_child_of_node: do not register or
    # apply constraints to two UFS-only outputs merely by enabling PM8350.
    for unused in [rsc + "/regulators-0/ldo7", rsc + "/regulators-0/ldo9"]:
        check(get(unused, "status", "s") == "disabled",
              unused + " unrelated UFS-only regulator activated")
    # Guard existing nonradio bring-up boundaries; this is not blanket enablement.
    for inactive in [soc + "/usb@a6f8800", soc + "/geniqup@9c0000/i2c@994000",
                     soc + "/phy@88e000"]:
        probe = subprocess.run(["fdtget", "-t", "s", str(a.dtb), inactive, "status"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            check(probe.stdout.strip() == "disabled", inactive + " newly enabled")
    check(n(pmu, "wlan-enable-gpios")[1] == 80, "wlan-en ownership")
    check(n(pmu, "bt-enable-gpios")[1] == 81, "bt-en ownership")
    check(n(pmu, "swctrl-gpios")[1] == 82, "switch state ownership")
    check(n(pmu, "xo-clk-gpios")[1] == 204, "XO ownership")
    check(n(pcie, "perst-gpios")[1] == 94, "PCIE PERST GPIO")
    check(n(pcie, "wake-gpios")[1] == 96, "PCIE WAKE GPIO")
    check(get(tlmm + "/qup-uart20-default-state", "pins", "s").split()
          == ["gpio76", "gpio77", "gpio78", "gpio79"], "UART20 pad mux")
    check(get("/aliases", "serial0", "s") == uart7, "serial0 changed")
    check(get("/chosen", "stdout-path", "s") == "serial0:115200n8",
          "diagnostic stdout changed")
    check(get(uart7, "status", "s") == "okay", "UART7 disabled")

    config = a.config.read_text().splitlines()
    cfg = dict(s.split("=", 1) for s in config if s.startswith("CONFIG_") and "=" in s)
    required = {
        "CONFIG_REGULATOR_QCOM_RPMH": "y",
        "CONFIG_FW_LOADER": "y",
        "CONFIG_POWER_SEQUENCING": "y",
        "CONFIG_POWER_SEQUENCING_QCOM_WCN": "y",
        "CONFIG_PCIE_QCOM": "y",
        "CONFIG_PHY_QCOM_QMP": "y",
        "CONFIG_PHY_QCOM_QMP_PCIE": "y",
        "CONFIG_PCI_PWRCTRL_GENERIC": "y",
        "CONFIG_PCI_PWRCTRL_PWRSEQ": "m",
        "CONFIG_ATH11K": "m",
        "CONFIG_ATH11K_PCI": "m",
        "CONFIG_MHI_BUS": "m",
        "CONFIG_QRTR": "m",
        "CONFIG_QRTR_MHI": "m",
        "CONFIG_BT": "m",
        "CONFIG_BT_LE": "y",
        "CONFIG_BT_HCIUART": "m",
        "CONFIG_BT_HCIUART_QCA": "y",
        "CONFIG_BT_QCA": "m",
        "CONFIG_SERIAL_QCOM_GENI": "y",
        "CONFIG_SERIAL_QCOM_GENI_CONSOLE": "y",
    }
    missing = {key: (want, cfg.get(key, "NOT SET"))
               for key, want in required.items() if cfg.get(key) != want}
    check(not missing, "resolved Kconfig differs: " + str(missing))
    print(f"PASS: {len(required)} resolved Kconfig requirements, "
          "seven actual regulator phandles, selector grids, 11 PMU supplies, "
          "six grouped parent edges, enabled ancestry, GPIO80 preservation and UART7 ownership")


if __name__ == "__main__":
    main()
