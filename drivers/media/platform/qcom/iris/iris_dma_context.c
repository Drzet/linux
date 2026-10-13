// SPDX-License-Identifier: GPL-2.0-only
/* Experimental SM8475 nonsecure DMA consumer foundation. No DMA allocations. */
#include <linux/dma-mapping.h>
#include <linux/iommu.h>
#include <linux/module.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>

#include "iris_dma_context.h"

#define IRIS_DMA_END 0xe0000000ULL
#define IRIS_DMA_LIMIT 0x100000000ULL

struct iris_dma_config {
	enum iris_dma_role role;
	u32 sid;
	u64 start;
};

static const struct iris_dma_config iris_nonpixel = {
	.role = IRIS_DMA_NONPIXEL, .sid = 0x2180, .start = 0x25800000,
};

static const struct iris_dma_config iris_pixel = {
	.role = IRIS_DMA_PIXEL, .sid = 0x2187, .start = 0x00100000,
};

/* Later routing must check the whole buffer, not only its first DMA address. */
bool iris_dma_range_valid(enum iris_dma_role role, u64 address, u64 size)
{
	u64 start;

	if (role != IRIS_DMA_NONPIXEL && role != IRIS_DMA_PIXEL)
		return false;
	start = role == IRIS_DMA_NONPIXEL ? iris_nonpixel.start : iris_pixel.start;

	return size && address >= start && address < IRIS_DMA_END &&
		size <= IRIS_DMA_END - address;
}

static int iris_dma_reservation(struct device *dev, int index,
				u64 expected_start, u64 expected_size)
{
	struct device_node *node, *target;
	const __be32 *map, *next;
	phys_addr_t start = 0;
	size_t size = 0;
	int len, ret = -EINVAL;

	node = of_parse_phandle(dev->of_node, "memory-region", index);
	if (!node)
		return -EINVAL;

	/* Strict self-targeted, IOVA-only tuples avoid ambiguous core parsing. */
	if (of_property_present(node, "reg") || of_property_present(node, "size"))
		goto out;
	map = of_get_property(node, "iommu-addresses", &len);
	if (!map || len != 5 * sizeof(*map) ||
	    of_n_addr_cells(dev->of_node) != 2 || of_n_size_cells(dev->of_node) != 2)
		goto out;
	target = of_find_node_by_phandle(be32_to_cpup(map));
	if (target != dev->of_node) {
		of_node_put(target);
		goto out;
	}
	of_node_put(target);
	next = of_translate_dma_region(dev->of_node, map + 1, &start, &size);
	if (!next || next != map + 5 || !size || start != expected_start ||
	    size != expected_size || start > U64_MAX - size)
		goto out;
	ret = 0;
out:
	of_node_put(node);
	return ret;
}

static int iris_dma_reservations(struct device *dev, const struct iris_dma_config *cfg)
{
	struct iommu_resv_region *region;
	bool low = false, high = false;
	LIST_HEAD(regions);
	int ret;

	if (of_count_phandle_with_args(dev->of_node, "memory-region", NULL) != 2)
		return -EINVAL;
	ret = iris_dma_reservation(dev, 0, 0, cfg->start);
	if (ret)
		return ret;
	ret = iris_dma_reservation(dev, 1, IRIS_DMA_END, IRIS_DMA_LIMIT - IRIS_DMA_END);
	if (ret)
		return ret;

	/* Confirm the provider exposes both exclusions to the normal DMA path. */
	iommu_get_resv_regions(dev, &regions);
	list_for_each_entry(region, &regions, list) {
		if (region->type != IOMMU_RESV_RESERVED)
			continue;
		if (!region->start && region->length == cfg->start)
			low = true;
		if (region->start == IRIS_DMA_END &&
		    region->length == IRIS_DMA_LIMIT - IRIS_DMA_END)
			high = true;
	}
	iommu_put_resv_regions(dev, &regions);

	return low && high ? 0 : -EINVAL;
}

static int iris_dma_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	const struct iris_dma_config *cfg = of_device_get_match_data(dev);
	struct iommu_domain *domain;
	struct of_phandle_args args;
	struct iommu_group *group;
	int ret;

	if (!cfg || !of_dma_is_coherent(dev->of_node))
		return -EINVAL;
	if (of_count_phandle_with_args(dev->of_node, "iommus", "#iommu-cells") != 1)
		return -EINVAL;
	ret = of_parse_phandle_with_args(dev->of_node, "iommus", "#iommu-cells", 0, &args);
	if (ret)
		return ret;
	ret = args.args_count == 2 && args.args[0] == cfg->sid &&
		args.args[1] == 0x400 ? 0 : -EINVAL;
	of_node_put(args.np);
	if (ret)
		return ret;

	/* platform_dma_configure has already attached the normal default domain. */
	domain = iommu_get_domain_for_dev(dev);
	if (!domain || (domain->type != IOMMU_DOMAIN_DMA && domain->type != IOMMU_DOMAIN_DMA_FQ))
		return -EINVAL;
	group = iommu_group_get(dev);
	if (!group)
		return -EINVAL;
	iommu_group_put(group);

	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
	if (ret)
		return ret;
	ret = iris_dma_reservations(dev, cfg);
	if (ret)
		return dev_err_probe(dev, ret, "invalid Iris IOVA exclusions\n");

	/* Published only after every check. No controller dependency or allocation. */
	platform_set_drvdata(pdev, (void *)cfg);
	return 0;
}

