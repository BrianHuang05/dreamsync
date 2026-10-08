"""Local socket overhead only: no lights, delivery or optical latency measured."""
import json
from pathlib import Path
import socket
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from dreamsync.output.govee_lan import _default_udp_transport, _PersistentUdpTransport


def main():
    samples = {"per_packet_socket": [], "persistent_socket": []}
    payload = b"x" * 300
    count = 1000
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        port = receiver.getsockname()[1]
        for trial in range(6):
            names = list(samples)
            if trial % 2:
                names.reverse()
            for name in names:
                persistent = _PersistentUdpTransport()
                sender = persistent if name == "persistent_socket" else _default_udp_transport
                try:
                    began = time.perf_counter()
                    for _ in range(count):
                        sender(payload, "127.0.0.1", port)
                    samples[name].append((time.perf_counter() - began) * 1e6 / count)
                finally:
                    persistent.close()
    print(json.dumps({
        "measurement": "loopback host send overhead; receive buffer may drop packets",
        "packets_per_trial": count,
        "microseconds_per_send": samples,
        "median_microseconds_per_send": {key: statistics.median(value) for key, value in samples.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
