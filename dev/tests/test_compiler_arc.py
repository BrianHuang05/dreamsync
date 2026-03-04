"""Tests for dreamsync.compiler.arc — Narrative Arc Planner."""

from __future__ import annotations

import pytest

from dreamsync.analyzer.sections import Section
from dreamsync.compiler.arc import ArcWeight, NarrativeArcPlanner


def make_section(
    label: str = "verse",
    energy_mean: float = 0.5,
    mood: str = "groove",
    bpm: float = 120.0,
    start_t: float = 0.0,
    end_t: float = 30.0,
    section_id: str = "A",
) -> Section:
    return Section(
        start_t=start_t,
        end_t=end_t,
        label=label,
        energy_mean=energy_mean,
        mood=mood,
        bpm=bpm,
        section_id=section_id,
    )


# ---------------------------------------------------------------------------
# 1. test_basic_arc_shape
# ---------------------------------------------------------------------------
def test_basic_arc_shape():
    """Standard verse-chorus-verse-chorus: repeated sections gain intensity."""
    planner = NarrativeArcPlanner()
    sections = (
        make_section(label="verse",  energy_mean=0.4, section_id="A", start_t=0,  end_t=30),
        make_section(label="chorus", energy_mean=0.7, section_id="B", start_t=30, end_t=60),
        make_section(label="verse",  energy_mean=0.4, section_id="A", start_t=60, end_t=90),
        make_section(label="chorus", energy_mean=0.7, section_id="B", start_t=90, end_t=120),
    )
    weights = planner.plan(sections)
    assert len(weights) == 4

    # Second verse should be more intense than first verse (buildup ramp)
    assert weights[2].final_intensity > weights[0].final_intensity
    # Second chorus should be more intense than first chorus (buildup ramp + peak boost)
    assert weights[3].final_intensity > weights[1].final_intensity


# ---------------------------------------------------------------------------
# 2. test_intro_cap
# ---------------------------------------------------------------------------
def test_intro_cap():
    """Intro section with high energy_mean is capped at intro_intensity."""
    planner = NarrativeArcPlanner(intro_intensity=0.15)
    sections = (
        make_section(label="intro", energy_mean=0.8),
    )
    weights = planner.plan(sections)
    assert len(weights) == 1
    # final_intensity should be capped near intro_intensity (0.15)
    assert weights[0].final_intensity == pytest.approx(0.15, abs=0.02)


# ---------------------------------------------------------------------------
# 3. test_outro_cap
# ---------------------------------------------------------------------------
def test_outro_cap():
    """Outro section with high energy_mean is capped at outro_intensity."""
    planner = NarrativeArcPlanner(outro_intensity=0.10)
    sections = (
        make_section(label="outro", energy_mean=0.9),
    )
    weights = planner.plan(sections)
    assert len(weights) == 1
    assert weights[0].final_intensity == pytest.approx(0.10, abs=0.02)


# ---------------------------------------------------------------------------
# 4. test_final_chorus_boost
# ---------------------------------------------------------------------------
def test_final_chorus_boost():
    """Last chorus gets peak_boost; it should be the highest intensity."""
    planner = NarrativeArcPlanner(peak_boost=1.2)
    sections = (
        make_section(label="verse",  energy_mean=0.5, start_t=0,  end_t=30),
        make_section(label="chorus", energy_mean=0.7, start_t=30, end_t=60),
        make_section(label="verse",  energy_mean=0.5, start_t=60, end_t=90),
        make_section(label="chorus", energy_mean=0.7, start_t=90, end_t=120),
    )
    weights = planner.plan(sections)
    # Last chorus (index 3) should have the highest final_intensity
    max_intensity = max(w.final_intensity for w in weights)
    assert weights[3].final_intensity == pytest.approx(max_intensity)
    # The peak boost should be reflected in the arc_multiplier
    assert weights[3].arc_multiplier > weights[1].arc_multiplier


# ---------------------------------------------------------------------------
# 5. test_final_drop_boost
# ---------------------------------------------------------------------------
def test_final_drop_boost():
    """Last drop (not chorus) gets peak_boost — drop label also triggers boost."""
    planner = NarrativeArcPlanner(peak_boost=1.2)
    sections = (
        make_section(label="verse", energy_mean=0.5, start_t=0,  end_t=30),
        make_section(label="drop",  energy_mean=0.8, start_t=30, end_t=60),
        make_section(label="verse", energy_mean=0.5, start_t=60, end_t=90),
        make_section(label="drop",  energy_mean=0.8, start_t=90, end_t=120),
    )
    weights = planner.plan(sections)
    # Last drop (index 3) should have peak_boost applied
    assert weights[3].arc_multiplier > weights[1].arc_multiplier
    # It should be the highest
    max_intensity = max(w.final_intensity for w in weights)
    assert weights[3].final_intensity == pytest.approx(max_intensity)


