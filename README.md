# Blackmagic Camera for Home Assistant

[![Release](https://img.shields.io/github/v/release/Videobarista/blackmagic-camera-ha?sort=semver)](https://github.com/Videobarista/blackmagic-camera-ha/releases)
[![Ruff](https://github.com/Videobarista/blackmagic-camera-ha/actions/workflows/ruff.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-camera-ha/actions/workflows/ruff.yml)
[![Hassfest](https://github.com/Videobarista/blackmagic-camera-ha/actions/workflows/hassfest.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-camera-ha/actions/workflows/hassfest.yml)
[![HACS](https://github.com/Videobarista/blackmagic-camera-ha/actions/workflows/hacs.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-camera-ha/actions/workflows/hacs.yml)
[![Tests](https://github.com/Videobarista/blackmagic-camera-ha/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-camera-ha/actions/workflows/tests.yml)
[![CodeQL](https://github.com/Videobarista/blackmagic-camera-ha/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/Videobarista/blackmagic-camera-ha/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Quality scale: Bronze](https://img.shields.io/badge/quality%20scale-aligned%20with%20Bronze-cd7f32.svg)](#quality-scale)
[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Videobarista&repository=blackmagic-camera-ha&category=integration)

Control Blackmagic Design cinema, broadcast and studio cameras from Home
Assistant through the camera's built-in **Camera Control REST API**: start and
stop recording, set ISO, shutter, white balance and iris, follow battery and
media status, switch monitoring overlays, audio inputs, tally and more.

> [!IMPORTANT]
> This is an unofficial, community made integration. It is **not affiliated
> with, endorsed by or supported by Blackmagic Design Pty. Ltd.** See the
> [disclaimer](#disclaimer).

## Features

- **Local push**: state changes arrive instantly over the camera's
  notification websocket; polling covers the rest (and everything when the
  websocket is unavailable).
- **Auto detection**: every camera model supports a different part of the
  API. Each property is probed once and only gets an entity when the camera
  supports it, so a PYXIS gets no ND filter and a URSA Broadcast G2 does.
- **Works with a camera that is switched off**: offline is treated as a
  state, not an error. Home Assistant starts normally, the entities are
  unavailable and the *Connection* sensor shows the camera as disconnected.
  A single small request checks for its return every 10 s, backing off to
  every 30 s; when it answers again everything is refreshed and push updates
  resume. Going offline and coming back are logged as information only.
- **Light on Home Assistant**: pushed changes only update the entities that
  read the changed value, so several cameras can run side by side.
- **Sorted entities**: main controls, sensors, configuration and diagnostics
  are separated by entity category, grouped by name prefix (*Lens …*,
  *Audio 1 …*, *Monitor LCD …*, *Color …*, *Stream …*) and niche entities are
  disabled by default.
- English and Dutch translations.

## Supported cameras

Any camera with the Blackmagic Camera Control REST API, among others:

| Camera | Status |
| --- | --- |
| Blackmagic PYXIS 6K | Tested (firmware 10.2) |
| Blackmagic PYXIS 12K, Cinema Camera 6K | Should work (same API) |
| Blackmagic URSA Cine 12K LF / 17K 65 / Immersive | Should work (same API) |
| Blackmagic URSA Broadcast G2 | Should work (same API) |
| Blackmagic Studio Camera 4K Plus / Pro (G2), 6K Pro, Micro Studio Camera 4K G2 | Should work (same API) |
| Blackmagic Pocket Cinema Camera 4K / 6K G2 / 6K Pro | Should work on firmware with the REST API |

Feedback from owners of other models is very welcome; please attach the
diagnostics file to an issue.

## Requirements

1. The camera is on your network (Ethernet, or USB-C Ethernet adapter).
2. In **Blackmagic Camera Setup → Network Access**, set **Web media manager**
   to **Enabled**. The REST API is served by the web media manager; without it
   the camera has no network control at all (older Blackmagic cameras without
   this option can only be controlled over Bluetooth or SDI, which this
   integration does not use).
   - With **Enabled with security only** the API runs over HTTPS with a
     username and password: turn on *Use HTTPS* and fill in the credentials
     when adding the camera.

## Installation

### HACS (recommended)

1. Click the **Open in HACS** badge above, or in HACS open the menu (⋮) →
   **Custom repositories**, add `https://github.com/Videobarista/blackmagic-camera-ha`
   with category **Integration**.
2. Download **Blackmagic Camera** and restart Home Assistant.

### Manual

1. Copy `custom_components/blackmagic_camera` into the `custom_components`
   folder of your Home Assistant configuration.
2. Restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → Blackmagic Camera**

| Field | Description |
| --- | --- |
| Host | IP address or hostname of the camera, e.g. `192.168.1.50` or `PYXIS-6K.local` |
| Use HTTPS | For web media manager set to *Enabled with security only* |
| Verify SSL certificate | Leave off for the camera's default self-signed certificate |
| Username / Password | Only when secure login is configured on the camera |

The connection can be changed later with **Reconfigure**. Under
**Configure** you can set the polling interval (default 10 s) and turn push
updates (websocket) off.

## Entities

Entities are only created for features your camera reports. Names with
`{n}` exist per audio channel, media slot or function button, names with
`{display}` per monitoring output (for example LCD, FrontUSBC, MainSDI).

### Controls

| Entity | Type | Notes |
| --- | --- | --- |
| Record | switch | Start / stop recording |
| Tally | select | Off / Preview / Program (can be driven by Home Assistant) |
| ISO, Gain, Shutter, ND filter | select | Values offered by the camera; shutter follows angle or speed mode |
| Auto exposure | select | Off / Continuous / One shot |
| White balance, Tint | number | Range reported by the camera |
| Auto white balance | button | |
| Lens iris, Lens focus, Lens zoom | number | Available when the lens can be controlled |
| Lens autofocus | button | One-shot autofocus |
| Preset | select | Camera presets (`.cset`) |
| Color bars | switch | |
| Start prerecord | button | |
| Function button {n} | button | Presses the camera's assignable F buttons |

### Sensors

| Entity | Notes |
| --- | --- |
| Remaining record time | Of the active media |
| Media slot {n} | Free space in %; volume, clip count and sizes as attributes |
| Battery, Voltage, Power source | |
| Lens aperture, Lens focal length | |
| Prerecording | binary sensor |

### Configuration (entity category *config*)

Shutter measurement, Dynamic range, Active media, Proxy recording, Auto
prerecord, Prerecord duration, Detail sharpening (+ level), Lens image
stabilization, Lens autofocus enabled / mode, Monitor {display} display LUT /
zebra / focus assist / false color, Audio {n} input / gain / phantom power /
pad / low cut filter.

Disabled by default: Capture still (records a still frame to the active
media; PYXIS firmware 10.2 answers this API call with an error while the
camera's own still button works), Focus assist mode / color / intensity, Program feed
display, Frame guide ratio, Safe area size,
Monitor {display} clean feed / frame guide / frame grids / safe area /
brightness, all **Color …** correction values and **Color correction reset**.

### Diagnostic

Connection (stays available while the camera is off), Codec, Frame rate,
Recording resolution, Reference lock, Lens autofocus state, Video format
(studio cameras). Disabled by default: Transport mode.

### Livestream (optional)

For people who use the camera's built-in streaming. All of these are
**disabled by default**; enable the ones you need:

| Entity | Notes |
| --- | --- |
| Stream | switch: start / stop the livestream |
| Stream status | Idle, Connecting, Streaming, Flushing, Interrupted |
| Stream bitrate, Stream duration, Stream cache | |
| Stream available | Including the reasons when streaming is not possible |
| Stream platform | Platform, server and quality (the stream key is never exposed) |

The stream destination itself is configured on the camera or with a
Blackmagic streaming XML file in Blackmagic Camera Setup.

### No camera image

The REST API has no endpoint for a live picture or snapshot, so this
integration cannot offer a camera entity. *Capture still* stores a
Blackmagic RAW frame on the camera's media; it is not sent to Home Assistant.
For a picture in Home Assistant, feed the camera's HDMI/SDI output into a
streaming encoder or capture device that Home Assistant supports.

## Troubleshooting

The **Connection** sensor shows whether the camera answers; its
`offline_reason` attribute tells why it does not:

| `offline_reason` | Meaning | What to do |
| --- | --- | --- |
| `unreachable` | No answer at all | Camera off, asleep or not on the network. Nothing to do: it is picked up automatically. |
| `api_unavailable` | The camera answers, but the REST API returns 404 | In Blackmagic Camera Setup → Network Access set **Web media manager** to **Enabled**. A repair notice is shown in Home Assistant while this lasts. |
| `https_required` | The camera redirects to HTTPS (web media manager **Enabled with security only**) | Reconfigure the integration with **Use HTTPS** and the username and password from Secure login, or set **Web media manager** back to **Enabled**. A repair notice is shown while this lasts. |

In both cases the integration keeps checking (every 10 s, backing off to 30 s)
and recovers by itself, including the push connection.

## Removal

1. **Settings → Devices & services → Blackmagic Camera → ⋮ → Delete**.
2. Remove the repository from HACS (or delete
   `custom_components/blackmagic_camera`) and restart Home Assistant.

Nothing is changed on the camera when the integration is removed.

## Quality scale

This integration is aligned with the Home Assistant quality scale: **Bronze**
(self assessment in
[`quality_scale.yaml`](custom_components/blackmagic_camera/quality_scale.yaml)).
Home Assistant only grades core integrations, so no official tier is claimed.

## Disclaimer

This project is not affiliated with, endorsed by, sponsored by or in any way
officially connected to Blackmagic Design Pty. Ltd. "Blackmagic Design",
"PYXIS", "URSA" and related names are trademarks of their respective owners
and are used here only to describe compatibility. The icons in this
repository are generic drawings and not Blackmagic Design artwork.

The software is provided "as is", without warranty of any kind. Use it at
your own risk; you are responsible for your camera, recordings and media.

## License

[MIT](LICENSE) © 2026 Videobarista
