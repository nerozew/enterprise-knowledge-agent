"""Recoverable storage adapter contracts, fakes, and lazy production SDK wrappers."""

from __future__ import annotations

import asyncio
import importlib
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from app.knowledge.errors import AdapterUnavailableError


class ObjectStore(Protocol):
    async def put(self, bucket: str, key: str, content: bytes, content_type: str) -> None: ...

    async def get(self, bucket: str, key: str) -> bytes: ...

    async def delete(self, bucket: str, key: str) -> None: ...

    async def health(self) -> bool: ...

    async def close(self) -> None: ...


class CacheStore(Protocol):
    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, ttl_seconds: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def health(self) -> bool: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class VectorRecord:
    chunk_id: str
    workspace_id: str
    document_id: str
    document_version_id: str
    parent_id: str | None
    text: str
    dense_vector: tuple[float, ...]
    active: bool
    source_format: str = ""
    page: int | None = None
    created_at_ms: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)


class VectorStore(Protocol):
    async def ensure_schema(self, profile_hash: str, dimensions: int) -> str: ...

    async def upsert(self, profile_hash: str, records: Sequence[VectorRecord]) -> None: ...

    async def search(
        self,
        profile_hash: str,
        *,
        workspace_id: str,
        query_vector: Sequence[float],
        query_text: str,
        limit: int,
        retrieval_mode: Literal["DENSE", "SPARSE", "HYBRID"] = "HYBRID",
        fusion: Literal["RRF", "WEIGHTED"] = "RRF",
        dense_weight: float = 0.65,
        sparse_weight: float = 0.35,
    ) -> list[tuple[str, float]]: ...

    async def activate_version(
        self, profile_hash: str, workspace_id: str, document_version_id: str
    ) -> None: ...

    async def deactivate_version(
        self, profile_hash: str, workspace_id: str, document_version_id: str
    ) -> None: ...

    async def health(self) -> bool: ...

    async def close(self) -> None: ...


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.closed = False

    async def put(self, bucket: str, key: str, content: bytes, content_type: str) -> None:
        self._assert_open()
        self.objects[(bucket, key)] = (bytes(content), content_type)

    async def get(self, bucket: str, key: str) -> bytes:
        self._assert_open()
        return self.objects[(bucket, key)][0]

    async def delete(self, bucket: str, key: str) -> None:
        self._assert_open()
        self.objects.pop((bucket, key), None)

    async def health(self) -> bool:
        return not self.closed

    async def close(self) -> None:
        self.closed = True

    def _assert_open(self) -> None:
        if self.closed:
            raise AdapterUnavailableError("fake object store is closed")


class FakeCacheStore:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.closed = False

    async def get(self, key: str) -> bytes | None:
        self._assert_open()
        return self.values.get(key)

    async def set(self, key: str, value: bytes, ttl_seconds: int) -> None:
        self._assert_open()
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.values[key] = bytes(value)

    async def delete(self, key: str) -> None:
        self._assert_open()
        self.values.pop(key, None)

    async def health(self) -> bool:
        return not self.closed

    async def close(self) -> None:
        self.closed = True

    def _assert_open(self) -> None:
        if self.closed:
            raise AdapterUnavailableError("fake cache is closed")


