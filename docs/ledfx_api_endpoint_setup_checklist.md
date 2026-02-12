# LedFx API Endpoint Setup Checklist (D3.1)

## Execution Status (2026-02-11)
- [x] Opened and executed this checklist.
- [x] Full endpoint validation completed end-to-end.

### Run Log
- LedFx bootstrapped successfully via `start_ledfx.ps1` and reached `http://127.0.0.1:8888`.
- `GET /api/virtuals` succeeded.
  - Created virtual id: `dreamsync-check`
- Initial Step 4 attempt failed due no device segments on virtual:
  - `Unable to set effect ... Cannot activate, no configured device segments`
- Added dummy device + mapped segments to virtual:
  - Device id: `dreamsync-dummy`
  - Virtual segments: `[["dreamsync-dummy",0,29,false]]`
- Step 4 succeeded:
  - `POST /api/virtuals/dreamsync-check/effects` returned `"status": "success"`.
- Step 5 succeeded:
  - `python -m dreamsync ledfx-test --base-url http://127.0.0.1:8888 --virtual-id dreamsync-check --mode pulse`
  - Output: `{"sent":true,"mode":"pulse"}`

### Remaining Manual Actions
- None.

## 1. Start LedFx locally
- Launch LedFx.
- Confirm the UI is reachable at `http://127.0.0.1:8888`.

## 2. Ensure at least one virtual exists
- In LedFx UI, create/configure a virtual if needed.
- Verify API visibility:

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8888/api/virtuals"
```

## 3. Get your `virtual_id`
- From the `/api/virtuals` response, copy the target virtual id/key.

## 4. Validate the effect endpoint directly
- Send a basic effect change to `/api/virtuals/{virtual_id}/effects`:

```powershell
$vid = "<YOUR_VIRTUAL_ID>"
$body = @{
  type = "energy"
  config = @{
    brightness = 0.4
    speed = 0.6
    bpm_hint = 120
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8888/api/virtuals/$vid/effects" -ContentType "application/json" -Body $body
```

## 5. Validate with DreamSync D3.1 smoke command

```powershell
python -m dreamsync ledfx-test --base-url http://127.0.0.1:8888 --virtual-id <YOUR_VIRTUAL_ID> --mode pulse
```

- Expected output:

```json
{"sent":true,"mode":"pulse"}
```

## 6. Troubleshooting
- `connection refused`: LedFx is not running, or host/port is wrong.
- `404` / invalid virtual: `virtual_id` is wrong.
- No visible change: virtual is inactive or misconfigured in LedFx UI.

## References
- LedFx API docs: `https://docs.ledfx.app/en/latest/apis/api.html`
- LedFx virtuals docs: `https://docs.ledfx.app/en/latest/api.html`
