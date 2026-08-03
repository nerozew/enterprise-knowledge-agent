"""Recoverable storage adapter contracts, fakes, and lazy production SDK wrappers."""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
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
        self._timeout = timeout
        self._client = pymilvus.AsyncMilvusClient(**kwargs)
        # AsyncMilvusClient 2.6 has no public query_iterator. Version publication is
        # off the search hot path, so use the public synchronous iterator in a worker
        # thread to avoid Milvus' finite query window while keeping the event loop free.
        self._sync_client_factory = lambda: pymilvus.MilvusClient(**kwargs)

    async def ensure_schema(self, profile_hash: str, dimensions: int) -> str:
        name = collection_name(profile_hash)
        if await self._client.has_collection(collection_name=name, timeout=self._timeout):
            description = await self._client.describe_collection(
                collection_name=name, timeout=self._timeout
            )
            _validate_milvus_schema(description, self._pymilvus, dimensions, name)
            await self._validate_milvus_indexes(name)
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
            collection_name=name,
            schema=schema,
            index_params=index,
            timeout=self._timeout,
        )
        return name

    async def _validate_milvus_indexes(self, collection: str) -> None:
        expected = {
            "dense_vector": ("HNSW", "COSINE"),
            "sparse_vector": ("SPARSE_INVERTED_INDEX", "BM25"),
        }
        for field_name, (index_type, metric_type) in expected.items():
            index_names = await self._client.list_indexes(
                collection_name=collection,
                field_name=field_name,
                timeout=self._timeout,
            )
            descriptions = [
                await self._client.describe_index(
                    collection_name=collection,
                    index_name=index_name,
                    timeout=self._timeout,
                )
                for index_name in index_names
            ]
            if not any(
                _milvus_index_matches(
                    description,
                    field_name=field_name,
                    index_type=index_type,
                    metric_type=metric_type,
                )
                for description in descriptions
            ):
                raise AdapterUnavailableError(
                    f"Milvus collection '{collection}' field '{field_name}' "
                    f"index mismatch: expected {index_type}/{metric_type}"
                )

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
            await self._client.upsert(
                collection_name=name, data=data, timeout=self._timeout
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
                timeout=self._timeout,
            )
            return [_milvus_search_hit(hit) for hit in results[0]]
        if retrieval_mode == "SPARSE":
            results = await self._client.search(
                collection_name=name,
                data=[query_text],
                anns_field="sparse_vector",
                search_params={"metric_type": "BM25", "params": {}},
                filter=expression,
                limit=limit,
                output_fields=["chunk_id"],
                timeout=self._timeout,
            )
            return [_milvus_search_hit(hit) for hit in results[0]]
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
            timeout=self._timeout,
        )
        return [_milvus_search_hit(hit) for hit in results[0]]

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
        thread_task = asyncio.create_task(
            asyncio.to_thread(
                self._set_version_active_sync,
                name,
                expression,
                workspace_id,
                document_version_id,
                active,
            )
        )
        try:
            await asyncio.shield(thread_task)
        except asyncio.CancelledError:
            # asyncio.to_thread cannot stop a running worker. Keep the coroutine alive
            # until the bounded Milvus RPCs finish and the client closes, so a cancelled
            # request cannot leave a late version-state write drifting in the background.
            while not thread_task.done():
                try:
                    await asyncio.shield(thread_task)
                except asyncio.CancelledError:
                    continue
            with suppress(Exception):
                thread_task.result()
            raise

    def _set_version_active_sync(
        self,
        collection: str,
        expression: str,
        workspace_id: str,
        document_version_id: str,
        active: bool,
    ) -> None:
        client = self._sync_client_factory()
        output_fields = [
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
        ]
        expected_ids: set[str] = set()
        mutated_count = 0
        try:
            iterator = client.query_iterator(
                collection_name=collection,
                batch_size=1000,
                limit=-1,
                filter=expression,
                output_fields=output_fields,
                consistency_level="Strong",
                timeout=self._timeout,
            )
            try:
                while rows := iterator.next():
                    for row in rows:
                        chunk_id = _milvus_row_chunk_id(row)
                        if (
                            row.get("workspace_id") != workspace_id
                            or row.get("document_version_id") != document_version_id
                        ):
                            raise AdapterUnavailableError(
                                "Milvus version mutation returned a row outside its "
                                "workspace/version scope"
                            )
                        if chunk_id in expected_ids:
                            raise AdapterUnavailableError(
                                f"Milvus version mutation returned duplicate chunk '{chunk_id}'"
                            )
                        expected_ids.add(chunk_id)
                        row["active"] = active
                    client.upsert(
                        collection_name=collection,
                        data=rows,
                        timeout=self._timeout,
                    )
                    mutated_count += len(rows)
            finally:
                iterator.close()

            if active and not expected_ids:
                raise AdapterUnavailableError(
                    f"Milvus activation found no vectors for workspace '{workspace_id}' "
                    f"and document version '{document_version_id}'"
                )
            if not expected_ids:
                return

            # Exactly one segment-sealing boundary per version transition. The Strong
            # iterator above supplies read-after-write for staged rows; this flush makes
            # the completed active-state transition available to normal bounded searches.
            client.flush(collection_name=collection, timeout=self._timeout)
            visible_ids = _milvus_iter_chunk_ids(
                client,
                collection_name=collection,
                expression=f"{expression} and active == {_milvus_bool(active)}",
                timeout=self._timeout,
            )
            if visible_ids != expected_ids or mutated_count != len(expected_ids):
                missing = len(expected_ids - visible_ids)
                unexpected = len(visible_ids - expected_ids)
                raise AdapterUnavailableError(
                    f"Milvus active-state verification failed for document version "
                    f"'{document_version_id}': expected {len(expected_ids)}, "
                    f"found {len(visible_ids)}, missing {missing}, unexpected {unexpected}"
                )
        finally:
            client.close()

    async def health(self) -> bool:
        try:
            await self._client.list_collections(timeout=self._timeout)
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


