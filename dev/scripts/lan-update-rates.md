# LAN changing-frame rate test

From the repo root on the Linux host, stop DreamSync output and deactivate
Govee DreamView/other lighting controllers, then run:

```bash
.venv/bin/python -u dev/scripts/test_lan_update_rates.py \
  --config dev/devices.yaml --rates 5 10 20 30 --seconds 30 \
  --mode both --pattern walk --session-mode continuous \
  --activation settled --output lan-rate-results.json \
  2>&1 | tee lan-rate-test.log
```

This tests each enabled LAN device individually, then all together, using its
configured segment count and transport. BLE entries and disabled devices are
skipped. It does not discover or add other-room lights. Rates override the config
cap for this test only. Three devices at four rates take about eight minutes
plus setup. Use `--mode together` for a roughly two-minute fleet-only run.

Continuous mode (the default) powers/configures each group once and keeps the
same socket and streaming session across all rates. No disable, enable, power,
or brightness commands are sent between rates. Stage reporting creates a short
gap between rate windows; there is no deliberate inter-rate sleep. The marker
index restarts at zero for each rate. The stream is released only after the
group's entire sweep. No periodic enables are injected into the measured stream.

`--activation settled` (default) finishes power-on and brightness setup for the
whole group, then sends three enable commands per Razer device 200 ms apart.
`--activation legacy` reproduces the old power-on/immediate-enable/brightness
ordering. The settled sequence is an experiment, not a confirmed firmware fix.
Both modes wait 500 ms after release before another session starts.

For two fleet sweeps with reversed order on the second trial:

```bash
.venv/bin/python -u dev/scripts/test_lan_update_rates.py \
  --config dev/devices.yaml --mode together --rates 5 10 20 30 \
  --seconds 30 --trials 2 --rate-order alternate \
  --session-mode continuous --activation settled \
  --output lan-continuous.json 2>&1 | tee lan-continuous.log
```

For a startup comparison at a fixed 5 Hz, run both sequences separately:

```bash
for activation in legacy settled; do
  .venv/bin/python -u dev/scripts/test_lan_update_rates.py \
    --config dev/devices.yaml --mode together --rates 5 --seconds 15 \
    --trials 6 --session-mode restart --activation "$activation" \
    --output "lan-startup-$activation.json" \
    2>&1 | tee "lan-startup-$activation.log"
done
```

Record visual pass/fail separately for each strip, trial, and rate. A successful
host send cannot mark visual success automatically. JSON rows carry trial and
session IDs; `sessions` records rate order and control-packet attempts, with
timestamps relative to session setup start, JSON payloads, and send errors.
Repeat the activation comparison in reverse order if the result differs, since
elapsed time and prior state can still influence separate trials.

Each frame advances the lit segment and changes its hue. All other configured
segments are black. At 5 Hz the marker advances every 200 ms; at 30 Hz every
33.3 ms. This differs from the old `govee-test --pattern walk`, whose steps are
fixed at 300 ms regardless of `--fps`. Use `--pattern solid` for changing whole
strip colors instead, including colorwc devices that cannot address segments.

The benchmark uses DreamSync's packet encoders and activation commands, with a
persistent UDP socket and an absolute-deadline scheduler instead of the normal
adapter rate limiter. Missed slots are skipped, not replayed in bursts. Fleet
frames use the same index but are sent sequentially; timestamps include that
per-device scheduling offset. Razer uses the production interpolation setting.

The JSON reports successful host sends per second, local send-call duration,
frame gaps, deadline lateness, missed scheduling slots, bytes, and send errors.
These do **not** measure radio delivery, device processing, or visible latency.
A disconnected light can still produce a clean UDP-send report. Visually watch
for stalls, dropped steps, or trails; capture video for actual optical timing.
No status probes are interleaved, so telemetry adds no device traffic.

Lights are powered on and hardware brightness is set to 30%. Razer streaming
mode is released after each session, including interruption; lights are not
explicitly powered off, and their previous state is not restored. It is normal
for devices to return to an earlier mode when streaming is released.

Completed stages are saved immediately and retained on Ctrl+C. Setup/release
events are saved after session exit, including interruption. An interrupted
stage is not included. Setup/send failure skips higher rates for that group and
continues the next group. Config files are never written. A zero exit status
only means no host send/setup errors were detected.
