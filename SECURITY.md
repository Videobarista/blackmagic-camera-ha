# Security Policy

## Supported versions

Only the latest release of this integration receives fixes.

| Version | Supported |
| ------- | --------- |
| 1.0.x   | Yes       |
| < 1.0   | No        |

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
- Credentials are stored in the Home Assistant config entry and are redacted
  from diagnostics, as are stream keys and passphrases.
