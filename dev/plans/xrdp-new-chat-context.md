# Prompt for a new implementation chat

Implement the plan in `dev/plans/xrdp-unattended-remote-access-plan.md`.

Context: I have a Surface Pro 6 running Ubuntu 22.04.5 LTS and need reliable,
unattended GUI access for Dreamsync development. SSH is already key-only and
works over Tailscale. My Windows laptop is `100.117.194.89` on the tailnet;
the Ubuntu device is `100.91.139.19`. Tailscale access control allows only
that laptop to reach Ubuntu TCP 22 and 3389. UFW permits laptop-only LAN
SSH/RDP and traffic on `tailscale0`; do not add public exposure or router
port-forwarding.

Ubuntu 22.04's native GNOME Desktop Sharing only works after a physical
`brian` GUI login. It does not provide the newer system-level/headless GNOME
Remote Login feature. The Surface's sleep/hibernate targets are masked because
the Type Cover emits a lid-close event. Do not undo that protection.

Install and configure xrdp over the existing SSH connection. Disable native
GNOME Desktop Sharing first so xrdp is the sole listener on TCP 3389. Preserve
SSH, Tailscale policy, UFW, and Dreamsync source/audio configuration. Validate
RDP after reboot and over Tailscale, then explicitly inspect audio devices in
the xrdp session because it will be a separate Xorg/PipeWire/PulseAudio
context. Do not claim the remote audio path matches the physical session
without testing it.

Use the plan's preflight checks, verification steps, security criteria, and
rollback procedure. Report command output and stop for direction if a port
conflict, credential choice, or display-session configuration requires a
material decision.
