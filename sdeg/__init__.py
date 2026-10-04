"""A fictional, in-memory Sensitive Data Egress Gate reference model."""

from .gate import Gate, ManualClock, ReleaseClass, Status

__all__ = ["Gate", "ManualClock", "ReleaseClass", "Status"]
