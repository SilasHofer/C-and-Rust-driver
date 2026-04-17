# Memory Safety Analysis — Summary

Generated: 2026-04-17T15:09:40

## C Driver

- Build: `ok`

### memcheck

- Log: `/home/pi/Documents/C-and-Rust-driver/Python_tests/Logs/memory_safety_20260417_145956/C/c_driver_memcheck.valgrind.log`
- errors: `130371`
- error_contexts: `8`
- definitely_lost_bytes: `0`
- definitely_lost_blocks: `0`
- indirectly_lost_bytes: `0`
- indirectly_lost_blocks: `0`
- possibly_lost_bytes: `0`
- possibly_lost_blocks: `0`
- still_reachable_bytes: `4096`
- still_reachable_blocks: `1`
- suppressed_bytes: `0`
- suppressed_blocks: `0`
- invalid_read: `0`
- invalid_write: `0`
- invalid_free: `0`
- mismatched_free: `0`
- uninitialised_value: `0`
- uninitialised_syscall: `8`

### helgrind

- Log: `/home/pi/Documents/C-and-Rust-driver/Python_tests/Logs/memory_safety_20260417_145956/C/c_driver_helgrind.valgrind.log`
- errors: `0`
- error_contexts: `0`
- data_races: `0`
- lock_order: `0`

### massif

- Log: `/home/pi/Documents/C-and-Rust-driver/Python_tests/Logs/memory_safety_20260417_145956/C/c_driver_massif.valgrind.log`

## Rust Driver

- Build: `ok`
- `unsafe` occurrences (local src/): **5**
  - `unsafe fn`: 0
  - `unsafe impl`: 0
  - `unsafe trait`: 0
  - `unsafe block`: 5

### memcheck

- Log: `/home/pi/Documents/C-and-Rust-driver/Python_tests/Logs/memory_safety_20260417_145956/Rust/rust_driver_memcheck.valgrind.log`
- errors: `133023`
- error_contexts: `8`
- definitely_lost_bytes: `0`
- definitely_lost_blocks: `0`
- indirectly_lost_bytes: `0`
- indirectly_lost_blocks: `0`
- possibly_lost_bytes: `0`
- possibly_lost_blocks: `0`
- still_reachable_bytes: `1034`
- still_reachable_blocks: `2`
- suppressed_bytes: `0`
- suppressed_blocks: `0`
- invalid_read: `0`
- invalid_write: `0`
- invalid_free: `0`
- mismatched_free: `0`
- uninitialised_value: `0`
- uninitialised_syscall: `8`

### helgrind

- Log: `/home/pi/Documents/C-and-Rust-driver/Python_tests/Logs/memory_safety_20260417_145956/Rust/rust_driver_helgrind.valgrind.log`
- errors: `0`
- error_contexts: `0`
- data_races: `0`
- lock_order: `0`

### massif

- Log: `/home/pi/Documents/C-and-Rust-driver/Python_tests/Logs/memory_safety_20260417_145956/Rust/rust_driver_massif.valgrind.log`

### AddressSanitizer

- status: `skipped`

### cargo geiger (unsafe across deps)

- status: `skipped`

### cargo clippy (unsafe-focused lints)

- Total warnings: `0`
- Log: `/home/pi/Documents/C-and-Rust-driver/Python_tests/Logs/memory_safety_20260417_145956/Rust/cargo_clippy.log`
  - `clippy::undocumented_unsafe_blocks`: 0
  - `clippy::multiple_unsafe_ops_per_block`: 0
  - `clippy::missing_safety_doc`: 0
  - `clippy::unnecessary_safety_comment`: 0
  - `clippy::unnecessary_safety_doc`: 0
  - `clippy::cast_ptr_alignment`: 0
  - `clippy::ptr_as_ptr`: 0
  - `clippy::transmute_ptr_to_ref`: 0

### cargo audit (RustSec advisory DB)

- status: `skipped`

## Thesis Metrics Mapping

| Thesis metric | Source |
|---|---|
| Memory leaks (C) | memcheck `definitely_lost_*`, `indirectly_lost_*` |
| Memory leaks (Rust) | memcheck same fields + ASan `memory_leaks` |
| Invalid memory accesses (C) | memcheck `invalid_read`, `invalid_write` |
| Invalid memory accesses (Rust) | memcheck same + ASan `heap_use_after_free`, `*_buffer_overflow` |
| Buffer overflow incidents | memcheck heap/stack invalid access + ASan `*_buffer_overflow` |
| Number of `unsafe` blocks (Rust, local) | `unsafe_audit.total` |
| `unsafe` usage across deps (Rust) | `geiger.unsafe_*_used/total` |
| Undocumented unsafe (Rust) | clippy `undocumented_unsafe_blocks` |
| Known-vulnerable deps (Rust) | cargo-audit `vulnerabilities` |
