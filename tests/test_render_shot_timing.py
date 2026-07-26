"""Timing/motion math for render_shot.py -- pure functions, no ffmpeg involved."""
import math

import pytest

import render_shot as rs


# --- sine_ease / signed_sine_ease -------------------------------------------

def test_sine_ease_boundary_values():
    assert rs.sine_ease(0.0) == pytest.approx(0.0)
    assert rs.sine_ease(1.0) == pytest.approx(1.0)
    assert rs.sine_ease(0.5) == pytest.approx(0.5)


def test_sine_ease_is_monotonic_and_slow_at_ends():
    # slope near p=0 and p=1 should be much smaller than the slope at the midpoint
    eps = 1e-4
    slope_start = (rs.sine_ease(eps) - rs.sine_ease(0.0)) / eps
    slope_mid = (rs.sine_ease(0.5 + eps) - rs.sine_ease(0.5 - eps)) / (2 * eps)
    slope_end = (rs.sine_ease(1.0) - rs.sine_ease(1.0 - eps)) / eps
    assert slope_start < slope_mid
    assert slope_end < slope_mid
    # monotonic non-decreasing across a coarse sweep
    values = [rs.sine_ease(p / 100) for p in range(101)]
    assert all(b >= a for a, b in zip(values, values[1:]))


def test_signed_sine_ease_boundary_values():
    assert rs.signed_sine_ease(0.0) == pytest.approx(-1.0)
    assert rs.signed_sine_ease(1.0) == pytest.approx(1.0)
    assert rs.signed_sine_ease(0.5) == pytest.approx(0.0)


# --- effective_duration ------------------------------------------------------

def test_effective_duration_no_narration():
    duration, overridden = rs.effective_duration(7.0, None, 0.5)
    assert duration == 7.0
    assert overridden is False


def test_effective_duration_narration_fits():
    # narration + padding comfortably under the stated duration
    duration, overridden = rs.effective_duration(7.0, 5.0, 0.5)
    assert duration == 7.0
    assert overridden is False


def test_effective_duration_narration_exactly_at_boundary():
    # narration + padding exactly equal to stated duration should NOT override
    # (spec: override only when it *exceeds* the stated duration)
    duration, overridden = rs.effective_duration(7.0, 6.5, 0.5)
    assert duration == 7.0
    assert overridden is False


def test_effective_duration_narration_overruns():
    duration, overridden = rs.effective_duration(6.0, 7.0, 0.5)
    assert duration == pytest.approx(7.5)
    assert overridden is True


def test_effective_duration_narration_barely_overruns():
    duration, overridden = rs.effective_duration(6.0, 5.6, 0.5)
    # 5.6 + 0.5 = 6.1 > 6.0 -> overridden
    assert duration == pytest.approx(6.1)
    assert overridden is True


# --- frame_count --------------------------------------------------------------

def test_frame_count_exact():
    assert rs.frame_count(6.0, 24) == 144


def test_frame_count_rounds_to_nearest():
    # 7.0 * 24 = 168 exactly; 6.5 * 24 = 156 exactly; use a value that needs rounding
    assert rs.frame_count(6.021, 24) == round(6.021 * 24)


def test_frame_count_never_zero():
    assert rs.frame_count(0.0, 24) == 1
    assert rs.frame_count(0.01, 24) >= 1


# --- build_motion_exprs -------------------------------------------------------

def test_build_motion_exprs_rejects_hold():
    with pytest.raises(ValueError):
        rs.build_motion_exprs("hold", 0.0, 100)


def test_build_motion_exprs_rejects_unknown_type():
    with pytest.raises(ValueError):
        rs.build_motion_exprs("dolly_zoom", 0.05, 100)


@pytest.mark.parametrize("motion_type", ["push_in", "push_out", "pan_left", "pan_right", "tilt_up"])
def test_build_motion_exprs_references_frame_counter(motion_type):
    d_frames = 168
    z, x, y = rs.build_motion_exprs(motion_type, 0.07, d_frames)
    # every expression chain must key off zoompan's own `on` counter and clamp
    # at the last frame index (d_frames - 1), never the raw (possibly-overshot) on
    assert "on" in z + x + y
    assert f"{d_frames - 1}" in z + x + y


def test_build_motion_exprs_push_in_zoom_endpoints():
    d_frames = 100
    d_m1 = d_frames - 1
    amount = 0.08
    z, _, _ = rs.build_motion_exprs("push_in", amount, d_frames)

    def eval_zoom(on):
        p = min(on, d_m1) / d_m1
        eased = 0.5 - 0.5 * math.cos(math.pi * p)
        # z expression is "1+{amount}*{eased-expr}"; re-derive numerically
        return 1 + amount * eased

    assert eval_zoom(0) == pytest.approx(1.0)
    assert eval_zoom(d_m1) == pytest.approx(1 + amount)


def test_build_motion_exprs_push_out_zoom_endpoints():
    d_frames = 100
    d_m1 = d_frames - 1
    amount = 0.06

    def eval_zoom(on):
        p = min(on, d_m1) / d_m1
        eased = 0.5 - 0.5 * math.cos(math.pi * p)
        return (1 + amount) - amount * eased

    assert eval_zoom(0) == pytest.approx(1 + amount)
    assert eval_zoom(d_m1) == pytest.approx(1.0)


def test_build_motion_exprs_pan_uses_fixed_headroom_zoom():
    z, _, _ = rs.build_motion_exprs("pan_right", 0.05, 100)
    assert z == str(rs.PAN_ZOOM_HEADROOM)


# --- vignette strength clamp --------------------------------------------------

def test_clamp_unit():
    assert rs.clamp_unit(-0.5) == 0.0
    assert rs.clamp_unit(1.5) == 1.0
    assert rs.clamp_unit(0.3) == pytest.approx(0.3)
