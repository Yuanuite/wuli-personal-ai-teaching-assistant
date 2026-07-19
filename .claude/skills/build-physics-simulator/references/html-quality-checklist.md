# Offline HTML quality checklist

## Interaction

- Play/pause must not reset progress unexpectedly.
- Replay must return to the true initial event.
- Duration changes playback rate without changing the physical path.
- Scrubbing pauses playback and redraws immediately.
- Parameter changes rebuild the model and reset progress.
- Zoom supports buttons and wheel/pinch input.
- Pan supports pointer dragging and does not fight pinch gestures.
- Fit-to-window includes every trajectory for the selected parameter.
- Canvas sizing accounts for `devicePixelRatio`.

## Visual physics

- Field symbols are visible across their full regions: `⊙` for out of the page and `⊗` for into the page.
- Electric arrows point in the declared field direction.
- The particle visibly carries `+` or `−`.
- Velocity is tangent to the path.
- Magnetic force points to the circle center.
- Electric force points along $q\vec E$.
- Entry counters refer to actual II→III transitions rather than animation segments.
- The final frame matches the exact deadline in the prompt.

## Portability

- UTF-8 `<meta charset>` and viewport metadata are present.
- All CSS and JavaScript are inline.
- No external URLs, network calls, iframes, or host-only APIs are required.
- The page opens by double-clicking the HTML file.
- HTML and ZIP filenames are ASCII; the visible document title may be Chinese.
- ZIP extraction succeeds on macOS, Windows, and common mobile archive apps.

## Runtime validation

1. Run `validate_simulator.py`.
2. Open the standalone file through `file://` in a real browser.
3. Capture `pageerror` and console errors.
4. Exercise each control at least once.
5. Test every exact solution preset at the final and claimed crossing instants.
6. Test one nearby non-solution value.
7. Inspect a desktop screenshot and a narrow mobile screenshot.

Treat static syntax checks as necessary but insufficient. Treat a clean browser run as necessary but insufficient for the physics.
