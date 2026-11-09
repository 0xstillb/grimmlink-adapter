"""Serialize token rotation shared by reader and background authentication."""

import asyncio

credential_refresh_lock = asyncio.Lock()
