"""Constants for the Blackmagic Camera integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "blackmagic_camera"
MANUFACTURER: Final = "Blackmagic Design"

CONF_USE_HTTPS: Final = "use_https"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_USE_WEBSOCKET: Final = "use_websocket"

DEFAULT_SCAN_INTERVAL: Final = 10
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 300
DEFAULT_USE_WEBSOCKET: Final = True

# An offline camera is checked every scan interval, doubling up to this (s).
OFFLINE_RETRY_MAX: Final = 30
# "Slow" properties are refreshed every N polls.
SLOW_POLL_EVERY: Final = 6
# Properties covered by the websocket are re-read every N polls as a safety net.
SAFETY_POLL_EVERY: Final = 30

REQUEST_TIMEOUT: Final = 10
# Answers to a partial update that are retried with the full object merged in.
REJECTED_STATUS: Final = frozenset({400, 422})
MAX_PARALLEL_REQUESTS: Final = 4

STORAGE_VERSION: Final = 1

API_BASE_PATH: Final = "/control/api/v1"
# The notification websocket lives next to the event API on current firmware;
# the second path is tried for other models.
WEBSOCKET_PATHS: Final = ("/control/api/v1/event/websocket", "/control/websocket")

# ---------------------------------------------------------------------------
# REST API property paths
# ---------------------------------------------------------------------------
P_PRODUCT: Final = "/system/product"
P_SYSTEM_FORMAT: Final = "/system/format"
P_VIDEO_FORMAT: Final = "/system/videoFormat"
P_CODEC_FORMAT: Final = "/system/codecFormat"
P_DYNAMIC_RANGE: Final = "/system/dynamicRange"
P_SUPPORTED_DYNAMIC_RANGES: Final = "/system/supportedDynamicRanges"

P_TRANSPORT: Final = "/transports/0"
P_RECORD: Final = "/transports/0/record"
P_STOP: Final = "/transports/0/stop"
P_STILL_CAPTURE: Final = "/transports/0/doStillCapture"
P_PRERECORD: Final = "/transports/0/prerecord"
P_PRERECORD_AUTO: Final = "/transports/0/prerecord/auto"
P_PRERECORD_MAX: Final = "/transports/0/prerecord/maxDuration"
P_PRERECORD_SUPPORTED: Final = "/transports/0/prerecord/supportedMaxDurations"
P_PROXY_RECORDING: Final = "/transports/0/proxyRecording"

P_WORKINGSET: Final = "/media/workingset"
P_MEDIA_ACTIVE: Final = "/media/active"

P_PRESETS: Final = "/presets"
P_PRESET_ACTIVE: Final = "/presets/active"

P_AUDIO_CHANNELS: Final = "/audio/channels"

P_IRIS: Final = "/lens/iris"
P_IRIS_DESC: Final = "/lens/iris/description"
P_ZOOM: Final = "/lens/zoom"
P_ZOOM_DESC: Final = "/lens/zoom/description"
P_FOCUS: Final = "/lens/focus"
P_FOCUS_DESC: Final = "/lens/focus/description"
P_DO_AUTOFOCUS: Final = "/lens/focus/doAutoFocus"
P_AUTOFOCUS: Final = "/lens/focus/autoFocus"
P_AUTOFOCUS_DESC: Final = "/lens/focus/autoFocus/description"
P_OIS: Final = "/lens/opticalImageStabilization"

P_ISO: Final = "/video/iso"
P_SUPPORTED_ISOS: Final = "/video/supportedISOs"
P_GAIN: Final = "/video/gain"
P_SUPPORTED_GAINS: Final = "/video/supportedGains"
P_WHITE_BALANCE: Final = "/video/whiteBalance"
P_WHITE_BALANCE_DESC: Final = "/video/whiteBalance/description"
P_AUTO_WHITE_BALANCE: Final = "/video/whiteBalance/doAuto"
P_TINT: Final = "/video/whiteBalanceTint"
P_TINT_DESC: Final = "/video/whiteBalanceTint/description"
P_ND_FILTER: Final = "/video/ndFilter"
P_SUPPORTED_ND: Final = "/video/supportedNDFilters"
P_SHUTTER: Final = "/video/shutter"
P_SHUTTER_MEASUREMENT: Final = "/video/shutter/measurement"
P_SUPPORTED_SHUTTERS: Final = "/video/supportedShutters"
P_AUTO_EXPOSURE: Final = "/video/autoExposure"
P_DETAIL_SHARPENING: Final = "/video/detailSharpening"
P_DETAIL_SHARPENING_LEVEL: Final = "/video/detailSharpeningLevel"

P_COLOR_BARS: Final = "/camera/colorBars"
P_PROGRAM_FEED: Final = "/camera/programFeedDisplay"
P_TALLY: Final = "/camera/tallyStatus"
P_POWER: Final = "/camera/power"
P_REFERENCE_LOCK: Final = "/camera/timingReferenceLock"

P_DISPLAYS: Final = "/monitoring/display"
P_FOCUS_ASSIST: Final = "/monitoring/focusAssist"
P_FRAME_GUIDE_RATIO: Final = "/monitoring/frameGuideRatio"
P_FRAME_GUIDE_PRESETS: Final = "/monitoring/frameGuideRatio/presets"
P_SAFE_AREA_PERCENT: Final = "/monitoring/safeAreaPercent"

P_CC_LIFT: Final = "/colorCorrection/lift"
P_CC_GAMMA: Final = "/colorCorrection/gamma"
P_CC_GAIN: Final = "/colorCorrection/gain"
P_CC_OFFSET: Final = "/colorCorrection/offset"
P_CC_CONTRAST: Final = "/colorCorrection/contrast"
P_CC_COLOR: Final = "/colorCorrection/color"
P_CC_LUMA: Final = "/colorCorrection/lumaContribution"

P_STREAM: Final = "/livestreams/0"
P_STREAM_START: Final = "/livestreams/0/start"
P_STREAM_STOP: Final = "/livestreams/0/stop"
P_STREAM_PLATFORM: Final = "/livestreams/0/activePlatform"
P_STREAM_AVAILABLE: Final = "/livestreams/0/available"

P_EVENT_LIST: Final = "/event/list"
P_PANEL_DEVICES: Final = "/panel/devices"
EVENT_STILL_CAPTURED: Final = "/transports/0/stillCaptured"

# Per display monitoring features ({display} is substituted).
MONITOR_FEATURES: Final = (
    "cleanFeed",
    "displayLUT",
    "zebra",
    "focusAssist",
    "frameGuide",
    "frameGrids",
    "safeArea",
    "falseColor",
)
MONITOR_BRIGHTNESS: Final = "brightness"

# Per audio channel properties ({channel} is substituted, 0-based).
AUDIO_FEATURES: Final = (
    "input",
    "input/description",
    "supportedInputs",
    "level",
    "phantomPower",
    "padding",
    "lowCutFilter",
)


def monitor_path(display: str, feature: str) -> str:
    """Return the property path of a per display monitoring feature."""
    return f"/monitoring/{display}/{feature}"


def audio_path(channel: int, feature: str) -> str:
    """Return the property path of a per channel audio feature."""
    return f"/audio/channel/{channel}/{feature}"


# Global properties that are probed once and then kept up to date.
GLOBAL_PATHS: Final = (
    P_SYSTEM_FORMAT,
    P_VIDEO_FORMAT,
    P_CODEC_FORMAT,
    P_DYNAMIC_RANGE,
    P_SUPPORTED_DYNAMIC_RANGES,
    P_TRANSPORT,
    P_RECORD,
    P_PRERECORD,
    P_PRERECORD_AUTO,
    P_PRERECORD_MAX,
    P_PRERECORD_SUPPORTED,
    P_PROXY_RECORDING,
    P_WORKINGSET,
    P_MEDIA_ACTIVE,
    P_PRESETS,
    P_PRESET_ACTIVE,
    P_IRIS,
    P_IRIS_DESC,
    P_ZOOM,
    P_ZOOM_DESC,
    P_FOCUS,
    P_FOCUS_DESC,
    P_AUTOFOCUS,
    P_AUTOFOCUS_DESC,
    P_OIS,
    P_ISO,
    P_SUPPORTED_ISOS,
    P_GAIN,
    P_SUPPORTED_GAINS,
    P_WHITE_BALANCE,
    P_WHITE_BALANCE_DESC,
    P_TINT,
    P_TINT_DESC,
    P_ND_FILTER,
    P_SUPPORTED_ND,
    P_SHUTTER,
    P_SHUTTER_MEASUREMENT,
    P_SUPPORTED_SHUTTERS,
    P_AUTO_EXPOSURE,
    P_DETAIL_SHARPENING,
    P_DETAIL_SHARPENING_LEVEL,
    P_COLOR_BARS,
    P_PROGRAM_FEED,
    P_TALLY,
    P_POWER,
    P_REFERENCE_LOCK,
    P_FOCUS_ASSIST,
    P_FRAME_GUIDE_RATIO,
    P_FRAME_GUIDE_PRESETS,
    P_SAFE_AREA_PERCENT,
    P_CC_LIFT,
    P_CC_GAMMA,
    P_CC_GAIN,
    P_CC_OFFSET,
    P_CC_CONTRAST,
    P_CC_COLOR,
    P_CC_LUMA,
    P_STREAM,
    P_STREAM_PLATFORM,
    P_STREAM_AVAILABLE,
)

# Properties that change often and are polled on every cycle when the
# websocket is not available. Everything else is polled less frequently.
FAST_PATHS: Final = frozenset(
    {
        P_TRANSPORT,
        P_RECORD,
        P_PRERECORD,
        P_WORKINGSET,
        P_MEDIA_ACTIVE,
        P_IRIS,
        P_ZOOM,
        P_FOCUS,
        P_AUTOFOCUS,
        P_ISO,
        P_GAIN,
        P_WHITE_BALANCE,
        P_TINT,
        P_ND_FILTER,
        P_SHUTTER,
        P_AUTO_EXPOSURE,
        P_COLOR_BARS,
        P_TALLY,
        P_POWER,
        P_PRESET_ACTIVE,
        P_STREAM,
    }
)

# Keys that must never show up in diagnostics.
REDACT_KEYS: Final = {
    "password",
    "username",
    "key",
    "passphrase",
    "url",
    "credentials",
}
