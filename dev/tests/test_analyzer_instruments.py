from __future__ import annotations

import pytest

from dreamsync.analyzer.features import FeatureRow
from dreamsync.analyzer.instruments import InstrumentHeuristicAnalyzer
from dreamsync.analyzer.phrases import Phrase
from dreamsync.live import EQ_BAND_NAMES


def _band_vector(**values: float) -> tuple[float, ...]:
    return tuple(values.get(name, 0.0) for name in EQ_BAND_NAMES)


def _chroma_vector(index: int, value: float = 1.0) -> tuple[float, ...]:
    values = [0.0] * 12
    values[index % 12] = value
    return tuple(values)


def _make_features(
    start_t: float,
    end_t: float,
    *,
    dt: float = 0.5,
    bass_ratio: float = 0.0,
    kick_flux: float = 0.0,
    onset_strength: float = 0.0,
    spectral_flux: float = 0.0,
    pan_center: float = 0.0,
    pan_width: float = 0.0,
    band_ratios: tuple[float, ...] | None = None,
    band_fluxes: tuple[float, ...] | None = None,
    band_pan_centers: tuple[float, ...] | None = None,
    chroma: tuple[float, ...] | None = None,
) -> list[FeatureRow]:
    rows: list[FeatureRow] = []
    t = start_t
    while t < end_t:
        rows.append(
            FeatureRow(
                t=t,
                rms=0.3,
                zcr=0.05,
                centroid=2000.0,
                bass_ratio=bass_ratio,
                spectral_flux=spectral_flux,
                kick_spectral_flux=kick_flux,
                onset_strength=onset_strength,
                energy=0.5,
                bpm=120.0,
                beat=False,
                mood="groove",
                pan_center=pan_center,
                pan_width=pan_width,
                band_ratios=band_ratios or _band_vector(),
                band_fluxes=band_fluxes or _band_vector(),
                band_pan_centers=band_pan_centers or _band_vector(),
                chroma=chroma or _chroma_vector(0),
            )
        )
        t += dt
    return rows


