"""Format detection and validation for network diagnostic logs and archives.

Inspects magic bytes and file signatures rather than trusting file extensions alone.
Handles text logs, ZIP archives, TAR archives, and various compression algorithms
(gzip, bzip2, xz).
"""
from __future__ import annotations

import io
import tarfile
import zipfile
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO


class FileFormat(str, Enum):
    TEXT = "text"
    ZIP = "zip"
    TAR = "tar"
    GZIP = "gzip"
    BZIP2 = "bzip2"
    XZ = "xz"
    UNKNOWN = "unknown"


# Standard Magic Byte Signatures
_SIG_ZIP = b"PK\x03\x04"
_SIG_ZIP_EMPTY = b"PK\x05\x06"
_SIG_ZIP_SPANNED = b"PK\x07\x08"
_SIG_GZIP = b"\x1f\x8b"
_SIG_BZIP2 = b"BZh"
_SIG_XZ = b"\xfd7zXZ\x00"
_SIG_7Z = b"7z\xbc\xaf\x27\x1c"


@dataclass(frozen=True)
class DetectionResult:
    format: FileFormat
    mime_type: str
    is_archive: bool
    is_compressed: bool
    description: str


def detect_format(stream: BinaryIO | bytes, filename: str = "") -> DetectionResult:
    """Detect the format of a file or byte stream using magic bytes and structural checks.

    Re-winds stream to beginning if a file-like object is provided.
    """
    if isinstance(stream, bytes):
        raw_header = stream[:512]
        file_obj = io.BytesIO(stream)
    else:
        pos = stream.tell()
        raw_header = stream.read(512)
        stream.seek(pos)
        file_obj = stream

    # 1. Check Magic Bytes for Compression / Outer Archive Containers
    if raw_header.startswith((_SIG_ZIP, _SIG_ZIP_EMPTY, _SIG_ZIP_SPANNED)):
        # Validate that zipfile can at least inspect the header
        is_valid_zip = zipfile.is_zipfile(file_obj)
        if isinstance(stream, io.IOBase):
            stream.seek(pos)
        if is_valid_zip:
            return DetectionResult(
                format=FileFormat.ZIP,
                mime_type="application/zip",
                is_archive=True,
                is_compressed=True,
                description="ZIP Archive",
            )

    if raw_header.startswith(_SIG_GZIP):
        # Could be .tar.gz or standalone .gz
        desc = "Gzip Compressed Tar Archive" if (".tar" in filename.lower() or filename.lower().endswith(".tgz")) else "Gzip Compressed Stream"
        return DetectionResult(
            format=FileFormat.GZIP,
            mime_type="application/gzip",
            is_archive=True,
            is_compressed=True,
            description=desc,
        )

    if raw_header.startswith(_SIG_BZIP2):
        desc = "Bzip2 Compressed Tar Archive" if (".tar" in filename.lower() or filename.lower().endswith(".tbz2")) else "Bzip2 Compressed Stream"
        return DetectionResult(
            format=FileFormat.BZIP2,
            mime_type="application/x-bzip2",
            is_archive=True,
            is_compressed=True,
            description=desc,
        )

    if raw_header.startswith(_SIG_XZ):
        desc = "XZ Compressed Tar Archive" if (".tar" in filename.lower() or filename.lower().endswith(".txz")) else "XZ Compressed Stream"
        return DetectionResult(
            format=FileFormat.XZ,
            mime_type="application/x-xz",
            is_archive=True,
            is_compressed=True,
            description=desc,
        )

    # 2. Check for Plain Uncompressed Tar Archive
    # Tar archives contain "ustar" magic at offset 257 in standard POSIX tar
    if len(raw_header) >= 262 and (raw_header[257:262] == b"ustar" or raw_header[257:265] == b"ustar  \x00"):
        return DetectionResult(
            format=FileFormat.TAR,
            mime_type="application/x-tar",
            is_archive=True,
            is_compressed=False,
            description="POSIX Tar Archive",
        )

    # Attempt tarfile detection if it's a tarfile structurally
    try:
        if tarfile.is_tarfile(file_obj if not isinstance(stream, bytes) else io.BytesIO(stream)):
            if isinstance(stream, io.IOBase):
                stream.seek(pos)
            return DetectionResult(
                format=FileFormat.TAR,
                mime_type="application/x-tar",
                is_archive=True,
                is_compressed=False,
                description="Tar Archive",
            )
    except Exception:
        pass

    if isinstance(stream, io.IOBase):
        stream.seek(pos)

    # 3. Check for Readable Text / Network Diagnostic Log
    try:
        sample = raw_header.decode("utf-8")
        # Ensure it doesn't contain an excessive amount of control / null characters
        null_count = raw_header.count(b"\x00")
        if null_count == 0 or (len(raw_header) > 0 and null_count / len(raw_header) < 0.01):
            return DetectionResult(
                format=FileFormat.TEXT,
                mime_type="text/plain",
                is_archive=False,
                is_compressed=False,
                description="Text Log or Configuration File",
            )
    except UnicodeDecodeError:
        pass

    # Fallback to ASCII / Latin-1 check
    try:
        raw_header.decode("latin-1")
        null_count = raw_header.count(b"\x00")
        if null_count == 0:
            return DetectionResult(
                format=FileFormat.TEXT,
                mime_type="text/plain; charset=latin-1",
                is_archive=False,
                is_compressed=False,
                description="Latin-1 Encoded Log File",
            )
    except Exception:
        pass

    return DetectionResult(
        format=FileFormat.UNKNOWN,
        mime_type="application/octet-stream",
        is_archive=False,
        is_compressed=False,
        description="Binary or Unsupported File Format",
    )
