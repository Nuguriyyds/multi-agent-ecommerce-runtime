from __future__ import annotations

from smoke_test import DEMO_USERS, run_smoke_test


def test_run_smoke_test_executes_demo_scenarios():
    summary = run_smoke_test()

    assert summary["demo_users"] == DEMO_USERS
    assert summary["shared_product_for_copy_check"] in {
        "sku-iphone-16-pro",
        "sku-huawei-mate-70-pro",
        "sku-ipad-air-m3",
    }
    assert summary["zero_stock_filtered_product"] == "sku-iphone-16-pro"
    assert summary["cold_start_hot_recommendations"] == [
        "sku-iphone-16-pro",
        "sku-huawei-mate-70-pro",
        "sku-airpods-pro-3",
    ]
