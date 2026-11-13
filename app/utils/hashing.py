"""
Cryptographic hashing utilities.
"""
import hashlib
from pathlib import Path


def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA-256 hash of a file on disk."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def calculate_bytes_hash(data: bytes) -> str:
    """Calculate SHA-256 hash of raw bytes."""
    return hashlib.sha256(data).hexdigest()
