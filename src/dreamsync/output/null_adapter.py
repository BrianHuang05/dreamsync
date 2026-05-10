"""No-op device adapter for --dry-run / audio-only testing."""


class NullMultiAdapter:
    """Drop-in replacement for MultiGoveeLanAdapter that does nothing.

    Satisfies the same duck-typed interface (activate, deactivate,
    send_frame, devices) so it can be used anywhere a multi-adapter
    is expected.
    """

    def __init__(self) -> None:
        self.devices: list = []
        self._ble_followers: list = []
        self._frames_sent: int = 0

    def activate(self, brightness: int = 100) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def send_frame(self, t, intent, beat=False, params=None) -> bool:
        self._frames_sent += 1
        return True

    def send_spatial_scene(self, t, scene, *, beat=False, base_intent=None) -> bool:
        self._frames_sent += 1
        return True
