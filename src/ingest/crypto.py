"""Encryption detection and password-protected archive handling.

Detects encrypted entries in ZIP archives and provides authenticated in-memory
decryption. Never brute-forces passwords or persists credentials.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import BinaryIO


class DecryptionError(Exception):
    """Raised when password decryption fails due to incorrect password or unsupported cipher."""


@dataclass(frozen=True)
class EncryptionStatus:
    is_encrypted: bool
    encrypted_entries: list[str]
    encryption_algorithm: str = "none"   # "zipcrypto" | "aes" | "none"


def inspect_zip_encryption(zf: zipfile.ZipFile) -> EncryptionStatus:
    """Inspect a ZipFile to detect if any entries are encrypted."""
    encrypted: list[str] = []
    algo = "none"

    for info in zf.infolist():
        # Bit 0 of flag_bits indicates encryption
        if info.flag_bits & 0x1:
            encrypted.append(info.filename)
            # AES encryption in zip files typically uses compression type 99 or extra field 0x9901
            if info.compress_type == 99:
                algo = "aes"
            elif algo == "none":
                algo = "zipcrypto"

    return EncryptionStatus(
        is_encrypted=len(encrypted) > 0,
        encrypted_entries=encrypted,
        encryption_algorithm=algo,
    )


def verify_zip_password(zf: zipfile.ZipFile, password: str) -> bool:
    """Verify if a password correctly decrypts the first encrypted entry without full extraction."""
    pwd_bytes = password.encode("utf-8")
    for info in zf.infolist():
        if info.flag_bits & 0x1:
            try:
                # Attempt to read first 64 bytes of the entry
                with zf.open(info, "r", pwd=pwd_bytes) as entry:
                    entry.read(64)
                return True
            except (RuntimeError, zipfile.BadZipFile):
                return False
    return True
