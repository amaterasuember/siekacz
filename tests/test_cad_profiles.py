from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cad.commands import AddEntityCommand, CommandStack, RepairProfileCommand
from cad.model import CadDocument, CircleEntity, LineEntity, Point2D, RectangleEntity
from cad.profiles import (
    ProfileIssueType,
    ProfileTolerance,
    analyze_profile,
    build_repair_plan,
)


def _document_with(*entities):
    document = CadDocument.create()
    stack = CommandStack(document)
    for entity in entities:
        stack.execute(AddEntityCommand(document.active_sketch_id, entity))
    return document, stack


def test_closed_nested_profiles_are_valid_and_oriented() -> None:
    plate = RectangleEntity(Point2D(-50, -30), 100, 60)
    hole = CircleEntity(Point2D(0, 0), 10)
    construction = LineEntity(Point2D(-100, 0), Point2D(100, 0), construction=True)
    document, _stack = _document_with(plate, hole, construction)
    report = analyze_profile(document.active_sketch)
    assert report.is_valid_surface
    assert len(report.loops) == 2
    assert sorted(loop.nesting_depth for loop in report.loops) == [0, 1]
    assert construction.id not in report.profile_entity_ids
    print("[OK] closed outer profile and nested hole form a valid surface")


def test_small_gap_is_reported_and_repaired_with_one_undo_step() -> None:
    lines = (
        LineEntity(Point2D(0, 0), Point2D(100, 0)),
        LineEntity(Point2D(100.05, 0), Point2D(100, 100)),
        LineEntity(Point2D(100, 100), Point2D(0, 100)),
        LineEntity(Point2D(0, 100), Point2D(0, 0)),
    )
    document, stack = _document_with(*lines)
    tolerance = ProfileTolerance(join=0.1)
    before = analyze_profile(document.active_sketch, tolerance)
    assert sum(issue.issue_type == ProfileIssueType.SMALL_GAP for issue in before.issues) == 1
    assert before.open_endpoint_count == 2
    plan = build_repair_plan(document.active_sketch, before)
    assert len(plan.coincident_constraints) == 1
    undo_count = len(stack.undo_commands)
    stack.execute(RepairProfileCommand(document.active_sketch_id, plan))
    assert len(stack.undo_commands) == undo_count + 1
    after = analyze_profile(document.active_sketch, tolerance)
    assert after.is_valid_surface
    assert after.open_endpoint_count == 0
    assert stack.undo()
    restored = analyze_profile(document.active_sketch, tolerance)
    assert restored.open_endpoint_count == 2
    assert stack.redo()
    assert analyze_profile(document.active_sketch, tolerance).is_valid_surface
    print("[OK] a small gap is closed by one reversible coincident-constraint repair")


def test_duplicates_and_micro_segments_are_removed_reversibly() -> None:
    first = RectangleEntity(Point2D(0, 0), 80, 40)
    duplicate = RectangleEntity(Point2D(0, 0), 80, 40)
    micro = LineEntity(Point2D(200, 0), Point2D(200.005, 0))
    document, stack = _document_with(first, duplicate, micro)
    report = analyze_profile(document.active_sketch)
    kinds = {issue.issue_type for issue in report.issues}
    assert ProfileIssueType.DUPLICATE in kinds
    assert ProfileIssueType.MICRO_SEGMENT in kinds
    plan = build_repair_plan(document.active_sketch, report, remove_micro_segments=True)
    assert set(plan.duplicate_entity_ids) == {duplicate.id}
    assert set(plan.micro_entity_ids) == {micro.id}
    stack.execute(RepairProfileCommand(document.active_sketch_id, plan))
    assert document.active_sketch.order == [first.id]
    assert analyze_profile(document.active_sketch).is_valid_surface
    assert stack.undo()
    assert document.active_sketch.order == [first.id, duplicate.id, micro.id]
    print("[OK] duplicate and micro geometry repair preserves exact undo state")


def test_crossings_and_overlaps_block_surface_use() -> None:
    bow = (
        LineEntity(Point2D(0, 0), Point2D(100, 100)),
        LineEntity(Point2D(100, 100), Point2D(0, 100)),
        LineEntity(Point2D(0, 100), Point2D(100, 0)),
        LineEntity(Point2D(100, 0), Point2D(0, 0)),
    )
    document, _stack = _document_with(*bow)
    report = analyze_profile(document.active_sketch)
    assert any(issue.issue_type == ProfileIssueType.SELF_INTERSECTION for issue in report.issues)
    assert not report.is_valid_surface

    crossing_line = LineEntity(Point2D(-20, 0), Point2D(20, 0))
    circle = CircleEntity(Point2D(0, 0), 10)
    document, _stack = _document_with(crossing_line, circle)
    report = analyze_profile(document.active_sketch)
    assert any(
        issue.issue_type == ProfileIssueType.SELF_INTERSECTION and set(issue.entity_ids) == {crossing_line.id, circle.id}
        for issue in report.issues
    )
    assert not report.is_valid_surface
    print("[OK] line/line and line/circle self-intersections block face creation")


def test_shared_branch_point_is_not_mistaken_for_a_closed_surface() -> None:
    first = RectangleEntity(Point2D(0, 0), 40, 40)
    second = RectangleEntity(Point2D(40, 40), 40, 40)
    document, _stack = _document_with(first, second)
    report = analyze_profile(document.active_sketch)
    assert any(issue.issue_type == ProfileIssueType.BRANCH_POINT for issue in report.issues)
    assert not report.is_valid_surface
    print("[OK] a shared branch point is diagnosed as non-manifold profile topology")


if __name__ == "__main__":
    test_closed_nested_profiles_are_valid_and_oriented()
    test_small_gap_is_reported_and_repaired_with_one_undo_step()
    test_duplicates_and_micro_segments_are_removed_reversibly()
    test_crossings_and_overlaps_block_surface_use()
    test_shared_branch_point_is_not_mistaken_for_a_closed_surface()
    print("CAD PROFILE TESTS OK")
