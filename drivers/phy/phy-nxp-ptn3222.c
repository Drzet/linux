// SPDX-License-Identifier: GPL-2.0
/*
 * Copyright (c) 2024, Linaro Limited
 */

#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/phy/phy.h>
#include <linux/regmap.h>
#include <linux/regulator/consumer.h>

#define NUM_SUPPLIES 2
#define PTN3222_VDD3V3 0
#define PTN3222_VDD1V8 1
#define PTN3222_USB2_RX_CONTROL 0x06
#define PTN3222_USB2_TX_CONTROL1 0x07
#define PTN3222_USB2_TX_CONTROL2 0x08
#define PTN3222_REVISION_ID 0x13
#define PTN3222_CHIP_ID_0 0x14
#define PTN3222_CHIP_ID_CUK 0x023222
#define PTN3222_CHIP_ID_HUK 0x013222

struct ptn3222 {
	struct i2c_client *client;
	struct phy *phy;
	struct gpio_desc *reset_gpio;
	struct regulator_bulk_data *supplies;
	struct regmap *regmap;
	enum phy_mode mode;
	bool oneli_profile;
	bool initialized;
	bool faulted;
	bool enabled[NUM_SUPPLIES];
	bool voltage_set[NUM_SUPPLIES];
};

/* Motorola kernel-msm 0450c5076a8e, repeater-i2c-eusb2.c and stock DT. */
static int ptn3222_oneli_power_off(struct ptn3222 *ptn3222)
{
	static const int max_uv[] = { 3300000, 1800000 };
	int i, ret;

	/* Remove the 3 V vote before 1.8 V; retain state if cleanup fails. */
	for (i = PTN3222_VDD3V3; i < NUM_SUPPLIES; i++) {
		if (ptn3222->enabled[i]) {
			ret = regulator_disable(ptn3222->supplies[i].consumer);
			if (ret)
				return ret;
			ptn3222->enabled[i] = false;
		}
		if (ptn3222->voltage_set[i]) {
			ret = regulator_set_voltage(ptn3222->supplies[i].consumer,
						    0, max_uv[i]);
			if (ret)
				return ret;
			ptn3222->voltage_set[i] = false;
		}
	}

	return 0;
}

static int ptn3222_oneli_power_on(struct ptn3222 *ptn3222)
{
	static const int min_uv[] = { 3075000, 1800000 };
	static const int max_uv[] = { 3300000, 1800000 };
	int i, ret;

	/* Unlike regulator_bulk_enable(), this guarantees vote ordering. */
	for (i = PTN3222_VDD1V8; i >= PTN3222_VDD3V3; i--) {
		ret = regulator_set_voltage(ptn3222->supplies[i].consumer,
					    min_uv[i], max_uv[i]);
		if (ret)
			return ret;
		ptn3222->voltage_set[i] = true;
		ret = regulator_enable(ptn3222->supplies[i].consumer);
		if (ret)
			return ret;
		ptn3222->enabled[i] = true;
	}

	return 0;
}

static int ptn3222_check_id(struct ptn3222 *ptn3222)
{
	unsigned int revision, val, id = 0;
	int i, ret;

	ret = regmap_read(ptn3222->regmap, PTN3222_REVISION_ID, &revision);
	if (ret)
		return ret;
	for (i = 0; i < 3; i++) {
		ret = regmap_read(ptn3222->regmap, PTN3222_CHIP_ID_0 + i, &val);
		if (ret)
			return ret;
		id |= val << (8 * i);
	}

	/* NXP CUK Rev. 1.2 table 9; HUK Rev. 2.0 table 8. */
	if (id != PTN3222_CHIP_ID_CUK && id != PTN3222_CHIP_ID_HUK)
		return dev_err_probe(&ptn3222->client->dev, -ENODEV,
				     "unsupported chip ID %#06x (revision %#02x)\n",
				     id, revision);

	dev_dbg(&ptn3222->client->dev, "chip ID %#06x revision %#02x\n",
		id, revision);
	return 0;
}

