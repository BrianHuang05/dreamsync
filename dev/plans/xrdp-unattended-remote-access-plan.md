# xrdp plan for unattended Dreamsync access

## Objective

Replace Ubuntu 22.04.5's built-in GNOME **Desktop Sharing** RDP path with
`xrdp`, so the Surface Pro 6 can offer an RDP log-in session after reboot
without a physical `brian` GNOME session already existing.

This is an operational/infrastructure change only. It must not modify
Dreamsync source code or its audio-routing configuration.

## Current known state

- Server: Ubuntu 22.04.5 LTS on a Surface Pro 6, account `brian`.
- Client: Windows laptop.
- OpenSSH is working, key-only, and is reachable over Tailscale.
- Tailscale access policy permits only the Windows Tailscale IP
  `100.117.194.89` to reach the Ubuntu Tailscale IP `100.91.139.19` on TCP
  ports 22 and 3389.
- UFW has laptop-only LAN rules for SSH/RDP and allows traffic on
  `tailscale0`; the Tailscale policy supplies the least-privilege restriction
  on that interface.
- No router port-forwarding is permitted.
- Suspend/hibernate targets are masked. The Type Cover emits a lid-close
  event, but masking now prevents it from suspending the system.
- Native GNOME Desktop Sharing is configured per-user. It cannot serve RDP
  after reboot because no graphical `brian` session exists; `loginctl` showed
  only GDM plus an SSH session. Ubuntu 22.04's Settings > Sharing > Remote
  Login is SSH-related on this release, not the newer system-level GDM RDP
  feature.

## Important design decision

`xrdp` must be the **only** service listening on TCP 3389. Disable native
GNOME Desktop Sharing before starting xrdp. Do not enable legacy VNC.

`xrdp` creates a separate Xorg desktop session. This makes it appropriate for
unattended GUI administration, but its PipeWire/PulseAudio session may differ
from the physical GNOME session. Treat it as a control/debug desktop; validate
Dreamsync's real audio capture devices and routing explicitly rather than
assuming they are identical.

## Implementation steps

Perform all commands from an existing SSH session. Keep that session open
until both xrdp and a second SSH session have been verified.

### 1. Capture pre-change state

```bash
sudo ss -ltnp | grep -E ':(3389|3390)\\b' || true
systemctl --user --no-pager status gnome-remote-desktop.service || true
gsettings get org.gnome.desktop.remote-desktop.rdp enable || true
sudo ufw status numbered
sudo systemctl is-enabled sleep.target suspend.target hibernate.target hybrid-sleep.target
tailscale status
```

Record whether anything owns 3389. Do not proceed if another service is
listening until its role has been identified.

### 2. Disable native GNOME Desktop Sharing

From the `brian` SSH session:

```bash
gsettings set org.gnome.desktop.remote-desktop.rdp enable false
systemctl --user disable --now gnome-remote-desktop.service
```

Verify that 3389 and 3390 are no longer in use:

```bash
sudo ss -ltnp | grep -E ':(3389|3390)\\b' || true
```

If Settings later shows Desktop Sharing as enabled, turn it off there too;
never run GNOME Desktop Sharing and xrdp on the same RDP port.

### 3. Install and enable xrdp

```bash
sudo apt update
sudo apt install xrdp xorgxrdp
sudo adduser xrdp ssl-cert
sudo systemctl enable --now xrdp
```

The package may already place `xrdp` in `ssl-cert`; the group command is
idempotent. Do not install VNC, expose a public port, or change the SSH
configuration.

### 4. Configure the Xorg desktop session only if needed

First test the default xrdp session. If Windows authenticates but presents a
blank/failed desktop, inspect `/etc/xrdp/startwm.sh` and journal logs before
editing anything:

```bash
sudo journalctl -u xrdp -u xrdp-sesman -b --no-pager
sudo sed -n '1,220p' /etc/xrdp/startwm.sh
```

If a per-user session command is required, use `brian`'s `~/.xsession` to
start the Ubuntu GNOME Xorg session, retaining a backup of any existing file:

```bash
cp ~/.xsession ~/.xsession.before-xrdp 2>/dev/null || true
printf '%s\\n' 'exec gnome-session --session=ubuntu' > ~/.xsession
chmod 600 ~/.xsession
```

Do not globally disable Wayland merely to make xrdp work unless logs prove it
is necessary; xrdp's own session should use Xorg independently.

### 5. Verify the listener and host controls

```bash
sudo systemctl --no-pager status xrdp xrdp-sesman
sudo ss -ltnp | grep ':3389'
sudo ufw status numbered
```

Expected result: xrdp listens on TCP 3389. No new UFW or Tailscale policy rule
is needed because both already allow only the intended laptop path. Confirm
that the access policy has not been broadened beyond:

```text
100.117.194.89 -> 100.91.139.19: tcp/22, tcp/3389
```

### 6. Test from Windows

Use Remote Desktop Connection (`mstsc`) to:

```text
100.91.139.19:3389
```

At the xrdp login screen, select an Xorg session if offered and authenticate
with the normal Ubuntu `brian` username and password. It does not use the SSH
key, Tailscale identity, or the prior GNOME Desktop Sharing password.

Run these tests in order:

1. Connect while the Surface is awake and a local session exists.
2. Disconnect, reboot the Surface, wait for Tailscale to return, then connect
   before any local user login.
3. Test from a non-home network over Tailscale.
4. Close the Type Cover, wait at least 10 minutes, and repeat SSH/RDP testing.
5. In the xrdp session, enumerate audio devices and verify Dreamsync's capture
   source deliberately; do not assume its audio context matches the physical
   session.

Useful diagnostics:

```powershell
Test-NetConnection 100.91.139.19 -Port 3389
```

```bash
sudo journalctl -u xrdp -u xrdp-sesman -b --no-pager
loginctl list-sessions
wpctl status
pactl list short sources
```

## Security acceptance criteria

- SSH remains key-only and is still accessible only to `brian`.
- xrdp is reachable only through the laptop's Tailscale identity/IP and the
  existing local-laptop UFW rule; no router port-forward is created.
- The xrdp service uses the normal Ubuntu account password over encrypted RDP
  tunneled inside Tailscale; use a strong unique Ubuntu password.
- Native GNOME Desktop Sharing and legacy VNC remain disabled, preventing RDP
  port conflicts and unnecessary listeners.
- The Surface stays awake with its Type Cover closed because sleep targets
  remain masked.

## Rollback

If xrdp fails or creates unacceptable audio/session behavior:

```bash
sudo systemctl disable --now xrdp xrdp-sesman
sudo apt remove xrdp xorgxrdp
rm -f ~/.xsession
```

Only restore native GNOME Desktop Sharing if an active local `brian` GUI
session is available and its port requirements are understood:

```bash
gsettings set org.gnome.desktop.remote-desktop.rdp enable true
systemctl --user enable --now gnome-remote-desktop.service
```

Do not alter Tailscale, UFW, SSH keys, or the systemd sleep-target masks as
part of rollback.
