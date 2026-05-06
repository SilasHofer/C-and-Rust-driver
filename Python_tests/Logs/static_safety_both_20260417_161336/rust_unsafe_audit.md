# Rust `unsafe` Code Audit

Generated: 2026-04-17T16:13:36.996667

## Summary
See the static_run.log file for cargo geiger results.

## Manual Review Instructions
For every `unsafe` block:
- **Purpose**: What hardware/FFI operation does it perform?
- **Justification**: Why can't this be done in safe Rust?

