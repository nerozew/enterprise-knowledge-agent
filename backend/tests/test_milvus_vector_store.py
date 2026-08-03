from __future__ import annotations

import asyncio
import threading
from copy import deepcopy
from typing import Any

import pytest

from app.knowledge.adapters import MilvusVectorStore, VectorRecord
from app.knowledge.errors import AdapterUnavailableError


class FakeDataType:
    VARCHAR = 21
    INT64 = 5
    FLOAT_VECTOR = 101
    SPARSE_FLOAT_VECTOR = 104
    BOOL = 1


class FakeFunctionType:
    BM25 = 1


class FakeMilvusModule:
    DataType = FakeDataType
    FunctionType = FakeFunctionType

    @staticmethod
    def AnnSearchRequest(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    @staticmethod
    def RRFRanker(*, k: int) -> tuple[str, int]:
        return ("RRF", k)

    @staticmethod
    def WeightedRanker(dense_weight: float, sparse_weight: float) -> tuple[str, float, float]:
        return ("WEIGHTED", dense_weight, sparse_weight)


def _store(*, async_client: object, sync_client: object | None = None) -> MilvusVectorStore:
    store = object.__new__(MilvusVectorStore)
    store._pymilvus = FakeMilvusModule()
    store._client = async_client
    store._timeout = 3.5
    if sync_client is not None:
        store._sync_client_factory = lambda: sync_client
    return store


@pytest.mark.asyncio
async def test_milvus_search_extracts_chunk_id_and_scopes_all_modes() -> None:
    class Client:
        def __init__(self) -> None:
            self.search_calls: list[dict[str, Any]] = []
            self.hybrid_calls: list[dict[str, Any]] = []

        async def search(self, **kwargs: Any) -> list[list[dict[str, Any]]]:
            self.search_calls.append(kwargs)
            chunk_id = f"{kwargs['anns_field']}-chunk"
            return [[{"distance": 0.9, "entity": {"chunk_id": chunk_id}}]]

        async def hybrid_search(self, **kwargs: Any) -> list[list[dict[str, Any]]]:
            self.hybrid_calls.append(kwargs)
            return [[{"distance": 0.8, "entity": {"chunk_id": "hybrid-chunk"}}]]

    client = Client()
    store = _store(async_client=client)
    common = {
        "workspace_id": "ws-1",
        "query_vector": (1.0, 0.0),
        "query_text": "权限",
        "limit": 5,
    }

    dense = await store.search("fixture_v1", retrieval_mode="DENSE", **common)
    sparse = await store.search("fixture_v1", retrieval_mode="SPARSE", **common)
    hybrid = await store.search("fixture_v1", retrieval_mode="HYBRID", **common)

    assert dense == [("dense_vector-chunk", 0.9)]
    assert sparse == [("sparse_vector-chunk", 0.9)]
    assert hybrid == [("hybrid-chunk", 0.8)]
    expected_filter = 'workspace_id == "ws-1" and active == true'
    assert [call["filter"] for call in client.search_calls] == [
        expected_filter,
        expected_filter,
    ]
    assert [
        request["expr"]
        for request in client.hybrid_calls[0]["reqs"]
    ] == [expected_filter, expected_filter]
    assert all(call["output_fields"] == ["chunk_id"] for call in client.search_calls)
    assert client.hybrid_calls[0]["output_fields"] == ["chunk_id"]
    assert all(call["timeout"] == 3.5 for call in client.search_calls)
    assert client.hybrid_calls[0]["timeout"] == 3.5
    assert all("consistency_level" not in call for call in client.search_calls)
    assert "consistency_level" not in client.hybrid_calls[0]


@pytest.mark.asyncio
async def test_milvus_search_accepts_top_level_primary_key_and_rejects_missing_key() -> None:
    class Client:
        result: dict[str, Any] = {"chunk_id": "top-level", "distance": 0.7}

        async def search(self, **kwargs: Any) -> list[list[dict[str, Any]]]:
            return [[self.result]]

    client = Client()
    store = _store(async_client=client)
    kwargs = {
        "workspace_id": "ws-1",
        "query_vector": (1.0, 0.0),
        "query_text": "权限",
        "limit": 5,
        "retrieval_mode": "DENSE",
    }

    assert await store.search("fixture_v1", **kwargs) == [("top-level", 0.7)]
    client.result = {"distance": 0.7, "entity": {}}
    with pytest.raises(AdapterUnavailableError, match="missing required field 'chunk_id'"):
        await store.search("fixture_v1", **kwargs)


@pytest.mark.asyncio
async def test_milvus_health_uses_external_storage_timeout() -> None:
    class Client:
        timeout: float | None = None

        async def list_collections(self, *, timeout: float) -> list[str]:
            self.timeout = timeout
            return []

    client = Client()
    store = _store(async_client=client)

    assert await store.health() is True
    assert client.timeout == 3.5


class FakeIterator:
    def __init__(self, rows: list[dict[str, Any]], batch_size: int) -> None:
        self._rows = rows
        self._batch_size = batch_size
        self._offset = 0
        self.closed = False

    def next(self) -> list[dict[str, Any]]:
        result = self._rows[self._offset : self._offset + self._batch_size]
        self._offset += len(result)
        return deepcopy(result)

    def close(self) -> None:
        self.closed = True


class VersionMutationClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {row["chunk_id"]: deepcopy(row) for row in rows}
        self.query_calls: list[dict[str, Any]] = []
        self.upsert_batch_sizes: list[int] = []
        self.flush_count = 0
        self.close_count = 0
        self.verification_override: list[dict[str, Any]] | None = None
        self.iterators: list[FakeIterator] = []

    def query_iterator(self, **kwargs: Any) -> FakeIterator:
        self.query_calls.append(kwargs)
        assert kwargs["batch_size"] == 1000
        assert kwargs["limit"] == -1
        assert kwargs["consistency_level"] == "Strong"
        assert kwargs["timeout"] == 3.5
        expression = kwargs["filter"]
        rows = [
            row
            for row in self.rows.values()
            if row["workspace_id"] == "ws-1" and row["document_version_id"] == "version-1"
        ]
        if "active ==" in expression:
            if self.verification_override is not None:
                rows = self.verification_override
            else:
                expected = expression.endswith("active == true")
                rows = [row for row in rows if row["active"] is expected]
        output_fields = kwargs["output_fields"]
        projected = [{field: row[field] for field in output_fields} for row in rows]
        iterator = FakeIterator(projected, kwargs["batch_size"])
        self.iterators.append(iterator)
        return iterator

    def upsert(
        self, *, collection_name: str, data: list[dict[str, Any]], timeout: float
    ) -> None:
        assert collection_name == "knowledge_chunks__fixture_v1"
        assert timeout == 3.5
        self.upsert_batch_sizes.append(len(data))
        for row in data:
            self.rows[row["chunk_id"]] = deepcopy(row)

    def flush(self, *, collection_name: str, timeout: float) -> None:
        assert collection_name == "knowledge_chunks__fixture_v1"
        assert timeout == 3.5
        self.flush_count += 1

    def close(self) -> None:
        self.close_count += 1


def _version_row(
    chunk_id: str,
    *,
    workspace_id: str = "ws-1",
    version_id: str = "version-1",
    active: bool = False,
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "workspace_id": workspace_id,
        "document_id": "doc-1",
        "document_version_id": version_id,
        "parent_id": None,
        "text": f"evidence {chunk_id}",
        "source_format": "TXT",
        "page": 1,
        "created_at_ms": 1,
        "dense_vector": [1.0, 0.0],
        "active": active,
    }


@pytest.mark.asyncio
async def test_milvus_activation_and_deactivation_iterate_all_rows_and_preserve_scope() -> None:
    target_rows = [_version_row(f"chunk-{index:04d}") for index in range(1005)]
    cross_workspace = _version_row("cross-workspace", workspace_id="ws-2")
    other_version = _version_row("other-version", version_id="version-2")
    sync_client = VersionMutationClient([*target_rows, cross_workspace, other_version])
    store = _store(async_client=object(), sync_client=sync_client)

    await store.activate_version("fixture_v1", "ws-1", "version-1")

    assert sync_client.upsert_batch_sizes == [1000, 5]
    assert all(sync_client.rows[row["chunk_id"]]["active"] for row in target_rows)
    assert sync_client.rows["cross-workspace"]["active"] is False
    assert sync_client.rows["other-version"]["active"] is False
    assert sync_client.flush_count == 1
    assert sync_client.query_calls[0]["filter"] == (
        'workspace_id == "ws-1" and document_version_id == "version-1"'
    )
    assert sync_client.query_calls[1]["filter"].endswith("active == true")

    await store.deactivate_version("fixture_v1", "ws-1", "version-1")

    assert sync_client.upsert_batch_sizes[-2:] == [1000, 5]
    assert not any(sync_client.rows[row["chunk_id"]]["active"] for row in target_rows)
    assert sync_client.rows["cross-workspace"]["active"] is False
    assert sync_client.flush_count == 2
    assert sync_client.query_calls[-1]["filter"].endswith("active == false")
    assert sync_client.close_count == 2
    assert all(iterator.closed for iterator in sync_client.iterators)


@pytest.mark.asyncio
async def test_milvus_deactivation_empty_set_is_idempotent_but_activation_is_not() -> None:
    sync_client = VersionMutationClient([])
    store = _store(async_client=object(), sync_client=sync_client)

    await store.deactivate_version("fixture_v1", "ws-1", "version-1")
    await store.deactivate_version("fixture_v1", "ws-1", "version-1")
    assert sync_client.flush_count == 0
    assert sync_client.upsert_batch_sizes == []

    with pytest.raises(AdapterUnavailableError, match="activation found no vectors"):
        await store.activate_version("fixture_v1", "ws-1", "version-1")
    assert sync_client.flush_count == 0
    assert sync_client.close_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "expected_message"),
    [
        ([{"chunk_id": "chunk-1"}], "missing 1, unexpected 0"),
        (
            [
                {"chunk_id": "chunk-1"},
                {"chunk_id": "chunk-2"},
                {"chunk_id": "unexpected"},
            ],
            "missing 0, unexpected 1",
        ),
    ],
)
async def test_milvus_activation_verification_detects_missing_and_unexpected(
    override: list[dict[str, Any]], expected_message: str
) -> None:
    sync_client = VersionMutationClient([_version_row("chunk-1"), _version_row("chunk-2")])
    sync_client.verification_override = override
    store = _store(async_client=object(), sync_client=sync_client)

    with pytest.raises(AdapterUnavailableError, match=expected_message):
        await store.activate_version("fixture_v1", "ws-1", "version-1")


