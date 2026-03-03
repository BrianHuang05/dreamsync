"""Manual validation: Spotify queue watcher (headless, no lights needed).

Usage:
    1. Start playing music in Spotify on any device.
    2. Run: python dev/tests/manual_spotify_watcher.py
    3. Skip songs and watch stdout for TRACK CHANGED / QUEUE UPDATED events.
    4. Ctrl+C to stop.
"""

import time

from dreamsync.spotify.auth import TokenStore, refresh_if_needed
from dreamsync.spotify.client import SpotifyClient
from dreamsync.spotify.queue_watcher import SpotifyQueueWatcher


def main() -> None:
    store = TokenStore()
    if not refresh_if_needed(store):
        print("Token refresh failed — re-run: dreamsync spotify-auth --client-id <ID>")
        return

    client = SpotifyClient(store)

    watcher = SpotifyQueueWatcher(
        client,
        poll_interval=2.0,
        on_track_changed=lambda new, old: print(
            f'TRACK CHANGED: "{new.name}" by {new.artist}'
        ),
        on_queue_updated=lambda q: print(
            f"QUEUE UPDATED: {len(q.queue)} upcoming tracks"
        ),
    )
    watcher.start()
    print("Watcher running — skip a song in Spotify to test. Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
        print("\nDone.")


if __name__ == "__main__":
    main()
