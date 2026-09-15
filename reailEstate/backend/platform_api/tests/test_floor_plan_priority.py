from types import SimpleNamespace
from uuid import uuid4

from platform_api.floor_plan_rules import plan_display_priority, plan_source_kind


def plan(asset_type, media_type, confidence=0.8):
    return SimpleNamespace(
        id=uuid4(), plan_scope="flat", confidence=confidence, page_number=1,
        asset=SimpleNamespace(asset_type=asset_type),
        document=SimpleNamespace(declared_media_type=media_type),
    )


def test_pre_cut_source_layouts_are_prioritized_over_generated_crops():
    generated = plan("unit_crop", "application/pdf", confidence=0.95)
    precut = plan("source_image", "image/png", confidence=0.75)

    assert plan_source_kind(precut) == "precut_source"
    assert plan_source_kind(generated) == "generated_unit_crop"
    assert sorted([generated, precut], key=plan_display_priority) == [precut, generated]