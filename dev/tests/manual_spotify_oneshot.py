"""Manual validation: one-shot Spotify playback & queue fetch (headless).

Quick sanity check that the client can talk to the Spotify API.

Usage:
    1. Start playing music in Spotify on any device.
    2. Run: python dev/tests/manual_spotify_oneshot.py
"""

from dreamsync.spotify.auth import TokenStore, refresh_if_needed
from dreamsync.spotify.client import SpotifyClient


def main() -> None:
    store = TokenStore()
    if not refresh_if_needed(store):
        print("Token refresh failed — re-run: dreamsync spotify-auth --client-id <ID>")
        return

    client = SpotifyClient(store)

    state = client.get_playback_state()
    if state and state.track:
        print(f'Now playing: "{state.track.name}" by {state.track.artist}')
        print(f"  Progress: {state.progress_ms // 1000}s, Playing: {state.is_playing}")
    else:
        print("Nothing playing (start music in Spotify first)")

    queue = client.get_queue()
    print(f"Queue: {len(queue.queue)} upcoming tracks")
    for i, t in enumerate(queue.queue[:5]):
        print(f'  {i+1}. "{t.name}" by {t.artist}')


if __name__ == "__main__":
    main()
