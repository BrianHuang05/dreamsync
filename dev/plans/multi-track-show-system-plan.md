# Multi-track Show system plan

## Terminology

- **Track**: one audio file and its compiled lighting timeline.  This is what
  the application previously called a saved show.
- **Show**: a named, ordered collection of Tracks.  A saved Show is the
  playlist that can be compiled, re-opened, and played end to end.

## Delivery plan

1. Add immutable `ShowTrack` and `Show` models.  A Show embeds its Tracks'
   compiled `ShowTimeline` objects so playback never needs to regenerate a
   timeline.  Preserve the existing single-track timeline format as a legacy
   import.
2. Extend the Show service with load/save and full-Show compilation helpers;
   save manifests in `out/shows` by default.
3. Add a precompiled multi-track session path.  It must use every persisted
   timeline in order, carry pause/stop/status support, and route audio to the
   selected output device.
4. Restructure the Shows tab into a Show action bar, an explicit Track list,
   and a vertically split Track editor.  Keep cue editing for the selected
   Track, but make it scrollable so the waveform cannot obscure the metadata
   and cue table.
5. Make compile state visible per Track: `Compile Track` is unavailable after
   a Track is compiled, while `Compile All Tracks` handles the outstanding
   tracks.  Include clearly visible Add, Remove, and Move Track actions.
6. Cover serialization, compilation state, sequential playback, load-folder
   defaults, and UI action availability with focused tests.