class FakeVectorStore:
    """Workspace-filtered L1 store; the fake never evaluates raw filter strings."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, VectorRecord]] = {}
        self.dimensions: dict[str, int] = {}
        self.closed = False

    async def ensure_schema(self, profile_hash: str, dimensions: int) -> str:
        self._assert_open()
        name = collection_name(profile_hash)
        self.collections.setdefault(name, {})
        self.dimensions[name] = dimensions
        return name

    async def upsert(self, profile_hash: str, records: Sequence[VectorRecord]) -> None:
        self._assert_open()
        name = collection_name(profile_hash)
        if name not in self.collections:
            raise AdapterUnavailableError("vector schema has not been initialized")
        for record in records:
            if len(record.dense_vector) != self.dimensions[name]:
                raise ValueError("vector dimension does not match the collection")
            self.collections[name][record.chunk_id] = VectorRecord(
                chunk_id=record.chunk_id,
                workspace_id=record.workspace_id,
                document_id=record.document_id,
                document_version_id=record.document_version_id,
                parent_id=record.parent_id,
                text=record.text,
                dense_vector=record.dense_vector,
                active=False,
                source_format=record.source_format,
                page=record.page,
                created_at_ms=record.created_at_ms,
                metadata=record.metadata,
            )

    async def search(
        self,
        profile_hash: str,
        *,
        workspace_id: str,
        query_vector: Sequence[float],
        query_text: str,
        limit: int,
        retrieval_mode: Literal["DENSE", "SPARSE", "HYBRID"] = "HYBRID",
        fusion: Literal["RRF", "WEIGHTED"] = "RRF",
        dense_weight: float = 0.65,
        sparse_weight: float = 0.35,
    ) -> list[tuple[str, float]]:
        from app.knowledge.providers import cosine_similarity, lexical_features

        self._assert_open()
        name = collection_name(profile_hash)
        records = self.collections.get(name, {})
        dense_scores = [
            (record.chunk_id, cosine_similarity(query_vector, record.dense_vector))
            for record in records.values()
            if record.workspace_id == workspace_id and record.active
        ]
        query_terms = set(lexical_features(query_text))
        sparse_scores = [
            (
                record.chunk_id,
                float(len(query_terms.intersection(lexical_features(record.text)))),
            )
            for record in records.values()
            if record.workspace_id == workspace_id and record.active
        ]
        if retrieval_mode == "DENSE":
            scored = dense_scores
        elif retrieval_mode == "SPARSE":
            scored = sparse_scores
        else:
            dense_rank = {
                chunk_id: rank
                for rank, (chunk_id, _) in enumerate(
                    sorted(dense_scores, key=lambda pair: (-pair[1], pair[0])), start=1
                )
            }
            sparse_rank = {
                chunk_id: rank
                for rank, (chunk_id, _) in enumerate(
                    sorted(sparse_scores, key=lambda pair: (-pair[1], pair[0])), start=1
                )
            }
            dense_by_id = dict(dense_scores)
            sparse_by_id = dict(sparse_scores)
            chunk_ids = dense_by_id.keys() | sparse_by_id.keys()
            if fusion == "WEIGHTED":
                scored = [
                    (
                        chunk_id,
                        dense_weight * dense_by_id.get(chunk_id, 0.0)
                        + sparse_weight * sparse_by_id.get(chunk_id, 0.0),
                    )
                    for chunk_id in chunk_ids
                ]
            else:
                scored = [
                    (
                        chunk_id,
                        1 / (60 + dense_rank.get(chunk_id, 10_000))
                        + 1 / (60 + sparse_rank.get(chunk_id, 10_000)),
                    )
                    for chunk_id in chunk_ids
                ]
        return sorted(scored, key=lambda pair: (-pair[1], pair[0]))[:limit]

    async def deactivate_version(
        self, profile_hash: str, workspace_id: str, document_version_id: str
    ) -> None:
        await self._set_version_active(
            profile_hash, workspace_id, document_version_id, active=False
        )

    async def activate_version(
        self, profile_hash: str, workspace_id: str, document_version_id: str
    ) -> None:
        await self._set_version_active(profile_hash, workspace_id, document_version_id, active=True)

    async def _set_version_active(
        self,
        profile_hash: str,
        workspace_id: str,
        document_version_id: str,
        *,
        active: bool,
    ) -> None:
        self._assert_open()
        name = collection_name(profile_hash)
        for chunk_id, record in list(self.collections.get(name, {}).items()):
            if (
                record.workspace_id == workspace_id
                and record.document_version_id == document_version_id
            ):
                self.collections[name][chunk_id] = VectorRecord(
                    chunk_id=record.chunk_id,
                    workspace_id=record.workspace_id,
                    document_id=record.document_id,
                    document_version_id=record.document_version_id,
                    parent_id=record.parent_id,
                    text=record.text,
                    dense_vector=record.dense_vector,
                    active=active,
                    source_format=record.source_format,
                    page=record.page,
                    created_at_ms=record.created_at_ms,
                    metadata=record.metadata,
                )

    async def health(self) -> bool:
        return not self.closed

    async def close(self) -> None:
        self.closed = True

    def _assert_open(self) -> None:
        if self.closed:
            raise AdapterUnavailableError("fake vector store is closed")


class RedisCacheStore:
    """redis.asyncio wrapper with one pooled client and explicit shutdown."""

    def __init__(self, url: str, *, socket_timeout: float = 2.0) -> None:
        try:
            redis_module = importlib.import_module("redis.asyncio")
        except ImportError as exc:
            raise AdapterUnavailableError("Redis adapter requires the 'redis' package") from exc
        self._client = redis_module.from_url(
            url,
            decode_responses=False,
            socket_connect_timeout=socket_timeout,
            socket_timeout=socket_timeout,
        )

    async def get(self, key: str) -> bytes | None:
        return cast(bytes | None, await self._client.get(key))

    async def set(self, key: str, value: bytes, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        await self._client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def health(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()


class MinioObjectStore:
    """Private-bucket MinIO wrapper. The SDK is synchronous, so calls use threads."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        *,
        secure: bool = True,
        versioned_buckets: set[str] | None = None,
    ) -> None:
        try:
            minio_module = importlib.import_module("minio")
        except ImportError as exc:
            raise AdapterUnavailableError("MinIO adapter requires the 'minio' package") from exc
        self._client = minio_module.Minio(
            endpoint, access_key=access_key, secret_key=secret_key, secure=secure
        )
        self._versioned_buckets = versioned_buckets or {"knowledge-source"}
        self._versioning = importlib.import_module("minio.versioningconfig")

    async def put(self, bucket: str, key: str, content: bytes, content_type: str) -> None:
        await asyncio.to_thread(self._ensure_private_bucket, bucket)
        await asyncio.to_thread(
            self._client.put_object,
            bucket,
            key,
            io.BytesIO(content),
            len(content),
            content_type=content_type,
        )

    async def get(self, bucket: str, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, bucket, key)
        try:
            return cast(bytes, await asyncio.to_thread(response.read))
        finally:
            await asyncio.to_thread(response.close)
            await asyncio.to_thread(response.release_conn)

    async def delete(self, bucket: str, key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, bucket, key)

    async def health(self) -> bool:
        try:
            await asyncio.to_thread(lambda: list(self._client.list_buckets()))
            return True
        except Exception:
            return False

    async def close(self) -> None:
        return None

    def _ensure_private_bucket(self, bucket: str) -> None:
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)
        else:
            try:
                self._client.delete_bucket_policy(bucket)
            except Exception as exc:
                if getattr(exc, "code", None) != "NoSuchBucketPolicy":
                    raise
        if bucket in self._versioned_buckets:
            self._client.set_bucket_versioning(
                bucket,
                self._versioning.VersioningConfig(self._versioning.ENABLED),
            )