class FailingIterator(FakeIterator):
    def next(self) -> list[dict[str, Any]]:
        raise RuntimeError("iterator next failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["next", "upsert", "flush", "verification"])
async def test_milvus_version_mutation_closes_resources_on_each_failure(
    failure_phase: str,
) -> None:
    class Client(VersionMutationClient):
        def query_iterator(self, **kwargs: Any) -> FakeIterator:
            iterator = super().query_iterator(**kwargs)
            call_number = len(self.query_calls)
            if failure_phase == "next" and call_number == 1:
                failing = FailingIterator([], 1000)
                self.iterators[-1] = failing
                return failing
            if failure_phase == "verification" and call_number == 2:
                failing = FailingIterator([], 1000)
                self.iterators[-1] = failing
                return failing
            return iterator

        def upsert(
            self, *, collection_name: str, data: list[dict[str, Any]], timeout: float
        ) -> None:
            if failure_phase == "upsert":
                raise RuntimeError("upsert failed")
            super().upsert(
                collection_name=collection_name,
                data=data,
                timeout=timeout,
            )

        def flush(self, *, collection_name: str, timeout: float) -> None:
            if failure_phase == "flush":
                raise RuntimeError("flush failed")
            super().flush(collection_name=collection_name, timeout=timeout)

    client = Client(
        [
            _version_row("target"),
            _version_row("cross", workspace_id="ws-2"),
        ]
    )
    store = _store(async_client=object(), sync_client=client)

    with pytest.raises(RuntimeError):
        await store.activate_version("fixture_v1", "ws-1", "version-1")

    assert client.close_count == 1
    assert all(iterator.closed for iterator in client.iterators)
    assert client.rows["cross"]["active"] is False


@pytest.mark.asyncio
async def test_milvus_version_mutation_rejects_cross_scope_row_before_writing() -> None:
    class Client(VersionMutationClient):
        def query_iterator(self, **kwargs: Any) -> FakeIterator:
            if not self.query_calls:
                self.query_calls.append(kwargs)
                iterator = FakeIterator(
                    [
                        _version_row("target"),
                        _version_row("malicious", workspace_id="ws-2"),
                    ],
                    1000,
                )
                self.iterators.append(iterator)
                return iterator
            return super().query_iterator(**kwargs)

    client = Client(
        [
            _version_row("target"),
            _version_row("malicious", workspace_id="ws-2"),
        ]
    )
    store = _store(async_client=object(), sync_client=client)

    with pytest.raises(AdapterUnavailableError, match="outside its workspace/version scope"):
        await store.activate_version("fixture_v1", "ws-1", "version-1")

    assert client.upsert_batch_sizes == []
    assert client.rows["target"]["active"] is False
    assert client.rows["malicious"]["active"] is False
    assert client.close_count == 1
    assert all(iterator.closed for iterator in client.iterators)


@pytest.mark.asyncio
async def test_milvus_cancellation_waits_for_worker_cleanup_before_propagating() -> None:
    entered = threading.Event()
    release = threading.Event()

    class Client(VersionMutationClient):
        def upsert(
            self, *, collection_name: str, data: list[dict[str, Any]], timeout: float
        ) -> None:
            entered.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test worker was not released")
            super().upsert(
                collection_name=collection_name,
                data=data,
                timeout=timeout,
            )

    client = Client([_version_row("target")])
    store = _store(async_client=object(), sync_client=client)
    task = asyncio.create_task(
        store.activate_version("fixture_v1", "ws-1", "version-1")
    )
    assert await asyncio.to_thread(entered.wait, 1)

    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    assert client.close_count == 0

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.close_count == 1
    assert client.rows["target"]["active"] is True
    assert all(iterator.closed for iterator in client.iterators)


@pytest.mark.asyncio
async def test_milvus_upsert_stages_inactive_rows_without_flushing() -> None:
    class Client:
        def __init__(self) -> None:
            self.data: list[dict[str, Any]] = []
            self.flush_count = 0

        async def upsert(
            self, *, collection_name: str, data: list[dict[str, Any]], timeout: float
        ) -> None:
            assert collection_name == "knowledge_chunks__fixture_v1"
            assert timeout == 3.5
            self.data = data

        async def flush(self, **kwargs: Any) -> None:
            self.flush_count += 1

    client = Client()
    store = _store(async_client=client)
    await store.upsert(
        "fixture_v1",
        [
            VectorRecord(
                chunk_id="chunk-1",
                workspace_id="ws-1",
                document_id="doc-1",
                document_version_id="version-1",
                parent_id=None,
                text="evidence",
                dense_vector=(1.0, 0.0),
                active=True,
            )
        ],
    )

    assert client.data[0]["active"] is False
    assert client.flush_count == 0


def _valid_schema(dimensions: str = "1024") -> dict[str, Any]:
    def field(
        name: str,
        field_type: int,
        *,
        nullable: bool = False,
        primary: bool = False,
        **params: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"name": name, "type": field_type, "params": params}
        if nullable:
            result["nullable"] = True
        if primary:
            result["is_primary"] = True
        return result

    return {
        "auto_id": False,
        "enable_dynamic_field": False,
        "fields": [
            field("chunk_id", FakeDataType.VARCHAR, primary=True, max_length="64"),
            field("workspace_id", FakeDataType.VARCHAR, max_length="64"),
            field("document_id", FakeDataType.VARCHAR, max_length="64"),
            field("document_version_id", FakeDataType.VARCHAR, max_length="64"),
            field("parent_id", FakeDataType.VARCHAR, nullable=True, max_length="64"),
            field(
                "text",
                FakeDataType.VARCHAR,
                max_length="65535",
                enable_analyzer="true",
                enable_match="true",
                analyzer_params='{"tokenizer":"jieba"}',
            ),
            field("source_format", FakeDataType.VARCHAR, max_length="16"),
            field("page", FakeDataType.INT64, nullable=True),
            field("created_at_ms", FakeDataType.INT64),
            field("dense_vector", FakeDataType.FLOAT_VECTOR, dim=dimensions),
            field("sparse_vector", FakeDataType.SPARSE_FLOAT_VECTOR),
            field("active", FakeDataType.BOOL),
        ],
        "functions": [
            {
                "name": "bm25",
                "type": FakeFunctionType.BM25,
                "input_field_names": ["text"],
                "output_field_names": ["sparse_vector"],
            }
        ],
    }


class ExistingCollectionClient:
    def __init__(
        self,
        description: dict[str, Any],
        *,
        indexes: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.description = description
        self.indexes = indexes if indexes is not None else {
            "dense_idx": {
                "field_name": "dense_vector",
                "index_type": "HNSW",
                "metric_type": "COSINE",
            },
            "sparse_idx": {
                "field_name": "sparse_vector",
                "index_type": "SPARSE_INVERTED_INDEX",
                "metric_type": "BM25",
            },
        }

    async def has_collection(self, **kwargs: Any) -> bool:
        assert kwargs["timeout"] == 3.5
        return True

    async def describe_collection(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["timeout"] == 3.5
        return self.description

    async def list_indexes(self, **kwargs: Any) -> list[str]:
        assert kwargs["timeout"] == 3.5
        field_name = kwargs["field_name"]
        return [
            name
            for name, description in self.indexes.items()
            if description["field_name"] == field_name
        ]

    async def describe_index(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["timeout"] == 3.5
        return self.indexes[kwargs["index_name"]]


@pytest.mark.asyncio
async def test_milvus_existing_collection_full_schema_is_validated() -> None:
    valid = _valid_schema()
    store = _store(async_client=ExistingCollectionClient(valid))
    assert await store.ensure_schema("fixture_v1", 1024) == "knowledge_chunks__fixture_v1"

    wrong_dimension = _valid_schema("128")
    store._client = ExistingCollectionClient(wrong_dimension)
    with pytest.raises(
        AdapterUnavailableError,
        match=r"dimension mismatch: configured 1024, existing 128",
    ):
        await store.ensure_schema("fixture_v1", 1024)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "expected_message"),
    [
        (
            lambda schema: schema["fields"].pop(1),
            "field set mismatch",
        ),
        (
            lambda schema: schema["fields"][1].update({"type": FakeDataType.INT64}),
            "field 'workspace_id' type mismatch",
        ),
        (
            lambda schema: schema.update({"functions": []}),
            "BM25 function mismatch",
        ),
        (
            lambda schema: schema["fields"][10].update({"type": FakeDataType.FLOAT_VECTOR}),
            "field 'sparse_vector' type mismatch",
        ),
        (
            lambda schema: schema["fields"][5]["params"].update(
                {"analyzer_params": '{"tokenizer":"standard"}'}
            ),
            "text analyzer configuration mismatch",
        ),
    ],
)
async def test_milvus_existing_collection_rejects_incompatible_schema(
    mutate: Any, expected_message: str
) -> None:
    schema = _valid_schema()
    mutate(schema)
    store = _store(async_client=ExistingCollectionClient(schema))

    with pytest.raises(AdapterUnavailableError, match=expected_message):
        await store.ensure_schema("fixture_v1", 1024)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("indexes", "expected_message"),
    [
        (
            {
                "dense_idx": {
                    "field_name": "dense_vector",
                    "index_type": "HNSW",
                    "metric_type": "COSINE",
                }
            },
            "field 'sparse_vector' index mismatch",
        ),
        (
            {
                "dense_idx": {
                    "field_name": "dense_vector",
                    "index_type": "HNSW",
                    "metric_type": "L2",
                },
                "sparse_idx": {
                    "field_name": "sparse_vector",
                    "index_type": "SPARSE_INVERTED_INDEX",
                    "metric_type": "BM25",
                },
            },
            "field 'dense_vector' index mismatch",
        ),
    ],
)
async def test_milvus_existing_collection_rejects_missing_or_wrong_index(
    indexes: dict[str, dict[str, Any]], expected_message: str
) -> None:
    store = _store(async_client=ExistingCollectionClient(_valid_schema(), indexes=indexes))

    with pytest.raises(AdapterUnavailableError, match=expected_message):
        await store.ensure_schema("fixture_v1", 1024)
