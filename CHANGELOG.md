# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-05-08

### Added

- Initial scaffold: Flask app, Postgres sibling, nginx routing under `/rebooter/`.
- Device API: register, heartbeat, command poll, command result, events upload, firmware check.
- Admin API: device list/detail/update, groups, group commands, firmware releases, firmware deployments, events query, sites CRUD.
- Admin web UI under `/rebooter/app/` (Jinja-rendered): dashboard, devices, device detail, enrollment tokens, groups, group detail, sites, firmware, events.
- Single-use enrollment tokens, admin-issued.
- Firmware binaries served directly by nginx from RAID6 volume; SHA-256 verified on upload.
- Per-device firmware assignments materialised from group/site/all_devices deployments; later deployments supersede pending ones.
- APScheduler in-process job: command expiry sweep every 30 s (single-worker via Postgres advisory lock).
- Locked v0.1 command payload schemas for `set_mode` and `apply_config` (agreed with firmware/design team 2026-05-09); malformed requests are rejected with `validation_failed`.