class TestInstrumentHeuristicAnalyzer:
    def test_bass_phrase_scores_bass_proxy_highest(self):
        phrase = Phrase(
            start_t=0.0,
            end_t=8.0,
            parent_section_index=0,
            phrase_type="steady",
            energy_delta=0.0,
            has_kick=True,
            band_ratios=_band_vector(sub=0.18, bass=0.34, low_mid=0.08),
            band_fluxes=_band_vector(bass=0.06, kick=0.05),
            dominant_band="bass",
        )
        features = _make_features(
            0.0,
            8.0,
            bass_ratio=0.42,
            onset_strength=0.10,
            spectral_flux=0.12,
            kick_flux=0.08,
            band_ratios=_band_vector(sub=0.18, bass=0.34, low_mid=0.08),
            band_fluxes=_band_vector(bass=0.06, kick=0.05),
            chroma=_chroma_vector(0),
        )

        proxy = InstrumentHeuristicAnalyzer().analyze([phrase], features)[0]
        assert proxy.dominant_proxy == "bass"
        assert proxy.bass > proxy.drums
        assert proxy.bass > proxy.vocals
        assert proxy.bass >= 0.45

    def test_percussive_phrase_scores_drums_and_percussive(self):
        phrase = Phrase(
            start_t=8.0,
            end_t=16.0,
            parent_section_index=1,
            phrase_type="drop",
            energy_delta=0.1,
            has_kick=True,
            band_ratios=_band_vector(kick=0.12, bass=0.10, mid=0.06),
            band_fluxes=_band_vector(kick=0.30, presence=0.04),
            dominant_band="kick",
        )
        features = _make_features(
            8.0,
            16.0,
            bass_ratio=0.16,
            onset_strength=0.55,
            spectral_flux=0.60,
            kick_flux=0.62,
            band_ratios=_band_vector(kick=0.12, bass=0.10, mid=0.06),
            band_fluxes=_band_vector(kick=0.30, presence=0.04),
            chroma=_chroma_vector(0, value=0.1),
        )

        proxy = InstrumentHeuristicAnalyzer().analyze([phrase], features)[0]
        assert proxy.dominant_proxy in {"drums", "percussive"}
        assert proxy.drums >= 0.3
        assert proxy.percussive >= 0.45
        assert proxy.drums > proxy.vocals

    def test_presence_phrase_scores_vocals_highest(self):
        phrase = Phrase(
            start_t=16.0,
            end_t=24.0,
            parent_section_index=2,
            phrase_type="steady",
            energy_delta=0.0,
            has_kick=False,
            band_ratios=_band_vector(low_mid=0.12, mid=0.24, presence=0.28),
            band_fluxes=_band_vector(presence=0.10, kick=0.02),
            dominant_band="presence",
        )
        features = _make_features(
            16.0,
            24.0,
            bass_ratio=0.08,
            onset_strength=0.08,
            spectral_flux=0.12,
            kick_flux=0.03,
            band_ratios=_band_vector(low_mid=0.12, mid=0.24, presence=0.28),
            band_fluxes=_band_vector(presence=0.10, kick=0.02),
            chroma=_chroma_vector(4),
        )

        proxy = InstrumentHeuristicAnalyzer().analyze([phrase], features)[0]
        assert proxy.dominant_proxy == "vocals"
        assert proxy.vocals > proxy.drums
        assert proxy.vocals > proxy.bass
        assert proxy.vocals >= 0.45
        assert proxy.active_proxies[0] == "vocals"

    def test_proxy_preserves_phrase_pan_summary(self):
        phrase = Phrase(
            start_t=24.0,
            end_t=32.0,
            parent_section_index=3,
            phrase_type="steady",
            energy_delta=0.0,
            has_kick=False,
            band_ratios=_band_vector(mid=0.20, presence=0.24),
            band_fluxes=_band_vector(presence=0.05),
            dominant_band="presence",
        )
        features = _make_features(
            24.0,
            32.0,
            pan_center=0.42,
            pan_width=0.36,
            band_ratios=_band_vector(mid=0.20, presence=0.24),
            band_fluxes=_band_vector(presence=0.05),
            band_pan_centers=_band_vector(mid=0.18, presence=0.46),
            chroma=_chroma_vector(7),
        )

        proxy = InstrumentHeuristicAnalyzer().analyze([phrase], features)[0]
        assert proxy.pan_center == pytest.approx(0.42, abs=1e-4)
        assert proxy.pan_width == pytest.approx(0.36, abs=1e-4)
        pan_map = dict(zip(EQ_BAND_NAMES, proxy.band_pan_centers))
        assert pan_map["presence"] > pan_map["mid"]

    def test_high_flux_phrase_does_not_saturate_all_proxy_scores(self):
        phrase = Phrase(
            start_t=32.0,
            end_t=40.0,
            parent_section_index=4,
            phrase_type="steady",
            energy_delta=0.0,
            has_kick=True,
            band_ratios=_band_vector(
                sub=0.08,
                kick=0.12,
                bass=0.23,
                low_mid=0.22,
                mid=0.16,
                presence=0.11,
                air=0.08,
            ),
            band_fluxes=_band_vector(
                sub=11.0,
                kick=31.0,
                bass=49.0,
                low_mid=32.0,
                mid=29.0,
                presence=14.0,
                air=7.0,
            ),
            dominant_band="bass",
        )
        features = _make_features(
            32.0,
            40.0,
            bass_ratio=0.24,
            kick_flux=31.0,
            onset_strength=0.42,
            spectral_flux=48.0,
            pan_center=0.03,
            pan_width=0.41,
            band_ratios=phrase.band_ratios,
            band_fluxes=phrase.band_fluxes,
            band_pan_centers=_band_vector(bass=-0.04, presence=0.03),
            chroma=_chroma_vector(5),
        )

        proxy = InstrumentHeuristicAnalyzer().analyze([phrase], features)[0]
        scores = {
            "drums": proxy.drums,
            "bass": proxy.bass,
            "vocals": proxy.vocals,
            "harmonic": proxy.harmonic,
            "percussive": proxy.percussive,
        }

        assert scores["drums"] < 1.0
        assert scores["vocals"] < 1.0
        assert scores["percussive"] < 1.0
        assert len({round(value, 4) for value in scores.values()}) >= 4
        assert proxy.dominant_proxy in {"drums", "bass", "percussive", "harmonic"}

    def test_specific_proxy_can_lead_when_drums_are_only_slightly_higher(self):
        phrase = Phrase(
            start_t=40.0,
            end_t=48.0,
            parent_section_index=5,
            phrase_type="steady",
            energy_delta=0.0,
            has_kick=True,
            band_ratios=_band_vector(kick=0.08, bass=0.16, low_mid=0.16, mid=0.20, presence=0.23),
            band_fluxes=_band_vector(kick=0.22, presence=0.08, mid=0.06),
            dominant_band="presence",
        )
        features = _make_features(
            40.0,
            48.0,
            bass_ratio=0.14,
            kick_flux=14.0,
            onset_strength=0.28,
            spectral_flux=24.0,
            pan_center=0.05,
            pan_width=0.22,
            band_ratios=phrase.band_ratios,
            band_fluxes=phrase.band_fluxes,
            chroma=_chroma_vector(4, value=0.9),
        )

        proxy = InstrumentHeuristicAnalyzer().analyze([phrase], features)[0]
        assert proxy.drums > proxy.vocals
        assert proxy.dominant_proxy == "vocals"
        assert proxy.secondary_proxy in {"drums", "harmonic"}
        assert proxy.active_proxies[0] == "vocals"
