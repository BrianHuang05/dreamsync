# Color-Wave Ripple — Open Issues & Next Steps

Pickup notes for resuming the slow color-wave ripple work.

## The Core Problem

There are three layers of mismatch between what dreamsync *thinks* it's doing, what it *sends* to LedFx, and what the lights *actually do*.

---

## 1. LedFx May Not Accept Our Config Keys

We send `color`, `mirror`, `brightness`, `speed`, and `bpm_hint` as config keys inside the POST payload. But LedFx effect types each have their own schema — there's no guarantee that `power`, `wavelength`, or `bar` accept all of these keys.

**What to investigate:**
- Hit `GET /api/effects` or `GET /api/schema` on the running LedFx instance to dump the actual config schema for each effect type.
- Check which keys `power` actually accepts. It likely has `mirror` and `speed`, but `color` may be named differently (e.g., `color_lows`, `color_mids`, `color_high`, `gradient`, `color_step`).
- `bpm_hint` is probably silently ignored by every effect — confirm and consider dropping it or mapping it to an actual parameter.

**Likely fix:** Map `intent.color` to the correct LedFx-specific key(s) per effect type, not a generic `"color"`.

## 2. POST Semantics: Replace vs Update

We POST to `/api/virtuals/{id}/effects` on every beat. The LedFx docs say this endpoint sets the active effect, but unclear behavior:
- Does POST with a new `type` fully replace the old effect? Or does the old one linger?
- Does POST with the *same* `type` but changed `config` merge or replace the config?
- If the effect type doesn't change beat-to-beat (it shouldn't in our case), maybe we should PUT/PATCH config updates instead of re-POSTing the whole effect.

**What to investigate:**
- Use `--debug-ledfx` and watch the actual HTTP responses. Look for 200 vs 409 vs unexpected behavior.
- Try manually: POST once with `power`, then POST again with just a changed `color` field. Does it merge? Does it restart the animation?
- Check if there's a `PUT /api/virtuals/{id}/effects` endpoint that updates config without replacing the effect.

**Why this matters:** If every POST restarts the animation, the wave will stutter/reset on every beat instead of smoothly continuing with a new color.

## 3. Mirror Center vs Physical Center

We hardcode `"mirror": true`, which tells LedFx to mirror the animation from the midpoint of the virtual's pixel range. But the "center" LedFx uses is the midpoint of the **segment index range**, not necessarily the physical center of the light strip.

**What to investigate:**
- Check how the virtual's segments are ordered in LedFx. If the strip is mapped start-to-end as segments `[0, N]`, mirror will split at pixel N/2. If the physical center is offset, the ripple will look wrong.
- Some LedFx effects have a `center` or `offset` config key — check if `power` does.

**Likely fix:** Either reorder segments in LedFx so the midpoint matches the physical center, or find a config key for center offset if the effect supports one.

## 4. Accent Role Drops Color

`transform_intent` in `roles.py` creates a new `LightingIntent` for accent devices but doesn't pass through `color`:

```python
return LightingIntent(
    mode=EffectMode.AMBIENT,
    intensity=intent.intensity * 0.6,
    speed=min(intent.speed, 0.25),
    bpm=intent.bpm,
    # color is missing — defaults to None
)
```

**Fix:** Pass `color=intent.color` through in the accent transform. Decide if accent devices should get the same color cycle or stay on a fixed color.

## 5. Effect Restart on Color Change

The current design sends `None` between beats (no update), then posts a full new intent on each beat. If LedFx treats each POST as "set up this effect from scratch," the wave will restart its animation on every beat rather than smoothly rolling with a new color.

**Possible approaches:**
- If LedFx has a way to update just the color of a running effect without restarting it, use that.
- If not, consider whether the `power` effect even supports a concept of "current color" being changed live. Some effects derive colors from a gradient and don't have a single `color` parameter.
- Alternatively, pick an effect type that natively supports color cycling on a beat input.

## 6. Speed Sanity Check

We compute `speed = bpm / 480.0`, clamped to `[0.08, 0.35]`. At 120 BPM that's `0.25`. But we don't know what LedFx interprets `speed: 0.25` to mean for the `power` effect — it might still be too fast or too slow for a visible wave motion.

**What to investigate:**
- Manually set `speed` to a few values (0.1, 0.25, 0.5) on a running `power` effect and observe.
- Confirm the `speed` key is even the right one — some effects use `decay` or `frequency` for wave motion.

---

## Quick Verification Checklist

1. `GET http://127.0.0.1:8888/api/schema` — dump effect schemas, find accepted keys for `power`/`wavelength`
2. `--debug-ledfx` run for 30s — watch payloads and confirm they're being accepted
3. Manual curl: POST a `power` effect, then POST again with only `config.color` changed — does the wave continue or restart?
4. Check virtual segment ordering in LedFx UI matches physical strip layout
5. Try `PUT` instead of `POST` to see if LedFx supports config-only updates

## Files to Touch

| File | What |
|------|------|
| `src/dreamsync/output/ledfx.py` | Fix config key mapping per effect type; possibly switch to PUT for updates |
| `src/dreamsync/output/roles.py` | Pass `color` through in accent transform |
| `src/dreamsync/basic_controller.py` | May need to always emit (not just on beat) if LedFx needs continuous pokes |
| `src/dreamsync/cli.py` | Possibly add `--center-offset` or similar if needed |