def _validate_milvus_schema(
    description: object, pymilvus: object, dimensions: int, collection: str
) -> None:
    if not isinstance(description, Mapping):
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' returned an invalid schema description"
        )
    fields = description.get("fields")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' schema does not contain fields"
        )
    if description.get("auto_id", False) is not False:
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' must disable auto_id"
        )
    if description.get("enable_dynamic_field", False) is not False:
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' must disable dynamic fields"
    )
    by_name: dict[str, Mapping[Any, Any]] = {}
    for schema_field in fields:
        if not isinstance(schema_field, Mapping):
            continue
        field_name = schema_field.get("name")
        if isinstance(field_name, str):
            by_name[field_name] = schema_field
    expected_names = {
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
        "sparse_vector",
        "active",
    }
    if set(by_name) != expected_names:
        missing = sorted(expected_names - set(by_name))
        unexpected = sorted(set(by_name) - expected_names)
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' field set mismatch: "
            f"missing {missing}, unexpected {unexpected}"
        )
    DataType = cast(Any, getattr(pymilvus, "DataType", None))
    if DataType is None:
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' cannot be validated without DataType"
        )
    field_contract: dict[str, tuple[object, bool, bool, int | None]] = {
        "chunk_id": (DataType.VARCHAR, False, True, 64),
        "workspace_id": (DataType.VARCHAR, False, False, 64),
        "document_id": (DataType.VARCHAR, False, False, 64),
        "document_version_id": (DataType.VARCHAR, False, False, 64),
        "parent_id": (DataType.VARCHAR, True, False, 64),
        "text": (DataType.VARCHAR, False, False, 65535),
        "source_format": (DataType.VARCHAR, False, False, 16),
        "page": (DataType.INT64, True, False, None),
        "created_at_ms": (DataType.INT64, False, False, None),
        "dense_vector": (DataType.FLOAT_VECTOR, False, False, None),
        "sparse_vector": (DataType.SPARSE_FLOAT_VECTOR, False, False, None),
        "active": (DataType.BOOL, False, False, None),
    }
    for field_name, (field_type, nullable, primary, max_length) in field_contract.items():
        field = by_name[field_name]
        if field.get("type") != field_type:
            raise AdapterUnavailableError(
                f"Milvus collection '{collection}' field '{field_name}' type mismatch"
            )
        if bool(field.get("nullable", False)) is not nullable:
            raise AdapterUnavailableError(
                f"Milvus collection '{collection}' field '{field_name}' nullable mismatch"
            )
        if bool(field.get("is_primary", False)) is not primary:
            raise AdapterUnavailableError(
                f"Milvus collection '{collection}' field '{field_name}' primary-key mismatch"
            )
        if max_length is not None and _milvus_int_param(field, "max_length") != max_length:
            raise AdapterUnavailableError(
                f"Milvus collection '{collection}' field '{field_name}' max_length mismatch"
            )
    existing_dimensions = _milvus_int_param(by_name["dense_vector"], "dim")
    if existing_dimensions != dimensions:
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' dense_vector dimension mismatch: "
            f"configured {dimensions}, existing {existing_dimensions}; "
            "rebuild the collection for this index profile"
        )
    text_params = by_name["text"].get("params")
    if not isinstance(text_params, Mapping):
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' text analyzer configuration mismatch"
        )
    analyzer_params = text_params.get("analyzer_params")
    if isinstance(analyzer_params, str):
        try:
            analyzer_params = json.loads(analyzer_params)
        except json.JSONDecodeError:
            analyzer_params = None
    if (
        not _milvus_truthy(text_params.get("enable_analyzer"))
        or not _milvus_truthy(text_params.get("enable_match"))
        or not isinstance(analyzer_params, Mapping)
        or analyzer_params.get("tokenizer") != "jieba"
    ):
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' text analyzer configuration mismatch"
        )
    functions = description.get("functions")
    if not isinstance(functions, Sequence) or isinstance(functions, (str, bytes)):
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' schema has no BM25 function"
        )
    FunctionType = cast(Any, getattr(pymilvus, "FunctionType", None))
    expected_bm25_type = getattr(FunctionType, "BM25", None)
    valid_bm25 = any(
        isinstance(function, Mapping)
        and function.get("name") == "bm25"
        and function.get("type") == expected_bm25_type
        and list(function.get("input_field_names", [])) == ["text"]
        and list(function.get("output_field_names", [])) == ["sparse_vector"]
        for function in functions
    )
    if not valid_bm25:
        raise AdapterUnavailableError(
            f"Milvus collection '{collection}' BM25 function mismatch"
        )