# ---------------------------------------------------------------------------
# 6. test_bridge_dip
# ---------------------------------------------------------------------------
def test_bridge_dip():
    """Bridge section intensity uses bridge_reduction."""
    planner = NarrativeArcPlanner(bridge_reduction=0.80)
    sections = (
        make_section(label="verse",  energy_mean=0.5, start_t=0,  end_t=30),
        make_section(label="bridge", energy_mean=0.5, start_t=30, end_t=60),
        make_section(label="verse",  energy_mean=0.5, start_t=60, end_t=90),
    )
    weights = planner.plan(sections)
    # Bridge intensity should be approximately energy_mean * bridge_reduction
    assert weights[1].final_intensity == pytest.approx(0.5 * 0.80, abs=0.01)


# ---------------------------------------------------------------------------
# 7. test_breakdown_dip
# ---------------------------------------------------------------------------
def test_breakdown_dip():
    """Breakdown label also gets the same reduction as bridge."""
    planner = NarrativeArcPlanner(bridge_reduction=0.80)
    sections = (
        make_section(label="verse",     energy_mean=0.5, start_t=0,  end_t=30),
        make_section(label="breakdown", energy_mean=0.5, start_t=30, end_t=60),
        make_section(label="verse",     energy_mean=0.5, start_t=60, end_t=90),
    )
    weights = planner.plan(sections)
    assert weights[1].final_intensity == pytest.approx(0.5 * 0.80, abs=0.01)


# ---------------------------------------------------------------------------
# 8. test_repeated_sections_ramp
# ---------------------------------------------------------------------------
def test_repeated_sections_ramp():
    """Three verses: verse 3 intensity > verse 2 > verse 1."""
    planner = NarrativeArcPlanner(buildup_ramp=0.15)
    sections = (
        make_section(label="verse", energy_mean=0.4, start_t=0,  end_t=30, section_id="A"),
        make_section(label="verse", energy_mean=0.4, start_t=30, end_t=60, section_id="A"),
        make_section(label="verse", energy_mean=0.4, start_t=60, end_t=90, section_id="A"),
    )
    weights = planner.plan(sections)
    assert weights[2].final_intensity > weights[1].final_intensity
    assert weights[1].final_intensity > weights[0].final_intensity


# ---------------------------------------------------------------------------
# 9. test_single_section
# ---------------------------------------------------------------------------
def test_single_section():
    """Song with one section: arc_multiplier = 1.0, final_intensity = clamp(energy_mean)."""
    planner = NarrativeArcPlanner()
    sections = (
        make_section(label="verse", energy_mean=0.6),
    )
    weights = planner.plan(sections)
    assert len(weights) == 1
    assert weights[0].arc_multiplier == pytest.approx(1.0)
    assert weights[0].final_intensity == pytest.approx(0.6, abs=0.01)


# ---------------------------------------------------------------------------
# 10. test_all_same_label
# ---------------------------------------------------------------------------
def test_all_same_label():
    """Song where every section is 'verse': each gets progressively higher intensity."""
    planner = NarrativeArcPlanner(buildup_ramp=0.15)
    sections = tuple(
        make_section(label="verse", energy_mean=0.3, start_t=i * 30, end_t=(i + 1) * 30)
        for i in range(5)
    )
    weights = planner.plan(sections)
    assert len(weights) == 5
    for i in range(1, len(weights)):
        assert weights[i].final_intensity > weights[i - 1].final_intensity


# ---------------------------------------------------------------------------
# 11. test_no_chorus_no_drop
# ---------------------------------------------------------------------------
def test_no_chorus_no_drop():
    """Song with no chorus or drop: no peak boost applied, intensities still valid."""
    planner = NarrativeArcPlanner(peak_boost=1.2)
    sections = (
        make_section(label="intro", energy_mean=0.2, start_t=0,  end_t=15),
        make_section(label="verse", energy_mean=0.5, start_t=15, end_t=45),
        make_section(label="bridge", energy_mean=0.4, start_t=45, end_t=75),
        make_section(label="outro", energy_mean=0.2, start_t=75, end_t=90),
    )
    weights = planner.plan(sections)
    assert len(weights) == 4
    # No section should have peak_boost in its arc_multiplier
    # peak_boost=1.2 multiplies the arc_multiplier, so no section's multiplier should
    # reflect 1.2x unless it's a chorus/drop. For verse at occurrence 0, multiplier is 1.0.
    assert weights[1].arc_multiplier == pytest.approx(1.0)
    # All final_intensity values should be within [0.05, 1.0]
    for w in weights:
        assert 0.05 <= w.final_intensity <= 1.0


# ---------------------------------------------------------------------------
# 12. test_clamp_bounds
# ---------------------------------------------------------------------------
def test_clamp_bounds():
    """Verify final_intensity is always in [0.05, 1.0]: energy_mean=0.0 and energy_mean=2.0."""
    planner = NarrativeArcPlanner()
    sections = (
        make_section(label="verse", energy_mean=0.0, start_t=0,  end_t=30),
        make_section(label="verse", energy_mean=2.0, start_t=30, end_t=60),
    )
    weights = planner.plan(sections)
    assert len(weights) == 2
    # energy_mean=0.0 should clamp to lower bound of 0.05
    assert weights[0].final_intensity == pytest.approx(0.05)
    # energy_mean=2.0 should clamp to upper bound of 1.0
    assert weights[1].final_intensity == pytest.approx(1.0)
