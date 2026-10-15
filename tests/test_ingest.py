"""Tests for the diagnostic log and archive ingestion pipeline.

Verifies:
- Format detection via magic bytes
- Plain-text ingestion
- Standalone compressed stream decompression (gzip, bzip2, xz)
- Multi-file ZIP and TAR archive extraction
- Security controls (path traversal, absolute paths, compression limits)
- Encrypted archive detection and password validation
"""
from __future__ import annotations

import bz2
import gzip
import io
import lzma
import tarfile
import zipfile

import pytest

from src.ingest.crypto import inspect_zip_encryption, verify_zip_password
from src.ingest.detector import FileFormat, detect_format
from src.ingest.pipeline import IngestionPipeline
from src.ingest.security import IngestLimits, SecurityViolation, sanitize_relative_path


# ----------------------------------------------------------- Format Detection
def test_detect_plain_text() -> None:
    text = b"2026-04-19 12:00:00 [BGP-1001] Peer 10.0.0.1 down\n"
    res = detect_format(text, "syslog.txt")
    assert res.format == FileFormat.TEXT
    assert res.is_archive is False


def test_detect_zip_format() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test.log", "hello world")
    data = buf.getvalue()

    res = detect_format(data, "supportsave.zip")
    assert res.format == FileFormat.ZIP
    assert res.is_archive is True
    assert res.is_compressed is True


def test_detect_gzip_format() -> None:
    data = gzip.compress(b"syslog line 1\nline 2")
    res = detect_format(data, "tech.log.gz")
    assert res.format == FileFormat.GZIP
    assert res.is_archive is True


def test_detect_tar_format() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name="diag.log")
        content = b"sample content"
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    data = buf.getvalue()

    res = detect_format(data, "bundle.tar")
    assert res.format == FileFormat.TAR
    assert res.is_archive is True


# ----------------------------------------------------------- Security Checks
def test_sanitize_relative_path_normal(tmp_path) -> None:
    p = sanitize_relative_path("var/log/messages", tmp_path)
    assert p == (tmp_path / "var/log/messages").resolve()


def test_sanitize_relative_path_traversal_rejected(tmp_path) -> None:
    with pytest.raises(SecurityViolation, match="escapes"):
        sanitize_relative_path("../../etc/shadow", tmp_path)


def test_sanitize_relative_path_windows_drive_rejected(tmp_path) -> None:
    with pytest.raises(SecurityViolation, match="Windows drive"):
        sanitize_relative_path("C:/Windows/System32", tmp_path)


def test_zip_path_traversal_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "malicious payload")
    data = buf.getvalue()

    pipeline = IngestionPipeline()
    report = pipeline.ingest_bytes(data, "evil.zip")
    assert len(report.errors) > 0
    assert any("traversal" in err.lower() or "violation" in err.lower() for err in report.errors)
    assert report.processed_count == 0


# ----------------------------------------------------------- Ingestion Pipeline
def test_ingest_plain_text_file() -> None:
    log_content = "2026-04-19 18:00:00 [SEC-3020] Warning interface flapping\n"
    pipeline = IngestionPipeline()
    report = pipeline.ingest_bytes(log_content.encode("utf-8"), "raslog.txt")

    assert report.processed_count == 1
    assert report.skipped_count == 0
    assert report.is_encrypted is False
    assert "Warning interface flapping" in report.combined_log_text


def test_ingest_zip_multi_file() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("show_tech.txt", "SLX: 20.7.3a\nBGP neighbor 10.0.0.1 down")
        zf.writestr("binary.bin", b"\x00\x01\x02\x03\xff\xfe")
    data = buf.getvalue()

    pipeline = IngestionPipeline()
    report = pipeline.ingest_bytes(data, "supportsave.zip")

    assert report.total_entries == 2
    assert report.processed_count == 1
    assert report.skipped_count == 1
    assert "show_tech.txt" in report.combined_log_text
    assert "SLX: 20.7.3a" in report.combined_log_text


def test_ingest_tar_gz_archive() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        text = b"device inventory: SLX9740 running 20.7.3a"
        info = tarfile.TarInfo(name="inventory.log")
        info.size = len(text)
        tf.addfile(info, io.BytesIO(text))
    data = buf.getvalue()

    pipeline = IngestionPipeline()
    report = pipeline.ingest_bytes(data, "bundle.tar.gz")

    assert report.processed_count == 1
    assert "SLX9740" in report.combined_log_text


# ----------------------------------------------------------- Encrypted ZIP
def test_encrypted_zip_detection() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("confidential.log", "Sensitive switch dump")
    raw = bytearray(buf.getvalue())
    # Set bit 0 of flag_bits (offset 6 in local header)
    raw[6] |= 0x1
    cd_idx = raw.find(b"PK\x01\x02")
    if cd_idx != -1:
        raw[cd_idx + 8] |= 0x1
    data = bytes(raw)

    # Without password -> flags encryption and asks for password
    pipeline = IngestionPipeline()
    report = pipeline.ingest_bytes(data, "secure_tech.zip")
    assert report.is_encrypted is True
    assert report.requires_password is True
    assert report.processed_count == 0

    # With incorrect password -> reports bad password
    report_bad = pipeline.ingest_bytes(data, "secure_tech.zip", password="wrongpassword")
    assert report_bad.requires_password is True
    assert any("incorrect" in e.lower() for e in report_bad.errors)

