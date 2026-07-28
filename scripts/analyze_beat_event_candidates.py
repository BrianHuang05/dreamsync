"""Build inspectable beat observations and decode a stable adaptive beat grid.

This is an offline research pipeline.  It never reads verified labels while
extracting or decoding events; labels are used only for the reported evaluation
tables.  By default it processes development tracks only, so held-out overlays
cannot be inspected accidentally during decoder calibration.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Sequence

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from beat_grid_decoder import (  # noqa: E402
    BeatCandidate,
    DecoderConfig,
    candidates_to_frame,
    cluster_observations,
    decode_beat_grid,
    extract_brilliance_edges,
    extract_harmonic_novelty,
    extract_pooled_mel_spikes,
    extract_sub_corners,
)
from beat_model_analysis import TimelineRecord, console_safe, match_event_times  # noqa: E402
from diagnose_beat_bands import add_derived_signals, parse_float_list  # noqa: E402
from tune_beat_ridge import load_dataset, resolve_path  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=Path("out/beat-ml-analysis/frame_features.csv.gz"))
    parser.add_argument("--manifest", type=Path, default=Path("out/beat-ml-analysis/verified_dataset_manifest.csv"))
    parser.add_argument("--splits", type=Path, default=Path("out/beat-ml-analysis/track_split.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("out/beat-band-diagnostics/grid-decoder"))
    parser.add_argument("--baseline-metrics", type=Path, default=None)
    parser.add_argument("--match-tolerances", type=parse_float_list, default=(0.10, 0.25, 0.30))
    parser.add_argument("--window-seconds", type=float, default=30.0)
    parser.add_argument("--agreement-seconds", type=float, default=0.10)
    parser.add_argument("--minimum-event-seconds", type=float, default=0.12)
    parser.add_argument("--track-scope", choices=("development", "heldout", "all"), default="development")
    parser.add_argument("--hard-case-substring", default="Last Train Home")
    parser.add_argument("--max-track-seconds", type=float, default=None)
    parser.add_argument("--allow-existing", action="store_true", help="Append only when explicitly resuming the same experiment directory.")
    parser.add_argument("--minimum-period-seconds", type=float, default=0.30)
    parser.add_argument("--maximum-period-seconds", type=float, default=1.00)
    parser.add_argument("--period-step-seconds", type=float, default=0.025)
    parser.add_argument("--snap-window-seconds", type=float, default=0.10)
    parser.add_argument("--beam-width", type=int, default=24)
    parser.add_argument("--octave-evidence-span", type=int, default=3)
    parser.add_argument("--octave-transition-lockout-beats", type=int, default=16)
    parser.add_argument("--octave-min-confidence", type=float, default=0.55)
    parser.add_argument("--octave-min-relative-score", type=float, default=0.90)
    return parser.parse_args(argv)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _track_role(track_id: str, args: argparse.Namespace, development_tracks: set[str]) -> str:
    if args.hard_case_substring.casefold() in track_id.casefold():
        return "hard_case"
    return "development" if track_id in development_tracks else "heldout"


def _event_rows(track_id: str, observations: Sequence[object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for observation in observations:
        rows.append(
            {
                "track_id": track_id,
                "t": observation.t,
                "family": observation.family,
                "score": observation.score,
                "prominence": observation.prominence,
                "width_seconds": observation.width_seconds,
                "metadata_json": json.dumps(dict(observation.metadata), ensure_ascii=False),
            }
        )
    return rows


def add_tempo_phase_support(candidates: pd.DataFrame) -> pd.DataFrame:
    """Retain the legacy independent candidate baseline for comparison only."""
    result = candidates.copy()
    if result.empty:
        for name in ("tempo_period_seconds", "tempo_phase_support", "adaptive_score", "selected_adaptive"):
            result[name] = pd.Series(dtype="float64" if name != "selected_adaptive" else "bool")
        return result
    times = result["t"].to_numpy(dtype=np.float64)
    scores = result["local_confidence"].to_numpy(dtype=np.float64)
    periods = np.arange(0.30, 1.001, 0.02)
    best_period = np.full(len(result), np.nan)
    support = np.zeros(len(result), dtype=np.float64)
    for index, time in enumerate(times):
        prior = np.flatnonzero((times[:index] >= time - 1.12) & (times[:index] <= time - 0.24))
        if not prior.size:
            continue
        differences = time - times[prior]
        period_indices = np.abs(differences[:, None] - periods[None, :]).argmin(axis=1)
        timing_error = np.abs(differences - periods[period_indices])
        candidate_support = scores[prior] - timing_error / 0.12
        local_best = int(np.argmax(candidate_support))
        if candidate_support[local_best] > 0:
            support[index] = candidate_support[local_best]
            best_period[index] = periods[period_indices[local_best]]
    adaptive = scores + 0.65 * support
    result["tempo_period_seconds"] = best_period
    result["tempo_phase_support"] = support
    result["adaptive_score"] = adaptive
    selected = np.zeros(len(result), dtype=bool)
    for index in np.argsort(adaptive)[::-1]:
        if adaptive[index] <= 0:
            continue
        if not selected[np.abs(times - times[index]) < 0.24].any():
            selected[index] = True
    result["selected_adaptive"] = selected
    return result


def metrics_row(
    track_id: str,
    track_role: str,
    method: str,
    predicted: np.ndarray,
    actual: np.ndarray,
    tolerance: float,
    duration: float,
    window_start: float | None = None,
) -> dict[str, object]:
    metrics = match_event_times(predicted, actual, tolerance=tolerance, miss_penalty=0.5)
    minutes = max(duration / 60.0, 1e-9)
    return {
        "track_id": track_id,
        "track_role": track_role,
        "window_start_seconds": window_start,
        "method": method,
        "tolerance_seconds": tolerance,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1": metrics.f1,
        "matched_mae_ms": metrics.matched_mae_ms,
        "predicted_events": metrics.predicted_events,
        "actual_events": metrics.actual_events,
        "matched_events": metrics.matched_events,
        "false_positives": metrics.predicted_events - metrics.matched_events,
        "false_negatives": metrics.actual_events - metrics.matched_events,
        "false_positives_per_minute": (metrics.predicted_events - metrics.matched_events) / minutes,
        "false_negatives_per_minute": (metrics.actual_events - metrics.matched_events) / minutes,
    }


def _method_predictions(candidates: pd.DataFrame, decoded_times: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "mel_spike": candidates.loc[candidates["mel_spike_score"] > 0, "t"].to_numpy(dtype=np.float64),
        "brilliance_edge": candidates.loc[candidates["brilliance_edge_score"] > 0, "t"].to_numpy(dtype=np.float64),
        "harmonic_novelty": candidates.loc[candidates["harmonic_novelty_score"] > 0, "t"].to_numpy(dtype=np.float64),
        "sub_corner": candidates.loc[candidates["sub_corner_score"] > 0, "t"].to_numpy(dtype=np.float64),
        "cross_signal_consensus": candidates.loc[candidates["family_count"] >= 2, "t"].to_numpy(dtype=np.float64),
        "adaptive_tempo_phase": candidates.loc[candidates["selected_adaptive"], "t"].to_numpy(dtype=np.float64),
        "stable_grid_decoder": decoded_times,
    }


def evaluate_track(
    track_id: str,
    track_role: str,
    candidates: pd.DataFrame,
    decoded_times: np.ndarray,
    timeline: TimelineRecord,
    start: float,
    end: float,
    tolerances: Sequence[float],
    window_seconds: float,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    actual = timeline.beat_times[(timeline.beat_times >= start) & (timeline.beat_times <= end)]
    methods = _method_predictions(candidates, decoded_times)
    track_rows = [
        metrics_row(track_id, track_role, method, predicted, actual, tolerance, end - start)
        for tolerance in tolerances
        for method, predicted in methods.items()
    ]
    window_rows: list[dict[str, object]] = []
    window_start = start
    while window_start <= end:
        window_end = min(end, window_start + window_seconds)
        window_actual = actual[(actual >= window_start) & (actual <= window_end)]
        for tolerance in tolerances:
            for method, predicted in methods.items():
                window_predicted = predicted[(predicted >= window_start) & (predicted <= window_end)]
                window_rows.append(
                    metrics_row(
                        track_id,
                        track_role,
                        method,
                        window_predicted,
                        window_actual,
                        tolerance,
                        window_end - window_start,
                        window_start,
                    )
                )
        window_start = window_end + 1e-6
    return track_rows, window_rows


def _decoder_rows(track_id: str, decoded: object) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for beat in decoded.beats:
        nearest_class = decoded.candidate_classes[beat.candidate_index] if beat.candidate_index is not None else "none"
        rows.append(
            {
                "track_id": track_id,
                "time": beat.t,
                "time_seconds": beat.t,
                "period_seconds": beat.period_seconds,
                "BPM": beat.bpm,
                "bpm": beat.bpm,
                "local_confidence": beat.local_confidence,
                "grid_confidence": beat.grid_confidence,
                "candidate_snapped": beat.candidate_snapped,
                "candidate_index": beat.candidate_index,
                "nearest_unused_event_class": nearest_class,
                "transition_confidence": beat.transition_confidence,
            }
        )
    return rows


def _segment_decoder_rows(track_id: str, track_role: str, decoded_rows: list[dict[str, object]], candidates: pd.DataFrame, start: float, end: float, window_seconds: float) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    decoded = pd.DataFrame(decoded_rows)
    window_start = start
    while window_start <= end:
        window_end = min(end, window_start + window_seconds)
        beats = decoded.loc[(decoded["time_seconds"] >= window_start) & (decoded["time_seconds"] <= window_end)] if not decoded.empty else decoded
        local_candidates = candidates.loc[(candidates["t"] >= window_start) & (candidates["t"] <= window_end)]
        periods = beats["period_seconds"].to_numpy(dtype=np.float64) if not beats.empty else np.empty(0)
        slopes = np.diff(np.log(np.maximum(periods, 1e-9))) if len(periods) > 1 else np.empty(0)
        suppressed = local_candidates["subdivision_class"].isin(["eighth", "triplet", "sixteenth"]).sum()
        harmony = local_candidates["harmonic_novelty_score"].to_numpy(dtype=np.float64)
        transient = local_candidates["brilliance_edge_score"].to_numpy(dtype=np.float64)
        agreement = float(np.mean((harmony > 0) == (transient > 0))) if len(local_candidates) else 0.0
        mean_confidence = float(beats["grid_confidence"].mean()) if not beats.empty else 0.0
        duration = max(window_end - window_start, 1e-9)
        rows.append(
            {
                "track_id": track_id,
                "track_role": track_role,
                "window_start_seconds": window_start,
                "window_end_seconds": window_end,
                "mean_grid_confidence": mean_confidence,
                "subdivision_candidates_per_minute": suppressed / (duration / 60.0),
                "mean_abs_tempo_slope": float(np.mean(np.abs(slopes))) if len(slopes) else 0.0,
                "harmonic_transient_agreement": agreement,
                "flag_low_grid_confidence": mean_confidence < 0.45,
                "flag_high_subdivision_density": suppressed / (duration / 60.0) > 20.0,
                "flag_rapid_tempo_change": bool(len(slopes) and np.max(np.abs(slopes)) > 0.08),
                "flag_low_harmonic_transient_agreement": agreement < 0.35,
            }
        )
        window_start = window_end + 1e-6
    return rows


def _cohort_summary(track_metrics: pd.DataFrame) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    cohorts = {
        "development": track_metrics["track_role"].eq("development"),
        "heldout": track_metrics["track_role"].eq("heldout"),
        "hard_case": track_metrics["track_role"].eq("hard_case"),
        "development_including_hard_case": track_metrics["track_role"].isin(["development", "hard_case"]),
    }
    fields = ["precision", "recall", "f1", "matched_mae_ms", "false_positives_per_minute", "false_negatives_per_minute"]
    for cohort, mask in cohorts.items():
        source = track_metrics.loc[mask]
        if source.empty:
            continue
        grouped = source.groupby(["method", "tolerance_seconds"], as_index=False)[fields].mean()
        for record in grouped.to_dict(orient="records"):
            rows.append({"cohort": cohort, **record})
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.window_seconds <= 0 or args.agreement_seconds <= 0 or args.minimum_event_seconds <= 0:
        raise ValueError("window, agreement, and event intervals must be positive")
    for name in ("features", "manifest", "splits", "output_dir"):
        setattr(args, name, resolve_path(getattr(args, name), REPO_ROOT))
    if args.baseline_metrics is not None:
        args.baseline_metrics = resolve_path(args.baseline_metrics, REPO_ROOT)
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.allow_existing:
        raise FileExistsError(f"Refusing to overwrite existing experiment directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    frame_df, timelines, development_tracks, test_tracks = load_dataset(args)
    if args.track_scope == "development":
        requested_tracks = development_tracks
    elif args.track_scope == "heldout":
        requested_tracks = test_tracks
    else:
        requested_tracks = development_tracks | test_tracks
    decoder_config = DecoderConfig(
        minimum_period_seconds=args.minimum_period_seconds,
        maximum_period_seconds=args.maximum_period_seconds,
        period_step_seconds=args.period_step_seconds,
        snap_window_seconds=args.snap_window_seconds,
        beam_width=args.beam_width,
        octave_evidence_span=args.octave_evidence_span,
        octave_transition_lockout_beats=args.octave_transition_lockout_beats,
        octave_min_confidence=args.octave_min_confidence,
        octave_min_relative_score=args.octave_min_relative_score,
    )
    decoder_config.validate()
    hop = float(np.median(np.diff(frame_df["t"].to_numpy(dtype=np.float64))))
    enriched, _ = add_derived_signals(frame_df, ["brilliance_ratio"], (0.40,), (2.0,), hop)
    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    mel_columns = sorted(
        [column for column in frame_df.columns if column.startswith("log_mel_") and 26 <= int(column.rsplit("_", 1)[1]) <= 31],
        key=lambda column: int(column.rsplit("_", 1)[1]),
    )
    chroma_columns = sorted([column for column in frame_df.columns if column.startswith("chroma_")])
    brilliance_column = next((column for column in enriched.columns if column.startswith("brilliance_ratio__autocorr_")), None)
    if not mel_columns or not chroma_columns or brilliance_column is None:
        raise ValueError("Feature cache is missing required mel, chroma, or brilliance autocorrelation columns")

    observation_rows: list[dict[str, object]] = []
    candidate_frames: list[pd.DataFrame] = []
    decoded_rows: list[dict[str, object]] = []
    tempo_rows: list[dict[str, object]] = []
    track_metric_rows: list[dict[str, object]] = []
    window_metric_rows: list[dict[str, object]] = []
    segment_rows: list[dict[str, object]] = []
    diagnostic_rows: list[dict[str, object]] = []
    path_summaries: dict[str, object] = {}

    for track_index, track_id in enumerate(sorted(requested_tracks), start=1):
        rows = enriched.loc[enriched["track_id"].eq(track_id)].reset_index(drop=True)
        times = rows["t"].to_numpy(dtype=np.float64)
        observations = (
            extract_pooled_mel_spikes(
                times,
                rows[mel_columns].to_numpy(dtype=np.float64),
                [int(column.rsplit("_", 1)[1]) for column in mel_columns],
                minimum_seconds=args.minimum_event_seconds,
            )
            + extract_brilliance_edges(
                times,
                rows[brilliance_column].to_numpy(dtype=np.float64),
                lag_seconds=0.40,
                minimum_seconds=args.minimum_event_seconds,
            )
            + extract_sub_corners(
                times,
                rows["sub_ratio"].to_numpy(dtype=np.float64),
                minimum_seconds=args.minimum_event_seconds,
            )
            + extract_harmonic_novelty(
                times,
                rows[chroma_columns].to_numpy(dtype=np.float64),
                spectral_centroid=rows["spectral_centroid"].to_numpy(dtype=np.float64),
                spectral_rolloff=rows["spectral_rolloff_85"].to_numpy(dtype=np.float64),
                minimum_seconds=args.minimum_event_seconds,
            )
        )
        candidates: tuple[BeatCandidate, ...] = cluster_observations(observations, agreement_seconds=args.agreement_seconds)
        decoded = decode_beat_grid(candidates, start_time=float(times[0]), end_time=float(times[-1]), config=decoder_config)
        candidate_frame = add_tempo_phase_support(candidates_to_frame(candidates))
        if candidate_frame.empty:
            candidate_frame = pd.DataFrame(columns=["t", "mel_spike_score", "brilliance_edge_score", "harmonic_novelty_score", "sub_corner_score", "family_count", "selected_adaptive", "subdivision_class"])
        else:
            candidate_frame["subdivision_class"] = list(decoded.candidate_classes)
            candidate_frame["suppressed_subdivision"] = candidate_frame["subdivision_class"].isin(["eighth", "triplet", "sixteenth"])
        candidate_frame.insert(0, "track_id", track_id)
        role = _track_role(track_id, args, development_tracks)
        track_decoded_rows = _decoder_rows(track_id, decoded)
        observation_rows.extend(_event_rows(track_id, observations))
        candidate_frames.append(candidate_frame)
        decoded_rows.extend(track_decoded_rows)
        tempo_rows.extend(
            {"track_id": track_id, "time_seconds": row["time_seconds"], "period_seconds": row["period_seconds"], "bpm": row["bpm"]}
            for row in track_decoded_rows
        )
        path_summaries[track_id] = {
            **dict(decoded.path_summary),
            "states": [
                {
                    "beat_time": beat.t,
                    "period_seconds": beat.period_seconds,
                    "tempo_slope": (math.log(beat.period_seconds / track_decoded_rows[index - 1]["period_seconds"]) if index else 0.0),
                    "grid_confidence": beat.grid_confidence,
                    "candidate_snapped": beat.candidate_snapped,
                    "transition_confidence": beat.transition_confidence,
                }
                for index, beat in enumerate(decoded.beats)
            ],
        }
        decoded_times = np.asarray([row["time_seconds"] for row in track_decoded_rows], dtype=np.float64)
        metrics, windows = evaluate_track(
            track_id,
            role,
            candidate_frame,
            decoded_times,
            timeline_by_track[track_id],
            float(times[0]),
            float(times[-1]),
            args.match_tolerances,
            args.window_seconds,
        )
        track_metric_rows.extend(metrics)
        window_metric_rows.extend(windows)
        segment_rows.extend(_segment_decoder_rows(track_id, role, track_decoded_rows, candidate_frame, float(times[0]), float(times[-1]), args.window_seconds))
        decoded_periods = np.asarray([row["period_seconds"] for row in track_decoded_rows], dtype=np.float64)
        tempo_slopes = np.diff(np.log(np.maximum(decoded_periods, 1e-9))) if len(decoded_periods) > 1 else np.empty(0)
        class_counts = candidate_frame["subdivision_class"].value_counts().to_dict() if "subdivision_class" in candidate_frame else {}
        diagnostic_rows.append(
            {
                "track_id": track_id,
                "track_role": role,
                "beat_period_variance": float(np.var(decoded_periods)) if len(decoded_periods) else float("nan"),
                "mean_abs_tempo_slope": float(np.mean(np.abs(tempo_slopes))) if len(tempo_slopes) else 0.0,
                "max_abs_tempo_slope": float(np.max(np.abs(tempo_slopes))) if len(tempo_slopes) else 0.0,
                "tempo_octave_transitions": int(decoded.path_summary.get("octave_transitions", 0)),
                "candidate_snapped_count": int(sum(row["candidate_snapped"] for row in track_decoded_rows)),
                "low_confidence_grid_beat_count": int(sum(row["grid_confidence"] < decoder_config.low_confidence_threshold for row in track_decoded_rows)),
                "beat_candidates": int(class_counts.get("beat", 0)),
                "eighth_candidates": int(class_counts.get("eighth", 0)),
                "triplet_candidates": int(class_counts.get("triplet", 0)),
                "sixteenth_candidates": int(class_counts.get("sixteenth", 0)),
                "off_grid_candidates": int(class_counts.get("off_grid", 0)),
            }
        )
        print(console_safe(f"[{track_index}/{len(requested_tracks)}] {track_id}: {len(candidates)} candidates, {len(decoded.beats)} grid beats"), flush=True)

    candidate_df = pd.concat(candidate_frames, ignore_index=True) if candidate_frames else pd.DataFrame()
    observation_df = pd.DataFrame(observation_rows)
    decoded_df = pd.DataFrame(decoded_rows)
    track_metrics = pd.DataFrame(track_metric_rows)
    window_metrics = pd.DataFrame(window_metric_rows)
    segment_metrics = pd.DataFrame(segment_rows)
    observation_df.to_csv(args.output_dir / "event_observations.csv", index=False)
    candidate_df.to_csv(args.output_dir / "event_candidates.csv", index=False)
    decoded_df.to_csv(args.output_dir / "decoded_beats.csv", index=False)
    pd.DataFrame(tempo_rows).to_csv(args.output_dir / "tempo_curve.csv", index=False)
    track_metrics.to_csv(args.output_dir / "track_metrics.csv", index=False)
    window_metrics.to_csv(args.output_dir / "window_metrics.csv", index=False)
    segment_metrics.to_csv(args.output_dir / "segment_metrics.csv", index=False)
    pd.DataFrame(diagnostic_rows).to_csv(args.output_dir / "decoder_diagnostics.csv", index=False)
    (args.output_dir / "decoder_path.json").write_text(json.dumps(path_summaries, indent=2, allow_nan=False), encoding="utf-8")

    config_payload = {
        "feature_families": ["pooled_mel_spike", "brilliance_edges", "harmonic_novelty", "sub_corner"],
        "minimum_event_seconds": args.minimum_event_seconds,
        "agreement_seconds": args.agreement_seconds,
        "match_tolerances_seconds": list(args.match_tolerances),
        "track_scope": args.track_scope,
        "max_track_seconds": args.max_track_seconds,
        "hard_case_substring": args.hard_case_substring,
        "decoder": asdict(decoder_config),
        "source_hashes": {"features": _sha256(args.features), "manifest": _sha256(args.manifest), "splits": _sha256(args.splits)},
        "split_identifiers": {"development": sorted(development_tracks), "heldout": sorted(test_tracks)},
    }
    (args.output_dir / "experiment_config.json").write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
    baseline_source = args.baseline_metrics if args.baseline_metrics and args.baseline_metrics.is_file() else None
    if baseline_source is not None:
        (args.output_dir / "baseline_metrics.json").write_text(baseline_source.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        baseline_rows = track_metrics.loc[track_metrics["method"].eq("adaptive_tempo_phase")]
        (args.output_dir / "baseline_metrics.json").write_text(json.dumps({"method": "adaptive_tempo_phase", "metrics": _cohort_summary(baseline_rows)}, indent=2, allow_nan=True), encoding="utf-8")
    summary = {
        "track_count": int(len(requested_tracks)),
        "candidate_count": int(len(candidate_df)),
        "decoded_beat_count": int(len(decoded_df)),
        "cohort_metrics": _cohort_summary(track_metrics),
        "notes": [
            "Verified labels are used only for evaluation and are never edited.",
            "The stable grid owns beat placement; local candidates only refine a predicted grid time.",
            "Held-out tracks must be run with --track-scope heldout after development configuration is locked.",
        ],
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=True), encoding="utf-8")
    print(f"wrote grid-decoder experiment to {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
