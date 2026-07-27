"""Bounded anonymous section recurrence and transition-order memory."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, replace

from .models import PhraseFingerprint, SectionFingerprint
from .similarity import MultiFeatureSimilarityMemory
from .structure_models import LiveBarFingerprint, LiveSectionHypothesis


@dataclass(frozen=True)
class SectionPrediction:
    section_id: str
    probability: float
    source: str
    semantic_role: str | None = None
    semantic_probability: float = 0.0


class SectionOrderMemory:
    def __init__(self, *, max_sections: int = 24, max_identities: int = 12) -> None:
        self.max_sections = max(2, int(max_sections))
        self.max_identities = max(2, int(max_identities))
        self.reset()

    def reset(self) -> None:
        self._identities: dict[str, SectionFingerprint] = {}
        self._order: deque[str] = deque(maxlen=self.max_sections)
        self._transitions: dict[str, Counter[str]] = {}

    @property
    def identities(self) -> tuple[SectionFingerprint, ...]:
        return tuple(self._identities.values())

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(self._order)

    def observe_completed(
        self,
        *,
        phrases: tuple[PhraseFingerprint, ...],
        duration_bars: int,
        entrance_function: str | None,
        exit_function: str | None,
        energy_envelope: tuple[int, ...],
        onset_envelope: tuple[int, ...],
    ) -> SectionFingerprint:
        candidate = SectionFingerprint(
            section_id="",
            phrases=phrases,
            duration_bars=max(1, int(duration_bars)),
            entrance_function=entrance_function,
            exit_function=exit_function,
            energy_envelope=energy_envelope,
            onset_envelope=onset_envelope,
        )
        match = self.match(candidate)
        if match and match[1] >= 0.72:
            identity = self._identities[match[0]]
            semantic_role, semantic_probability = _semantic_role(
                identity.section_id,
                energy_envelope,
                identity.recurrence_count + 1,
            )
            identity = replace(
                identity,
                recurrence_count=identity.recurrence_count + 1,
                semantic_role=semantic_role,
                semantic_probability=semantic_probability,
            )
        else:
            identity = replace(
                candidate,
                section_id=self._new_identity(),
            )
        self._identities[identity.section_id] = identity
        if self._order:
            self._transitions.setdefault(self._order[-1], Counter())[
                identity.section_id
            ] += 1
        self._order.append(identity.section_id)
        return identity

    def match(
        self, candidate: SectionFingerprint
    ) -> tuple[str, float] | None:
        ranked = [
            (section_id, _section_similarity(candidate, fingerprint))
            for section_id, fingerprint in self._identities.items()
        ]
        return max(ranked, key=lambda item: item[1]) if ranked else None

    def match_prefix(
        self,
        numerals: tuple[str, ...],
    ) -> tuple[SectionPrediction, ...]:
        if not numerals:
            return ()
        matches: list[SectionPrediction] = []
        for identity in self._identities.values():
            flattened = tuple(
                numeral
                for phrase in identity.phrases
                for numeral in phrase.numerals
            )
            comparable = min(len(numerals), len(flattened))
            if comparable == 0:
                continue
            correct = sum(
                left == right
                for left, right in zip(numerals[-comparable:], flattened[:comparable])
            )
            score = (correct / comparable) * min(1.0, 0.5 + (0.12 * comparable))
            if score >= 0.45:
                matches.append(
                    SectionPrediction(
                        identity.section_id,
                        score,
                        "section_prefix",
                        identity.semantic_role,
                        identity.semantic_probability,
                    )
                )
        return tuple(sorted(matches, key=lambda item: item.probability, reverse=True))

    def predict_successor(self) -> tuple[SectionPrediction, ...]:
        if not self._order:
            return ()
        counts = self._transitions.get(self._order[-1])
        if not counts:
            return ()
        total = sum(counts.values())
        return tuple(
            SectionPrediction(
                section_id=section_id,
                probability=count / total,
                source="section_order",
                semantic_role=self._identities[section_id].semantic_role,
                semantic_probability=self._identities[
                    section_id
                ].semantic_probability,
            )
            for section_id, count in counts.most_common()
        )

    def _new_identity(self) -> str:
        count = len(self._identities)
        if count < self.max_identities:
            return _alpha_id(count)
        # Reuse the least recurrent identity when the identity bound is hit.
        return min(
            self._identities.values(),
            key=lambda item: item.recurrence_count,
        ).section_id


def _section_similarity(
    left: SectionFingerprint, right: SectionFingerprint
) -> float:
    left_numerals = tuple(n for phrase in left.phrases for n in phrase.numerals)
    right_numerals = tuple(n for phrase in right.phrases for n in phrase.numerals)
    comparable = min(len(left_numerals), len(right_numerals))
    harmonic = (
        sum(a == b for a, b in zip(left_numerals[:comparable], right_numerals[:comparable]))
        / comparable
        if comparable
        else 0.0
    )
    duration = 1.0 - min(
        1.0,
        abs(left.duration_bars - right.duration_bars)
        / max(left.duration_bars, right.duration_bars, 1),
    )
    entrance = float(
        left.entrance_function is not None
        and left.entrance_function == right.entrance_function
    )
    energy = _shape_similarity(left.energy_envelope, right.energy_envelope)
    return (0.62 * harmonic) + (0.16 * duration) + (0.10 * entrance) + (0.12 * energy)


def _shape_similarity(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    if not left or not right:
        return 0.5
    size = min(len(left), len(right))
    return 1.0 - (
        sum(min(4, abs(a - b)) / 4.0 for a, b in zip(left[:size], right[:size]))
        / size
    )


def _semantic_role(
    section_id: str,
    energy: tuple[int, ...],
    recurrence_count: int,
) -> tuple[str | None, float]:
    # Semantic names remain conservative; anonymous identity is primary.
    if recurrence_count < 2 or len(energy) < 2:
        return None, 0.0
    rise = energy[-1] - energy[0]
    if rise >= 1:
        return "chorus", min(0.78, 0.50 + (0.07 * recurrence_count))
    if section_id == "A":
        return "verse", min(0.72, 0.46 + (0.06 * recurrence_count))
    return None, 0.0


def _alpha_id(index: int) -> str:
    return chr(ord("A") + (index % 26))


@dataclass
class _StructureSectionIdentity:
    section_id: str
    prototype: tuple[LiveBarFingerprint, ...]
    durations: Counter[int]
    recurrence_count: int = 1
    semantic_role: str | None = None
    semantic_probability: float = 0.0


class StructureSectionMemory:
    """Bounded anonymous section identities and active transition memory."""

    def __init__(
        self,
        *,
        max_sections: int = 24,
        max_identities: int = 12,
        match_threshold: float = 0.70,
    ) -> None:
        self.max_sections = max(2, int(max_sections))
        self.max_identities = max(2, int(max_identities))
        self.match_threshold = float(match_threshold)
        self._similarity = MultiFeatureSimilarityMemory(max_bars=256)
        self.reset()

    def reset(self) -> None:
        self._identities: dict[str, _StructureSectionIdentity] = {}
        self._order: deque[str] = deque(maxlen=self.max_sections)
        self._transitions: dict[str, Counter[str]] = {}
        self._merges: deque[tuple[str, str]] = deque(maxlen=32)

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(self._order)

    @property
    def identities(self) -> tuple[str, ...]:
        return tuple(self._identities)

    @property
    def merges(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._merges)

    def observe_completed(
        self,
        bars: tuple[LiveBarFingerprint, ...],
    ) -> str:
        if not bars:
            raise ValueError("a completed section must contain bars")
        ranked = self._rank(bars)
        if ranked and ranked[0][1] >= self.match_threshold:
            section_id = ranked[0][0]
            identity = self._identities[section_id]
            identity.recurrence_count += 1
            identity.durations[len(bars)] += 1
        else:
            section_id = self._new_identity()
            self._identities[section_id] = _StructureSectionIdentity(
                section_id=section_id,
                prototype=bars,
                durations=Counter({len(bars): 1}),
            )
        if self._order:
            self._transitions.setdefault(self._order[-1], Counter())[section_id] += 1
        self._order.append(section_id)
        self._merge_equivalent_identities()
        return section_id

    def match_prefix(
        self,
        bars: tuple[LiveBarFingerprint, ...],
        *,
        start_bar: int,
    ) -> tuple[LiveSectionHypothesis, ...]:
        if not bars:
            return ()
        hypotheses: list[LiveSectionHypothesis] = []
        for section_id, identity in self._identities.items():
            comparable = min(len(bars), len(identity.prototype))
            result = self._similarity.sequence_similarity(
                bars[:comparable],
                identity.prototype[:comparable],
            )
            if result is None:
                continue
            prefix_growth = min(1.0, 0.45 + (0.12 * comparable))
            section_score = _structure_section_sequence_score(
                bars[:comparable],
                identity.prototype[:comparable],
                result.combined,
            )
            probability = section_score * result.reliability * prefix_growth
            if probability < 0.35:
                continue
            duration_distribution = _counter_distribution(identity.durations)
            expected_duration = (
                duration_distribution[0][0] if duration_distribution else None
            )
            hypotheses.append(
                LiveSectionHypothesis(
                    section_id=section_id,
                    start_bar=start_bar,
                    expected_end_bar=(
                        start_bar + expected_duration
                        if expected_duration is not None
                        else None
                    ),
                    duration_distribution=duration_distribution,
                    prefix_match=section_score,
                    recurrence_count=identity.recurrence_count,
                    likely_successors=tuple(
                        (item.section_id, item.probability)
                        for item in self.predict_successor(section_id)
                    ),
                    probability=max(0.0, min(1.0, probability)),
                    semantic_role=identity.semantic_role,
                    semantic_probability=identity.semantic_probability,
                )
            )
        return tuple(
            sorted(
                hypotheses,
                key=lambda item: (-item.probability, item.section_id),
            )
        )

    def predict_successor(
        self,
        section_id: str | None = None,
    ) -> tuple[SectionPrediction, ...]:
        source = section_id or (self._order[-1] if self._order else None)
        if source is None:
            return ()
        counts = self._transitions.get(source)
        if not counts:
            return ()
        total = sum(counts.values()) or 1
        return tuple(
            SectionPrediction(
                section_id=target,
                probability=count / total,
                source="structure_section_order",
            )
            for target, count in counts.most_common()
        )

    def _rank(
        self,
        bars: tuple[LiveBarFingerprint, ...],
    ) -> tuple[tuple[str, float], ...]:
        ranked: list[tuple[str, float]] = []
        for section_id, identity in self._identities.items():
            comparable = min(len(bars), len(identity.prototype))
            result = self._similarity.sequence_similarity(
                bars[:comparable],
                identity.prototype[:comparable],
            )
            if result is None:
                continue
            section_score = _structure_section_sequence_score(
                bars[:comparable],
                identity.prototype[:comparable],
                result.combined,
            )
            duration = 1.0 - min(
                1.0,
                abs(len(bars) - len(identity.prototype))
                / max(len(bars), len(identity.prototype)),
            )
            ranked.append((section_id, (0.88 * section_score) + (0.12 * duration)))
        return tuple(sorted(ranked, key=lambda item: (-item[1], item[0])))

    def _new_identity(self) -> str:
        if len(self._identities) < self.max_identities:
            return _alpha_id(len(self._identities))
        replaceable = min(
            self._identities.values(),
            key=lambda item: (item.recurrence_count, item.section_id),
        )
        return replaceable.section_id

    def _merge_equivalent_identities(self) -> None:
        ids = tuple(self._identities)
        for left_index, left_id in enumerate(ids):
            for right_id in ids[left_index + 1 :]:
                if left_id not in self._identities or right_id not in self._identities:
                    continue
                left = self._identities[left_id]
                right = self._identities[right_id]
                comparable = min(len(left.prototype), len(right.prototype))
                result = self._similarity.sequence_similarity(
                    left.prototype[:comparable],
                    right.prototype[:comparable],
                )
                if result is None or result.combined < 0.92:
                    continue
                keep_id, drop_id = sorted((left_id, right_id))
                keep = self._identities[keep_id]
                drop = self._identities.pop(drop_id)
                keep.recurrence_count += drop.recurrence_count
                keep.durations.update(drop.durations)
                self._order = deque(
                    (keep_id if value == drop_id else value for value in self._order),
                    maxlen=self.max_sections,
                )
                for counts in self._transitions.values():
                    if drop_id in counts:
                        counts[keep_id] += counts.pop(drop_id)
                if drop_id in self._transitions:
                    self._transitions.setdefault(keep_id, Counter()).update(
                        self._transitions.pop(drop_id)
                    )
                self._merges.append((drop_id, keep_id))


def _counter_distribution(
    counter: Counter[int],
) -> tuple[tuple[int, float], ...]:
    total = sum(counter.values()) or 1
    return tuple(
        (duration, count / total)
        for duration, count in sorted(
            counter.items(),
            key=lambda item: (-item[1], item[0]),
        )
    )


def _structure_section_sequence_score(
    left: tuple[LiveBarFingerprint, ...],
    right: tuple[LiveBarFingerprint, ...],
    combined: float,
) -> float:
    from .similarity import compare_bars

    timbre = sum(
        compare_bars(a, b).timbre for a, b in zip(left, right)
    ) / max(1, min(len(left), len(right)))
    return (0.55 * timbre) + (0.45 * combined)