def _milvus_int_param(field: Mapping[Any, Any], parameter: str) -> int:
    params = field.get("params")
    value = params.get(parameter) if isinstance(params, Mapping) else None
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise AdapterUnavailableError(
            f"Milvus field '{field.get('name')}' has no valid {parameter}"
        ) from exc
    if parsed <= 0:
        raise AdapterUnavailableError(
            f"Milvus field '{field.get('name')}' has no valid {parameter}"
        )
    return parsed


def _milvus_iter_chunk_ids(
    client: object, *, collection_name: str, expression: str, timeout: float
) -> set[str]:
    iterator = cast(Any, client).query_iterator(
        collection_name=collection_name,
        batch_size=1000,
        limit=-1,
        filter=expression,
        output_fields=["chunk_id"],
        consistency_level="Strong",
        timeout=timeout,
    )
    chunk_ids: set[str] = set()
    try:
        while rows := iterator.next():
            for row in rows:
                chunk_id = _milvus_row_chunk_id(row)
                if chunk_id in chunk_ids:
                    raise AdapterUnavailableError(
                        f"Milvus verification returned duplicate chunk '{chunk_id}'"
                    )
                chunk_ids.add(chunk_id)
    finally:
        iterator.close()
    return chunk_ids


def _milvus_bool(value: bool) -> str:
    return "true" if value else "false"


def _milvus_truthy(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.casefold() == "true")


def _milvus_index_matches(
    description: object,
    *,
    field_name: str,
    index_type: str,
    metric_type: str,
) -> bool:
    if not isinstance(description, Mapping):
        return False
    params = description.get("params")
    nested = params if isinstance(params, Mapping) else {}
    actual_index_type = description.get("index_type", nested.get("index_type"))
    actual_metric_type = description.get("metric_type", nested.get("metric_type"))
    return (
        description.get("field_name") == field_name
        and actual_index_type == index_type
        and actual_metric_type == metric_type
    )


def _milvus_search_hit(hit: object) -> tuple[str, float]:
    if not isinstance(hit, Mapping):
        raise AdapterUnavailableError("Milvus search returned an invalid hit")
    chunk_id = _milvus_mapping_value(hit, "chunk_id")
    distance = _milvus_mapping_value(hit, "distance")
    if not isinstance(chunk_id, str) or not chunk_id:
        raise AdapterUnavailableError(
            "Milvus search hit field 'chunk_id' must be a non-empty string"
        )
    if isinstance(distance, bool) or not isinstance(distance, (int, float)):
        raise AdapterUnavailableError("Milvus search hit field 'distance' must be numeric")
    return chunk_id, float(distance)


def _milvus_mapping_value(hit: Mapping[Any, Any], field: str) -> object:
    if field in hit:
        return hit[field]
    entity = hit.get("entity")
    if isinstance(entity, Mapping) and field in entity:
        return entity[field]
    raise AdapterUnavailableError(f"Milvus search hit is missing required field '{field}'")


def _milvus_row_chunk_id(row: object) -> str:
    if not isinstance(row, Mapping):
        raise AdapterUnavailableError("Milvus query returned an invalid row")
    value = row.get("chunk_id")
    if not isinstance(value, str) or not value:
        raise AdapterUnavailableError(
            "Milvus query row field 'chunk_id' must be a non-empty string"
        )
    return value