static void iris_dma_remove(struct platform_device *pdev)
{
	/* Managed links force controller teardown before supplier DMA cleanup. */
	platform_set_drvdata(pdev, NULL);
}

static void iris_dma_put(void *data)
{
	put_device(data);
}

static int iris_dma_get_one(struct device *dev, enum iris_dma_role role,
			    struct device **result)
{
	const struct iris_dma_config *cfg;
	struct platform_device *pdev;
	struct device_node *node;
	struct device *supplier;
	int ret = -EPROBE_DEFER;

	node = of_parse_phandle(dev->of_node, "qcom,dma-consumers", role);
	if (!node)
		return -EINVAL;
	if (!of_device_is_available(node) ||
	    !of_device_is_compatible(node, role == IRIS_DMA_NONPIXEL ?
				     "qcom,sm8475-iris-nonpixel" : "qcom,sm8475-iris-pixel")) {
		of_node_put(node);
		return -EINVAL;
	}
	pdev = of_find_device_by_node(node);
	of_node_put(node);
	if (!pdev)
		return -EPROBE_DEFER;
	supplier = &pdev->dev;

	/* Serialize readiness + link creation with supplier driver unbind. */
	device_lock(supplier);
	if (!device_is_bound(supplier))
		goto unlock;
	if (supplier->driver != &iris_dma_context_driver.driver) {
		ret = -EINVAL;
		goto unlock;
	}
	cfg = dev_get_drvdata(supplier);
	if (!cfg || cfg->role != role) {
		ret = -EINVAL;
		goto unlock;
	}
	if (!device_link_add(dev, supplier,
			     DL_FLAG_PM_RUNTIME | DL_FLAG_AUTOREMOVE_CONSUMER)) {
		ret = -ENOMEM;
		goto unlock;
	}
	ret = 0;
unlock:
	device_unlock(supplier);
	if (ret) {
		put_device(supplier);
		return ret;
	}

	/* Never device_link_del() a managed link; driver core owns its lifetime. */
	ret = devm_add_action_or_reset(dev, iris_dma_put, supplier);
	if (!ret)
		*result = supplier;
	return ret;
}

int iris_dma_contexts_get(struct device *dev, struct device **contexts)
{
	struct iommu_domain *nonpixel, *pixel;
	struct iommu_group *ng, *pg;
	struct device_node *np, *px;
	int ret;

	contexts[0] = NULL;
	contexts[1] = NULL;
	if (of_count_phandle_with_args(dev->of_node, "qcom,dma-consumers", NULL) != 2)
		return -EINVAL;
	np = of_parse_phandle(dev->of_node, "qcom,dma-consumers", 0);
	px = of_parse_phandle(dev->of_node, "qcom,dma-consumers", 1);
	ret = !np || !px || np == px ? -EINVAL : 0;
	of_node_put(np);
	of_node_put(px);
	if (ret)
		return ret;
	ret = iris_dma_get_one(dev, IRIS_DMA_NONPIXEL, &contexts[0]);
	if (ret)
		return ret;
	ret = iris_dma_get_one(dev, IRIS_DMA_PIXEL, &contexts[1]);
	if (ret)
		return ret;

	nonpixel = iommu_get_domain_for_dev(contexts[0]);
	pixel = iommu_get_domain_for_dev(contexts[1]);
	ng = iommu_group_get(contexts[0]);
	pg = iommu_group_get(contexts[1]);
	ret = !nonpixel || !pixel || nonpixel == pixel || !ng || !pg || ng == pg ? -EINVAL : 0;
	iommu_group_put(ng);
	iommu_group_put(pg);

	/* On failure caller must fail probe: devres and managed links then unwind. */
	return ret;
}

static const struct of_device_id iris_dma_match[] = {
	{ .compatible = "qcom,sm8475-iris-nonpixel", .data = &iris_nonpixel },
	{ .compatible = "qcom,sm8475-iris-pixel", .data = &iris_pixel },
	{ }
};
MODULE_DEVICE_TABLE(of, iris_dma_match);

struct platform_driver iris_dma_context_driver = {
	.probe = iris_dma_probe,
	.remove = iris_dma_remove,
	.driver = {
		.name = "qcom-iris-dma-context",
		.of_match_table = iris_dma_match,
	},
};
