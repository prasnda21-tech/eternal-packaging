"""Storage provider implementations.

Providers implement a small contract (see `base.StorageProvider`) and are
selected at process start based on env `STORAGE_PROVIDER`. Callers should NOT
import providers directly — use `services.storage` module which acts as a
thin dispatcher and preserves the historic function-level API.
"""
