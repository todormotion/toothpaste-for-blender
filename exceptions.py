"""
Custom exceptions for ToothPaste addon.
"""


class ToothPasteError(Exception):
    """Base exception for all ToothPaste errors."""
    pass


class InvalidSVGError(ToothPasteError):
    """Raised when SVG data is invalid or malformed."""
    pass


class ClipboardError(ToothPasteError):
    """Raised when clipboard access fails or contains invalid data."""
    pass


class ImportContentError(ToothPasteError):
    """Raised when an SVG parses correctly but holds nothing ToothPaste can import."""
    pass
