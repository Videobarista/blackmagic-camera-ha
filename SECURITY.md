# Security Policy

## Supported versions

Only the latest release of this integration receives fixes.

| Version | Supported |
| ------- | --------- |
| 1.1.x   | Yes       |
| < 1.1   | No        |

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Use GitHub's private vulnerability reporting instead:
**Security** tab → **Report a vulnerability** on
<https://github.com/Videobarista/blackmagic-camera-ha>.

Include what you found, how to reproduce it and which version you use. You
can expect a first reply within a week.

## Scope

This integration talks to cameras on your local network through their REST
API. Keep in mind:

- The camera's web media manager (which also serves the REST API) has no
  authentication unless **Enabled with security only** is chosen in
  Blackmagic Camera Setup. Anyone on the same network can control a camera
  that has plain HTTP access enabled.
- Credentials are stored in the Home Assistant config entry (like every
  integration) and are redacted from diagnostics, as are stream keys,
  passphrases, the camera name and media volume names. Protect your
  Home Assistant host and backups accordingly.
- With HTTPS and the camera's self-signed certificate, the certificate is
  pinned at first contact. Confirm a changed certificate only when you know
  why it changed (firmware update, reset, new certificate).

The README has a [Security section](README.md#security) with what the
integration does and recommended network settings.