static int ptn3222_apply_profile(struct ptn3222 *ptn3222, enum phy_mode mode)
{
	int ret;

	/* Exact ordered Motorola value/register pairs, no raw-register DT ABI. */
	ret = regmap_write(ptn3222->regmap, PTN3222_USB2_RX_CONTROL, 0x40);
	if (ret)
		return ret;
	ret = regmap_write(ptn3222->regmap, PTN3222_USB2_TX_CONTROL1, 0x22);
	if (ret)
		return ret;
	ret = regmap_write(ptn3222->regmap, PTN3222_USB2_TX_CONTROL2,
			   mode == PHY_MODE_USB_HOST ? 0x24 : 0x64);
	if (ret)
		return ret;

	/* NXP CUK Rev. 1.2 table 33: configuration takes 0.5 ms. */
	usleep_range(500, 1000);
	return 0;
}

static void ptn3222_abort(struct ptn3222 *ptn3222)
{
	int ret;

	gpiod_set_value_cansleep(ptn3222->reset_gpio, 1);
	ptn3222->initialized = false;
	ret = ptn3222_oneli_power_off(ptn3222);
	ptn3222->faulted = !!ret;
	if (ret)
		dev_err(&ptn3222->client->dev, "power cleanup failed: %d\n", ret);
}

static void ptn3222_cleanup(void *data)
{
	ptn3222_abort(data);
}

static int ptn3222_init(struct phy *phy)
{
	struct ptn3222 *ptn3222 = phy_get_drvdata(phy);
	int ret;

	if (ptn3222->oneli_profile) {
		if (ptn3222->faulted)
			return -EBUSY;
		ret = ptn3222_oneli_power_on(ptn3222);
		if (ret)
			goto abort;
	} else {
		ret = regulator_bulk_enable(NUM_SUPPLIES, ptn3222->supplies);
	}
	if (ret)
		return ret;

	gpiod_set_value_cansleep(ptn3222->reset_gpio, 0);

	if (ptn3222->oneli_profile) {
		/* NXP CUK Rev. 1.2 table 33: startup after both rails are valid. */
		usleep_range(1000, 1500);
		ret = ptn3222_check_id(ptn3222);
		if (ret)
			goto abort;
		ret = ptn3222_apply_profile(ptn3222, ptn3222->mode);
		if (ret)
			goto abort;
	}
	ptn3222->initialized = true;
	return 0;

abort:
	ptn3222_abort(ptn3222);
	return ret;
}

static int ptn3222_exit(struct phy *phy)
{
	struct ptn3222 *ptn3222 = phy_get_drvdata(phy);

	gpiod_set_value_cansleep(ptn3222->reset_gpio, 1);
	ptn3222->initialized = false;
	if (ptn3222->oneli_profile) {
		int ret = ptn3222_oneli_power_off(ptn3222);

		ptn3222->faulted = !!ret;
		return ret;
	}

	return regulator_bulk_disable(NUM_SUPPLIES, ptn3222->supplies);
}

static int ptn3222_set_mode(struct phy *phy, enum phy_mode mode, int submode)
{
	struct ptn3222 *ptn3222 = phy_get_drvdata(phy);
	int ret, rollback;

	if (!ptn3222->oneli_profile)
		return 0;
	if (mode != PHY_MODE_USB_HOST && mode != PHY_MODE_USB_DEVICE &&
	    mode != PHY_MODE_USB_OTG)
		return -EINVAL;
	if (ptn3222->faulted)
		return -EIO;
	if (ptn3222->initialized && mode != ptn3222->mode) {
		ret = ptn3222_apply_profile(ptn3222, mode);
		if (ret) {
			/* Preserve the previous role, or fail closed if replay fails. */
			rollback = ptn3222_apply_profile(ptn3222, ptn3222->mode);
			if (rollback) {
				ptn3222_abort(ptn3222);
				ptn3222->faulted = true;
			}
			return ret;
		}
	}
	ptn3222->mode = mode;
	return 0;
}

static const struct phy_ops ptn3222_ops = {
	.init		= ptn3222_init,
	.exit		= ptn3222_exit,
	.set_mode	= ptn3222_set_mode,
	.owner		= THIS_MODULE,
};

