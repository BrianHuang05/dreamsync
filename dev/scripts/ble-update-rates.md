# BLE update-rate test

## One-command config sweep

```bash
.venv/bin/python dev/scripts/test_ble_update_rates.py \
  --config dev/devices.yaml --rates 5 --seconds 30 --output ble-baseline.json
```

This tests every enabled BLE entry individually, then all together, preserving
each entry's bulb/segment protocol and segment count. LAN and disabled entries
are explicitly skipped. A failed individual device is recorded and the sweep
continues with the next device. A failed group skips its remaining higher rates.
The together stage requires all selected devices to connect; a setup failure is
recorded for that group. Results are saved after each stage. No YAML is changed.
Use `--mode individual` or `--mode together` to run only one phase.
After the 5 Hz baseline, use `--rates 5 10 15 20` for a rate sweep.

## Explicit addresses

Run on the Linux lighting host with this updated checkout and its existing
Python environment (including the `bleak` dependency). Stop DreamSync lighting
output and close Govee Home so the test can own the BLE connections.

From the repository root, substitute a BLE address from your device configuration:

```bash
.venv/bin/python dev/scripts/test_ble_update_rates.py \
  --address AA:BB:CC:DD:EE:FF --protocol segment --segments 15 \
  --rates 5 10 15 20 --seconds 30 --output ble-rate-results.json
```

For bulbs use `--protocol bulb`. For strips supply their actual segment count.
Repeat `--address` to test several devices of the same protocol concurrently.
For the H6006 fleet, use `--protocol bulb`. Start with one bulb at `--rates 5`,
then repeat with additional bulb addresses at the same rate before trying 10
or 20 Hz. Keep Govee DreamView/Sync Center inactive during these comparisons.
Discovery is shared within the process and connection setup is serialized;
startup can take longer as the number of devices increases. This coordination
does not extend to a separate DreamSync process or another Bluetooth app.
The test turns the lights on, sets brightness to 30%, and continuously cycles
colors. It may leave the lights showing the final test color; restart DreamSync
or use the app afterward. Ctrl+C stops the sweep and saves completed stages.
The script does not modify device configuration or the default update rate.

Compare the 5 Hz baseline with each higher rate:

- Color-write frequency should increase; it can be below the requested cap
  because the adapter waits between completed frames and each write takes time.
- Watch for increasing maximum/p95 gaps, write errors, disconnections, freezes,
  and visible jumps. The script stops after a stage with detected transport
  failure. Connection sampling can miss very brief disconnections.
- These are **host-side write-without-response measurements**, not device
  acknowledgments or visible latency. A clean result does not prove every update
  appeared on the light. Observe or record the light during each stage.
- This is a solid-color workload (one color packet per frame), so it is an initial
  screening test. Multicolor segment effects and changing hardware brightness
  use more packets. Retest the chosen rate with real DreamSync effects and all
  devices active before setting each device's `max_fps` in your configuration.

Choose the highest rate that remains visibly smooth and stable, with some margin
below any rate that fails. Start with 10 Hz rather than assuming 20 Hz is safe.
