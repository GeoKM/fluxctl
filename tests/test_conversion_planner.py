from fluxctl.application.conversion_planner import (
    ConversionContext,
    LOGICALLY_EQUIVALENT,
    LOSSY_BUT_USEFUL,
    SECTOR_LOSSLESS,
    UNSUPPORTED,
    available_conversion_plans,
    plan_conversion,
)


def test_1541_routes_are_limited_to_semantic_targets() -> None:
    context = ConversionContext(
        source_kind="scp",
        layout_id="commodore_gcr_1541_170k",
        encoding="gcr",
    )

    plans = {plan.target: plan for plan in available_conversion_plans(context)}

    assert set(plans) == {"raw", "imd", "d64", "g64"}
    assert plans["d64"].classification == LOGICALLY_EQUIVALENT
    assert plans["d64"].allowed
    assert not plan_conversion(context, "adf").allowed
    assert plan_conversion(context, "adf").classification == UNSUPPORTED


def test_logical_containers_can_convert_without_physical_loss_warning() -> None:
    context = ConversionContext(
        source_kind="d64",
        layout_id="commodore_gcr_1541_170k",
        encoding="gcr",
    )

    plan = plan_conversion(context, "raw")

    assert plan.allowed
    assert plan.classification == SECTOR_LOSSLESS
    assert not plan.warnings


def test_amiga_imd_route_is_allowed_but_explicitly_lossy() -> None:
    context = ConversionContext(
        source_kind="scp",
        layout_id="amiga_mfm_880k",
        encoding="mfm",
    )

    plan = plan_conversion(context, "imd")

    assert plan.allowed
    assert plan.classification == LOSSY_BUT_USEFUL
    assert plan.warnings
    assert "native Amiga" in plan.reason


def test_1581_routes_require_1581_geometry() -> None:
    context = ConversionContext(
        source_kind="img",
        layout_id="ibm_mfm_720k",
        encoding="mfm",
    )

    plan = plan_conversion(context, "d81")

    assert not plan.allowed
    assert plan.classification == UNSUPPORTED
    assert "1581" in plan.reason


def test_apple_sector_order_targets_require_apple_layout() -> None:
    context = ConversionContext(
        source_kind="scp",
        layout_id="ibm_mfm_720k",
        encoding="mfm",
    )

    assert not plan_conversion(context, "po").allowed
    assert not plan_conversion(context, "do").allowed