static const struct regulator_bulk_data ptn3222_supplies[NUM_SUPPLIES] = {
	{
		.supply = "vdd3v3",
		.init_load_uA = 11000,
	}, {
		.supply = "vdd1v8",
		.init_load_uA = 55000,
	}
};

static const struct regulator_bulk_data ptn3222_oneli_supplies[NUM_SUPPLIES] = {
	{ .supply = "vdd3v3", .init_load_uA = 3500 },
	{ .supply = "vdd1v8", .init_load_uA = 32000 },
};

static const struct regmap_config ptn3222_regmap_config = {
	.reg_bits = 8,
	.val_bits = 8,
	.max_register = 0x16,
};

static int ptn3222_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct phy_provider *phy_provider;
	struct ptn3222 *ptn3222;
	const char *profile;
	int ret;

	ptn3222 = devm_kzalloc(dev, sizeof(*ptn3222), GFP_KERNEL);
	if (!ptn3222)
		return -ENOMEM;

	ptn3222->client = client;
	ptn3222->mode = PHY_MODE_USB_DEVICE;
	if (of_find_property(dev->of_node, "nxp,usb2-tuning-profile", NULL)) {
		ret = of_property_read_string(dev->of_node,
					      "nxp,usb2-tuning-profile", &profile);
		if (ret)
			return ret;
		if (strcmp(profile, "motorola,oneli"))
			return -EINVAL;
		ptn3222->oneli_profile = true;
		/* A profile must not silently acquire dummy power providers. */
		if (!of_find_property(dev->of_node, "vdd1v8-supply", NULL) ||
		    !of_find_property(dev->of_node, "vdd3v3-supply", NULL))
			return -EINVAL;
	}

	ptn3222->reset_gpio = devm_gpiod_get_optional(dev, "reset",
						      GPIOD_OUT_HIGH);
	if (IS_ERR(ptn3222->reset_gpio))
		return dev_err_probe(dev, PTR_ERR(ptn3222->reset_gpio),
				     "unable to acquire reset gpio\n");
	if (ptn3222->oneli_profile && !ptn3222->reset_gpio)
		return -EINVAL;

	ret = devm_regulator_bulk_get_const(dev, NUM_SUPPLIES,
					    ptn3222->oneli_profile ?
					    ptn3222_oneli_supplies : ptn3222_supplies,
					    &ptn3222->supplies);
	if (ret)
		return ret;
	if (ptn3222->oneli_profile) {
		ptn3222->regmap = devm_regmap_init_i2c(client, &ptn3222_regmap_config);
		if (IS_ERR(ptn3222->regmap))
			return dev_err_probe(dev, PTR_ERR(ptn3222->regmap),
					     "failed to initialize regmap\n");
		ret = devm_add_action_or_reset(dev, ptn3222_cleanup, ptn3222);
		if (ret)
			return ret;
	}

	ptn3222->phy = devm_phy_create(dev, dev->of_node, &ptn3222_ops);
	if (IS_ERR(ptn3222->phy)) {
		dev_err(dev, "failed to create PHY: %ld\n", PTR_ERR(ptn3222->phy));
		return PTR_ERR(ptn3222->phy);
	}

	phy_set_drvdata(ptn3222->phy, ptn3222);

	phy_provider = devm_of_phy_provider_register(dev, of_phy_simple_xlate);

	return PTR_ERR_OR_ZERO(phy_provider);
}

static const struct i2c_device_id ptn3222_table[] = {
	{ .name = "ptn3222" },
	{ }
};
MODULE_DEVICE_TABLE(i2c, ptn3222_table);

static const struct of_device_id ptn3222_of_table[] = {
	{ .compatible = "nxp,ptn3222" },
	{ }
};
MODULE_DEVICE_TABLE(of, ptn3222_of_table);

static struct i2c_driver ptn3222_driver = {
	.driver = {
		.name = "ptn3222",
		.of_match_table = ptn3222_of_table,
	},
	.probe = ptn3222_probe,
	.id_table = ptn3222_table,
};

module_i2c_driver(ptn3222_driver);

MODULE_DESCRIPTION("NXP PTN3222 eUSB2 Redriver driver");
MODULE_LICENSE("GPL");
