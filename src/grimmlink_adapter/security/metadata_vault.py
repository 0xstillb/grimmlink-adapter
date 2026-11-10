"""Authenticated encryption for durable background credentials."""

import os
from pathlib import Path

from cryptography.fernet import Fernet

from grimmlink_adapter.config import settings
from grimmlink_adapter.state.database import get_db_path


def metadata_cipher() -> Fernet:
    path = Path(settings.METADATA_KEY_FILE) if settings.METADATA_KEY_FILE else get_db_path().with_suffix(".metadata.key")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
    return Fernet(path.read_bytes().strip())
