"""P2-P4 knowledge ingestion and retrieval building blocks.

The package deliberately has no import-time dependency on Redis, MinIO, Milvus,
pypdf, or python-docx. Production adapters load their SDK only when constructed,
while L1 tests use deterministic fixture providers and in-memory stores.
"""

from app.knowledge.chunking import ParentChildChunker
from app.knowledge.parsers import DocumentParser
from app.knowledge.providers import FixtureEmbeddingProvider, ProviderMode
from app.knowledge.retrieval import HybridRetriever

__all__ = [
    "DocumentParser",
    "FixtureEmbeddingProvider",
    "HybridRetriever",
    "ParentChildChunker",
    "ProviderMode",
]
