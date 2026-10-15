"""Modular diagnostic archive ingestion pipeline.

Orchestrates format detection, security verification, scoped sandboxed extraction,
and normalized text log aggregation.
"""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from .crypto import DecryptionError, inspect_zip_encryption, verify_zip_password
from .detector import FileFormat, detect_format
from .security import IngestLimits, SecurityViolation, sanitize_relative_path, validate_tar_security, validate_zip_security


@dataclass
class IngestionItem:
    original_path: str
    relative_path: str
    size_bytes: int
    is_text: bool
    status: str            # "processed" | "skipped" | "encrypted" | "error"
    reason: str = ""
    extracted_text: str | None = None


@dataclass
class IngestionReport:
    original_filename: str
    detected_format: str
    total_entries: int = 0
    processed_count: int = 0
    skipped_count: int = 0
    is_encrypted: bool = False
    requires_password: bool = False
    encryption_type: str = "none"
    items: list[IngestionItem] = field(default_factory=list)
    combined_log_text: str = ""
    errors: list[str] = field(default_factory=list)


class IngestionPipeline:
    """Safely ingests diagnostic archives and files in a controlled temp environment."""

    def __init__(self, limits: IngestLimits = IngestLimits()) -> None:
        self.limits = limits

    def ingest_bytes(
        self,
        data: bytes,
        filename: str,
        password: str | None = None,
    ) -> IngestionReport:
        """Process byte buffer through format detection and secure sandboxed extraction."""
        if len(data) > self.limits.max_upload_size:
            report = IngestionReport(original_filename=filename, detected_format="unknown")
            report.errors.append(f"Upload size ({len(data)} bytes) exceeds limit of {self.limits.max_upload_size} bytes")
            return report

        detection = detect_format(data, filename=filename)
        report = IngestionReport(
            original_filename=filename,
            detected_format=detection.description,
        )

        # 1. Plain Text / Uncompressed Log File
        if detection.format == FileFormat.TEXT:
            try:
                text = data.decode("utf-8", errors="replace")
                item = IngestionItem(
                    original_path=filename,
                    relative_path=filename,
                    size_bytes=len(data),
                    is_text=True,
                    status="processed",
                    extracted_text=text,
                )
                report.total_entries = 1
                report.processed_count = 1
                report.items.append(item)
                report.combined_log_text = text
                return report
            except Exception as exc:
                report.errors.append(f"Failed decoding text: {exc}")
                return report

        # 2. Standalone Compressed Streams (.gz, .bz2, .xz without tar)
        if detection.format in (FileFormat.GZIP, FileFormat.BZIP2, FileFormat.XZ) and not (
            ".tar" in filename.lower() or filename.lower().endswith((".tgz", ".tbz2", ".txz"))
        ):
            return self._ingest_single_compressed_stream(data, filename, detection.format, report)

        # 3. Archive Processing (ZIP or TAR)
        temp_dir = tempfile.mkdtemp(prefix="gtac_ingest_")
        try:
            target_path = Path(temp_dir)
            if detection.format == FileFormat.ZIP:
                self._extract_zip(data, target_path, password, report)
            elif detection.format in (FileFormat.TAR, FileFormat.GZIP, FileFormat.BZIP2, FileFormat.XZ):
                self._extract_tar(data, target_path, detection.format, report)
            else:
                report.errors.append(f"Unsupported file format: {detection.description}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return report

    def _ingest_single_compressed_stream(
        self,
        data: bytes,
        filename: str,
        fmt: FileFormat,
        report: IngestionReport,
    ) -> IngestionReport:
        try:
            if fmt == FileFormat.GZIP:
                decompressed = gzip.decompress(data)
            elif fmt == FileFormat.BZIP2:
                decompressed = bz2.decompress(data)
            elif fmt == FileFormat.XZ:
                decompressed = lzma.decompress(data)
            else:
                decompressed = data

            if len(decompressed) > self.limits.max_single_file_size:
                report.errors.append(f"Decompressed stream exceeds maximum size ({len(decompressed)} > {self.limits.max_single_file_size})")
                return report

            text = decompressed.decode("utf-8", errors="replace")
            clean_name = filename.rsplit(".", 1)[0]
            item = IngestionItem(
                original_path=filename,
                relative_path=clean_name,
                size_bytes=len(decompressed),
                is_text=True,
                status="processed",
                extracted_text=text,
            )
            report.total_entries = 1
            report.processed_count = 1
            report.items.append(item)
            report.combined_log_text = text
        except Exception as exc:
            report.errors.append(f"Decompression failed: {exc}")
        return report

    def _extract_zip(
        self,
        data: bytes,
        target_dir: Path,
        password: str | None,
        report: IngestionReport,
    ) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                # 1. Security Check
                validate_zip_security(zf, self.limits)

                # 2. Encryption Inspection
                enc_status = inspect_zip_encryption(zf)
                report.is_encrypted = enc_status.is_encrypted
                report.encryption_type = enc_status.encryption_algorithm

                if enc_status.is_encrypted:
                    if not password:
                        report.requires_password = True
                        report.errors.append("Archive contains encrypted entries. Password required.")
                        for entry in enc_status.encrypted_entries:
                            report.items.append(IngestionItem(
                                original_path=entry,
                                relative_path=entry,
                                size_bytes=0,
                                is_text=False,
                                status="encrypted",
                                reason="Password required",
                            ))
                        return

                    if not verify_zip_password(zf, password):
                        report.requires_password = True
                        report.errors.append("Incorrect password for encrypted archive.")
                        return

                pwd_bytes = password.encode("utf-8") if password else None
                combined_texts: list[str] = []

                for info in zf.infolist():
                    if info.is_dir():
                        continue

                    report.total_entries += 1
                    dest = sanitize_relative_path(info.filename, target_dir)
                    dest.parent.mkdir(parents=True, exist_ok=True)

                    # Extract file content
                    try:
                        content = zf.read(info, pwd=pwd_bytes)
                        # Check text readability (reject null bytes / binary streams)
                        text = None
                        if b"\x00" in content:
                            is_text = False
                        else:
                            try:
                                text = content.decode("utf-8")
                                is_text = True
                            except UnicodeDecodeError:
                                try:
                                    text = content.decode("latin-1")
                                    is_text = True
                                except Exception:
                                    is_text = False

                        if is_text and text:
                            report.processed_count += 1
                            item = IngestionItem(
                                original_path=info.filename,
                                relative_path=info.filename,
                                size_bytes=len(content),
                                is_text=True,
                                status="processed",
                                extracted_text=text,
                            )
                            combined_texts.append(f"=== File: {info.filename} ===\n{text}\n")
                        else:
                            report.skipped_count += 1
                            item = IngestionItem(
                                original_path=info.filename,
                                relative_path=info.filename,
                                size_bytes=len(content),
                                is_text=False,
                                status="skipped",
                                reason="Binary or non-text format",
                            )
                        report.items.append(item)
                    except Exception as exc:
                        report.skipped_count += 1
                        report.items.append(IngestionItem(
                            original_path=info.filename,
                            relative_path=info.filename,
                            size_bytes=0,
                            is_text=False,
                            status="error",
                            reason=str(exc),
                        ))

                report.combined_log_text = "\n".join(combined_texts)
        except SecurityViolation as exc:
            report.errors.append(f"Security violation: {exc}")
        except zipfile.BadZipFile as exc:
            report.errors.append(f"Corrupt or malformed ZIP archive: {exc}")

    def _extract_tar(
        self,
        data: bytes,
        target_dir: Path,
        fmt: FileFormat,
        report: IngestionReport,
    ) -> None:
        mode = "r:*"
        if fmt == FileFormat.GZIP:
            mode = "r:gz"
        elif fmt == FileFormat.BZIP2:
            mode = "r:bz2"
        elif fmt == FileFormat.XZ:
            mode = "r:xz"

        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as tf:
                validate_tar_security(tf, self.limits)
                combined_texts: list[str] = []

                for member in tf.getmembers():
                    if member.isdir():
                        continue

                    report.total_entries += 1
                    dest = sanitize_relative_path(member.name, target_dir)
                    dest.parent.mkdir(parents=True, exist_ok=True)

                    f = tf.extractfile(member)
                    if f is None:
                        report.skipped_count += 1
                        report.items.append(IngestionItem(
                            original_path=member.name,
                            relative_path=member.name,
                            size_bytes=member.size,
                            is_text=False,
                            status="skipped",
                            reason="Could not read member",
                        ))
                        continue

                    content = f.read()
                    text = None
                    if b"\x00" in content:
                        is_text = False
                    else:
                        try:
                            text = content.decode("utf-8")
                            is_text = True
                        except UnicodeDecodeError:
                            try:
                                text = content.decode("latin-1")
                                is_text = True
                            except Exception:
                                is_text = False

                    if is_text and text:
                        report.processed_count += 1
                        item = IngestionItem(
                            original_path=member.name,
                            relative_path=member.name,
                            size_bytes=len(content),
                            is_text=True,
                            status="processed",
                            extracted_text=text,
                        )
                        combined_texts.append(f"=== File: {member.name} ===\n{text}\n")
                    else:
                        report.skipped_count += 1
                        item = IngestionItem(
                            original_path=member.name,
                            relative_path=member.name,
                            size_bytes=len(content),
                            is_text=False,
                            status="skipped",
                            reason="Binary or non-text format",
                        )
                    report.items.append(item)

                report.combined_log_text = "\n".join(combined_texts)
        except SecurityViolation as exc:
            report.errors.append(f"Security violation: {exc}")
        except tarfile.TarError as exc:
            report.errors.append(f"Corrupt or malformed TAR archive: {exc}")