class MilvusVectorStore:
    """PyMilvus 2.6 async client using server-side BM25 and typed filters only."""

    def __init__(self, uri: str, *, token: str | None = None, timeout: float = 5.0) -> None:
        try:
            pymilvus = importlib.import_module("pymilvus")
        except ImportError as exc:
            raise AdapterUnavailableError(
                "Milvus adapter requires the 'pymilvus>=2.6,<2.7' package"
            ) from exc
        self._pymilvus = pymilvus
        kwargs: dict[str, Any] = {"uri": uri, "timeout": timeout}
        if token:
            kwargs["token"] = token
        self._client = pymilvus.AsyncMilvusClient(**kwargs)

    async def ensure_schema(self, profile_hash: str, dimensions: int) -> str:
        name = collection_name(profile_hash)
        if await self._client.has_collection(collection_name=name):
            return name
        DataType = self._pymilvus.DataType
        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("workspace_id", DataType.VARCHAR, max_length=64)
        schema.add_field("document_id", DataType.VARCHAR, max_length=64)
        schema.add_field("document_version_id", DataType.VARCHAR, max_length=64)
        schema.add_field("parent_id", DataType.VARCHAR, max_length=64, nullable=True)
        schema.add_field(
            "text",
            DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            enable_match=True,
            analyzer_params={"tokenizer": "jieba"},
        )
        schema.add_field("source_format", DataType.VARCHAR, max_length=16)
        schema.add_field("page", DataType.INT64, nullable=True)
        schema.add_field("created_at_ms", DataType.INT64)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=dimensions)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("active", DataType.BOOL)
        schema.add_function(
            self._pymilvus.Function(
                name="bm25",
                function_type=self._pymilvus.FunctionType.BM25,
                input_field_names=["text"],
                output_field_names=["sparse_vector"],
            )
        )
        index = self._client.prepare_index_params()
        index.add_index(
            field_name="dense_vector",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 128},
        )
        index.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )
        await self._client.create_collection(
            collection_name=name, schema=schema, index_params=index
        )
        return name

    async def upsert(self, profile_hash: str, records: Sequence[VectorRecord]) -> None:
        name = collection_name(profile_hash)
        data = [
            {
                "chunk_id": record.chunk_id,
                "workspace_id": record.workspace_id,
                "document_id": record.document_id,
                "document_version_id": record.document_version_id,
                "parent_id": record.parent_id,
                "text": record.text,
                "source_format": record.source_format,
                "page": record.page,
                "created_at_ms": record.created_at_ms,
                "dense_vector": list(record.dense_vector),
                "active": False,
            }
            for record in records
        ]
        if data:
            await self._client.upsert(collection_name=name, data=data)

    async def search(
        self,
        profile_hash: str,
        *,
        workspace_id: str,
        query_vector: Sequence[float],
        query_text: str,
        limit: int,
        retrieval_mode: Literal["DENSE", "SPARSE", "HYBRID"] = "HYBRID",
        fusion: Literal["RRF", "WEIGHTED"] = "RRF",
        dense_weight: float = 0.65,
        sparse_weight: float = 0.35,
    ) -> list[tuple[str, float]]:
        name = collection_name(profile_hash)
        safe_workspace = _milvus_literal(workspace_id)
        expression = f'workspace_id == "{safe_workspace}" and active == true'
        if retrieval_mode == "DENSE":
            results = await self._client.search(
                collection_name=name,
                data=[list(query_vector)],
                anns_field="dense_vector",
                search_params={
                    "metric_type": "COSINE",
                    "params": {"ef": max(limit, 64)},
                },
                filter=expression,
                limit=limit,
                output_fields=["chunk_id"],
            )
            return [(str(hit["id"]), float(hit["distance"])) for hit in results[0]]
        if retrieval_mode == "SPARSE":
            results = await self._client.search(
                collection_name=name,
                data=[query_text],
                anns_field="sparse_vector",
                search_params={"metric_type": "BM25", "params": {}},
                filter=expression,
                limit=limit,
                output_fields=["chunk_id"],
            )
            return [(str(hit["id"]), float(hit["distance"])) for hit in results[0]]
        dense_request = self._pymilvus.AnnSearchRequest(
            data=[list(query_vector)],
            anns_field="dense_vector",
            param={"metric_type": "COSINE", "params": {"ef": max(limit, 64)}},
            limit=limit,
            expr=expression,
        )
        sparse_request = self._pymilvus.AnnSearchRequest(
            data=[query_text],
            anns_field="sparse_vector",
            param={"metric_type": "BM25", "params": {}},
            limit=limit,
            expr=expression,
        )
        ranker = (
            self._pymilvus.RRFRanker(k=60)
            if fusion == "RRF"
            else self._pymilvus.WeightedRanker(dense_weight, sparse_weight)
        )
        results = await self._client.hybrid_search(
            collection_name=name,
            reqs=[dense_request, sparse_request],
            ranker=ranker,
            limit=limit,
            output_fields=["chunk_id"],
        )
        return [(str(hit["id"]), float(hit["distance"])) for hit in results[0]]

    async def deactivate_version(
        self, profile_hash: str, workspace_id: str, document_version_id: str
    ) -> None:
        await self._set_version_active(
            profile_hash, workspace_id, document_version_id, active=False
        )

    async def activate_version(
        self, profile_hash: str, workspace_id: str, document_version_id: str
    ) -> None:
        await self._set_version_active(profile_hash, workspace_id, document_version_id, active=True)

    async def _set_version_active(
        self,
        profile_hash: str,
        workspace_id: str,
        document_version_id: str,
        *,
        active: bool,
    ) -> None:
        name = collection_name(profile_hash)
        workspace = _milvus_literal(workspace_id)
        version = _milvus_literal(document_version_id)
        expression = f'workspace_id == "{workspace}" and document_version_id == "{version}"'
        rows = await self._client.query(
            collection_name=name,
            filter=expression,
            output_fields=[
                "chunk_id",
                "workspace_id",
                "document_id",
                "document_version_id",
                "parent_id",
                "text",
                "source_format",
                "page",
                "created_at_ms",
                "dense_vector",
            ],
        )
        for row in rows:
            row["active"] = active
        if rows:
            await self._client.upsert(collection_name=name, data=rows)

    async def health(self) -> bool:
        try:
            await self._client.list_collections()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.close()


def collection_name(profile_hash: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_]{1,48}", profile_hash):
        raise ValueError("profile_hash must contain only letters, digits, or underscore")
    return f"knowledge_chunks__{profile_hash.lower()}"


def _milvus_literal(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise ValueError("identifier is not safe for a typed Milvus filter")
    return value
