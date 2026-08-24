"""Typed errors exposed by knowledge components."""


class KnowledgeError(Exception):
    """Base error safe for service-layer translation."""


class UnsafeDocumentError(KnowledgeError):
    """The uploaded document violates an ingestion safety rule."""


class UnsupportedDocumentError(KnowledgeError):
    """The document format or an optional parser dependency is unavailable."""


class ProviderUnavailableError(KnowledgeError):
    """A configured provider cannot serve the requested capability."""


class OCRProviderUnavailableError(ProviderUnavailableError):
    """The configured OCR engine or its inference runtime is temporarily unavailable."""


class AdapterUnavailableError(KnowledgeError):
    """An external storage adapter cannot be constructed or reached."""
