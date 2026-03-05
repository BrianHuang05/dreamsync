# Phase 4: Dynamic Rolling Window

## Goal

Extend the static splitting system to handle real-time updates to song timing data. Maintain a mutable boundary queue that can be updated as new timing information arrives, while enforcing safety margins to prevent modification of imminent boundaries.

## Prerequisites

- Phase 3 complete (static segment splitting verified)
- External timing data source identified and accessible (e.g., Spotify API, show session)

## Deliverables

| ID  | Deliverable                  | Plan                                                      |
|-----|------------------------------|-----------------------------------------------------------|
| 4.1 | Boundary Queue Manager       | [p4.1-boundary-queue-manager.md](phase4/p4.1-boundary-queue-manager.md) |
| 4.2 | Safety Margin Enforcement    | [p4.2-safety-margin-enforcement.md](phase4/p4.2-safety-margin-enforcement.md) |
| 4.3 | External Timing Integration  | [p4.3-external-timing-integration.md](phase4/p4.3-external-timing-integration.md) |

## Deliverable Dependencies

```
4.1 Boundary Queue Manager
  |
  v
4.2 Safety Margin Enforcement
  |
  v
4.3 External Timing Integration
```

## Tests

- [ ] After 4.1: Queue supports add/update/remove of boundaries; maintains sorted order by frame position
- [ ] After 4.2: Boundaries within safety margin (24,000 frames at 0.5s) are rejected for modification
- [ ] After 4.2: Boundaries beyond safety margin are updated correctly
- [ ] After 4.3: Simulated timing refresh updates future boundaries without affecting current segment
- [ ] After 4.3: Simulated track-change event triggers immediate boundary reassessment
- [ ] Integration: Dynamic plan with 3 mid-stream updates produces correct splits

## Completion Criteria

1. Boundary queue is a mutable, sorted data structure holding upcoming split points
2. Insert/update/remove operations on the queue are thread-safe (if multithreaded)
3. Safety margin prevents modification of any boundary within N frames of the current position (default: 24,000 frames = 0.5s)
4. External timing interface accepts `(currentPlaybackTime, songDurations[])` and recomputes future boundaries
5. Track-change events trigger an immediate plan refresh
6. Plan refresh interval is configurable (default: 5 seconds)
