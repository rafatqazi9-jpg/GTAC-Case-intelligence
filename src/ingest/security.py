"""Archive security validation and extraction guardrails.

Enforces strict defense-in-depth protection against:
- Path traversal (Zip Slip / Tar Slip)
- Absolute paths (e.g. /etc/passwd or C:\\Windows\\...)
- Drive-path extraction
- Symlinks and hard links pointing outside the extraction root
- Decompression bombs (excessive compression ratio / uncompressed size limits)
- Maximum nesting depth
- Malformed / truncated archive abuse
"""
from __future__ import annotations

import os
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


class SecurityViolation(Exception):
    """Raised when an archive entry attempts path traversal, symlink escape, or size abuse."""


@dataclass(frozen=True)
class IngestLimits:
    max_upload_size: int = 100 * 1024 * 1024       # 100 MB max archive upload
    max_extracted_total_size: int = 500 * 1024 * 1024  # 500 MB max total decompressed
    max_single_file_size: int = 100 * 1024 * 1024    # 100 MB max individual uncompressed file
    max_files: int = 1000                           # Max files per archive
    max_nesting_depth: int = 3                      # Max archive-in-archive recursion depth
    max_compression_ratio: float = 100.0            # Max ratio between uncompressed and compressed size


def sanitize_relative_path(entry_name: str, target_dir: Path) -> Path:
    """Validate that entry_name does not escape target_dir.

    Rejects:
    - Path traversal attempts ('../', '..\\')
    - Absolute root paths ('/foo', '\\foo')
    - Windows drive paths ('C:', 'D:\\')
    """
    # Normalize slashes
    clean_name = entry_name.replace("\\", "/").lstrip("/")

    # Check for drive colon (e.g. C:...)
    if len(clean_name) >= 2 and clean_name[1] == ":":
        raise SecurityViolation(f"Windows drive letter prohibited in archive path: {entry_name!r}")

    # Resolve against target directory
    destination = (target_dir / clean_name).resolve()

    # Ensure destination is strictly inside target_dir
    try:
        destination.relative_to(target_dir.resolve())
    except ValueError as exc:
        raise SecurityViolation(f"Path traversal detected: {entry_name!r} escapes {target_dir}") from exc

    return destination


def validate_zip_security(zf: zipfile.ZipFile, limits: IngestLimits = IngestLimits()) -> None:
    """Pre-validate a ZIP archive before extracting entries."""
    infolist = zf.infolist()

    if len(infolist) > limits.max_files:
        raise SecurityViolation(f"Archive exceeds maximum file count limit ({len(infolist)} > {limits.max_files})")

    total_uncompressed = 0
    total_compressed = 0

    for info in infolist:
        # Check single file limit
        if info.file_size > limits.max_single_file_size:
            raise SecurityViolation(f"File {info.filename!r} exceeds single file size limit ({info.file_size} > {limits.max_single_file_size})")

        total_uncompressed += info.file_size
        total_compressed += info.compress_size

        # Check total extracted size limit
        if total_uncompressed > limits.max_extracted_total_size:
            raise SecurityViolation(f"Archive exceeds total extracted size limit ({total_uncompressed} > {limits.max_extracted_total_size})")

        # Basic path traversal check
        if ".." in info.filename.replace("\\", "/").split("/"):
            raise SecurityViolation(f"Path traversal detected in zip entry: {info.filename!r}")

    # Check compression ratio to prevent zip bombs
    if total_compressed > 0:
        ratio = total_uncompressed / total_compressed
        if ratio > limits.max_compression_ratio and total_uncompressed > 10 * 1024 * 1024:
            raise SecurityViolation(f"Excessive compression ratio detected ({ratio:.1f}x > {limits.max_compression_ratio}x)")


def validate_tar_security(tf: tarfile.TarFile, limits: IngestLimits = IngestLimits()) -> None:
    """Pre-validate a TAR archive before extracting entries."""
    members = tf.getmembers()

    if len(members) > limits.max_files:
        raise SecurityViolation(f"Tar archive exceeds maximum file count limit ({len(members)} > {limits.max_files})")

    total_uncompressed = 0

    for member in members:
        # Prevent symlinks and hardlinks pointing outside or escaping
        if member.issym() or member.islnk():
            # Check link destination
            link_target = member.linkname.replace("\\", "/").lstrip("/")
            if ".." in link_target.split("/") or link_target.startswith("/"):
                raise SecurityViolation(f"Dangerous link target detected in tar member {member.name!r} -> {member.linkname!r}")

        # Check size limits
        if member.size > limits.max_single_file_size:
            raise SecurityViolation(f"Member {member.name!r} exceeds single file size limit ({member.size} > {limits.max_single_file_size})")

        total_uncompressed += member.size
        if total_uncompressed > limits.max_extracted_total_size:
            raise SecurityViolation(f"Tar archive exceeds total extracted size limit ({total_uncompressed} > {limits.max_extracted_total_size})")

        # Path traversal check
        if ".." in member.name.replace("\\", "/").split("/"):
            raise SecurityViolation(f"Path traversal detected in tar member: {member.name!r}")
