"""Utilities for managing conversation memory across Cosmos DB and Azure AI Search."""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)


@dataclass
class ConversationStatus:
    """Represents the processing status of a conversation thread."""

    id: str
    processed: bool
    etag: Optional[str] = None
    timestamp: Optional[_dt.datetime] = None


class CosmosConversationStore:
    """Simple repository for conversation processing metadata in Cosmos DB."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        key: Optional[str] = None,
        *,
        database_name: str = "insurance_claims",
        container_name: str = "conversations",
        throughput: int = 400,
    ) -> None:
        self.endpoint = endpoint or os.getenv("COSMOS_ENDPOINT")
        self.key = key or os.getenv("COSMOS_KEY")
        self.database_name = database_name
        self.container_name = container_name
        self._throughput = throughput

        self._client: Optional[CosmosClient] = None
        self._database = None
        self._container = None

        self._initialise()

    # ------------------------------------------------------------------
    def _initialise(self) -> None:
        if not self.endpoint or not self.key:
            print("⚠️ Cosmos DB endpoint or key is missing – conversation tracking disabled.")
            return

        try:
            self._client = CosmosClient(self.endpoint, credential=self.key)
            self._database = self._client.create_database_if_not_exists(id=self.database_name)

            try:
                self._container = self._database.create_container_if_not_exists(
                    id=self.container_name,
                    partition_key=PartitionKey(path="/id"),
                    offer_throughput=self._throughput,
                )
            except CosmosHttpResponseError as throughput_error:
                message = str(throughput_error).lower()
                if throughput_error.status_code in (400, 409) and "throughput" in message:
                    # Likely a serverless or shared throughput account – retry without offer_throughput
                    print("ℹ️ Cosmos account rejected dedicated throughput; retrying with shared/serverless configuration.")
                    self._container = self._database.create_container_if_not_exists(
                        id=self.container_name,
                        partition_key=PartitionKey(path="/id"),
                    )
                else:
                    raise

            print(f"🗄️ Cosmos conversation container ready: {self.container_name}")
        except Exception as exc:  # pragma: no cover - connection failures
            print(f"❌ Failed to initialise Cosmos conversations container: {exc}")
            self._client = None
            self._database = None
            self._container = None

    # ------------------------------------------------------------------
    @property
    def container(self):
        return self._container

    # ------------------------------------------------------------------
    def register_thread(self, thread_id: str) -> bool:
        """Ensure a thread document exists with processed = False."""
        if not thread_id:
            return False

        if not self._container:
            print("⚠️ Conversation container unavailable; cannot register thread.")
            return False

        try:
            self._container.read_item(item=thread_id, partition_key=thread_id)
            return False
        except CosmosResourceNotFoundError:
            document = {"id": thread_id, "processed": False}
            self._container.create_item(body=document)
            print(f"🗄️ ✅ Conversation '{thread_id}' registered")
            return True
        except CosmosHttpResponseError as err:
            if err.status_code == 409:
                return False
            raise

    # ------------------------------------------------------------------
    def get_unprocessed_threads(self, *, limit: Optional[int] = None) -> List[ConversationStatus]:
        if not self._container:
            return []

        query = "SELECT * FROM c WHERE c.processed = false"
        if limit:
            query = f"SELECT * FROM c WHERE c.processed = false OFFSET 0 LIMIT {int(limit)}"

        items = list(
            self._container.query_items(query=query, enable_cross_partition_query=True)
        )
        results: List[ConversationStatus] = []
        for item in items:
            timestamp = None
            if "_ts" in item:
                timestamp = _dt.datetime.utcfromtimestamp(item["_ts"])
            results.append(
                ConversationStatus(
                    id=item.get("id"),
                    processed=item.get("processed", False),
                    etag=item.get("_etag"),
                    timestamp=timestamp,
                )
            )
        return results

    # ------------------------------------------------------------------
    def mark_processed(self, thread_id: str) -> None:
        if not thread_id or not self._container:
            return

        try:
            document = self._container.read_item(item=thread_id, partition_key=thread_id)
            document["processed"] = True
            self._container.replace_item(item=thread_id, body=document)
        except CosmosResourceNotFoundError:
            print(f"⚠️ Cannot mark processed – conversation '{thread_id}' not found.")


# ----------------------------------------------------------------------
# Azure AI Search indexing support
# ----------------------------------------------------------------------


def _normalize_openai_endpoint(endpoint: Optional[str]) -> Optional[str]:
    if not endpoint:
        return None

    endpoint = endpoint.rstrip("/")

    if endpoint.endswith(".openai.azure.com"):
        return endpoint

    if ".cognitiveservices.azure.com" in endpoint:
        resource_name = endpoint.split("//")[-1].split(".")[0]
        return f"https://{resource_name}.openai.azure.com"

    if "/openai/" in endpoint:
        parts = endpoint.split("//")[-1].split("/")
        resource_name = parts[0].split(".")[0]
        return f"https://{resource_name}.openai.azure.com"

    return endpoint


class ConversationSearchIndexer:
    """Helper that manages the Azure AI Search index for conversations."""

    def __init__(
        self,
        *,
        search_endpoint: Optional[str] = None,
        admin_key: Optional[str] = None,
        index_name: Optional[str] = None,
        openai_endpoint: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        embedding_deployment: Optional[str] = None,
        vector_dimensions: int = 1536,
    ) -> None:
        self.search_endpoint = search_endpoint or os.getenv("SEARCH_SERVICE_ENDPOINT")
        self.admin_key = admin_key or os.getenv("SEARCH_ADMIN_KEY")
        self.index_name = index_name or os.getenv("CONVERSATION_SEARCH_INDEX", "conversation-memory-index")
        self.openai_endpoint = _normalize_openai_endpoint(openai_endpoint or os.getenv("AZURE_OPENAI_ENDPOINT"))
        self.openai_api_key = openai_api_key or os.getenv("AZURE_OPENAI_KEY")
        self.embedding_deployment = embedding_deployment or os.getenv(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002"
        )
        self.vector_dimensions = vector_dimensions

        self._search_client: Optional[SearchClient] = None
        self._index_client: Optional[SearchIndexClient] = None
        self._credential: Optional[AzureKeyCredential] = None

        self._initialise()

    # ------------------------------------------------------------------
    def _initialise(self) -> None:
        if not self.search_endpoint or not self.admin_key:
            print("⚠️ Azure AI Search endpoint or admin key missing – indexing disabled.")
            return

        if not self.openai_endpoint or not self.openai_api_key:
            print("⚠️ Azure OpenAI settings missing – vectorisation disabled.")
            return

        self._credential = AzureKeyCredential(self.admin_key)
        self._index_client = SearchIndexClient(endpoint=self.search_endpoint, credential=self._credential)
        self._search_client = SearchClient(
            endpoint=self.search_endpoint, index_name=self.index_name, credential=self._credential
        )
        self._ensure_index()

    # ------------------------------------------------------------------
    def _ensure_index(self) -> None:
        if not self._index_client:
            return

        try:
            self._index_client.get_index(self.index_name)
        except ResourceNotFoundError:
            index_definition = self._build_index_definition()
            self._index_client.create_index(index_definition)

    # ------------------------------------------------------------------
    def _build_index_definition(self) -> SearchIndex:
        vectorizer = AzureOpenAIVectorizer(
            vectorizer_name="conversation-vectorizer",
            parameters=AzureOpenAIVectorizerParameters(
                resource_url=self.openai_endpoint,
                deployment_name=self.embedding_deployment,
                model_name="text-embedding-ada-002",
                api_key=self.openai_api_key,
            ),
        )

        vector_search = VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="conversation-hnsw", kind="hnsw")],
            vectorizers=[vectorizer],
            profiles=[
                VectorSearchProfile(
                    name="conversation-profile",
                    algorithm_configuration_name="conversation-hnsw",
                    vectorizer_name="conversation-vectorizer",
                )
            ],
        )

        semantic_search = SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name="conversation-semantic",
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="thread_id"),
                        content_fields=[SemanticField(field_name="content")],
                        keywords_fields=[SemanticField(field_name="thread_id")],
                    ),
                )
            ]
        )

        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SimpleField(name="thread_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="message_count", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
            SimpleField(name="content_length", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
            SimpleField(
                name="conversation_timestamp",
                type=SearchFieldDataType.DateTimeOffset,
                filterable=True,
                sortable=True,
            ),
            SimpleField(
                name="processed_timestamp",
                type=SearchFieldDataType.DateTimeOffset,
                filterable=True,
                sortable=True,
            ),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=self.vector_dimensions,
                vector_search_profile_name="conversation-profile",
            ),
        ]

        return SearchIndex(
            name=self.index_name,
            fields=fields,
            vector_search=vector_search,
            semantic_search=semantic_search,
        )

    # ------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return bool(self._search_client and self._index_client)

    # ------------------------------------------------------------------
    def upsert_conversation(
        self,
        *,
        thread_id: str,
        content: str,
        conversation_timestamp: Optional[_dt.datetime],
        processed_timestamp: Optional[_dt.datetime],
        message_count: int,
    ) -> None:
        if not self.ready:
            print("⚠️ Search indexer not ready; skipping conversation upload.")
            return

        conversation_ts = _to_utc_iso(conversation_timestamp)
        processed_ts = _to_utc_iso(processed_timestamp)

        document = {
            "id": thread_id,
            "thread_id": thread_id,
            "content": content,
            "message_count": message_count,
            "content_length": len(content),
            "conversation_timestamp": conversation_ts,
            "processed_timestamp": processed_ts,
        }

        self._search_client.upload_documents(documents=[document])


# ----------------------------------------------------------------------
# Search helper for read scenarios
# ----------------------------------------------------------------------


class ConversationMemorySearcher:
    """Lightweight search helper for querying the conversation index."""

    def __init__(
        self,
        *,
        search_endpoint: Optional[str] = None,
        admin_key: Optional[str] = None,
        index_name: Optional[str] = None,
        ensure_index: bool = False,
    ) -> None:
        self.search_endpoint = search_endpoint or os.getenv("SEARCH_SERVICE_ENDPOINT")
        self.admin_key = admin_key or os.getenv("SEARCH_ADMIN_KEY")
        self.index_name = index_name or os.getenv("CONVERSATION_SEARCH_INDEX", "conversation-memory-index")

        self._credential: Optional[AzureKeyCredential] = None
        self._client: Optional[SearchClient] = None

        if ensure_index:
            ConversationSearchIndexer(
                search_endpoint=self.search_endpoint,
                admin_key=self.admin_key,
                index_name=self.index_name,
            )

        if self.search_endpoint and self.admin_key:
            self._credential = AzureKeyCredential(self.admin_key)
            self._client = SearchClient(
                endpoint=self.search_endpoint,
                index_name=self.index_name,
                credential=self._credential,
            )
        else:
            print("⚠️ Conversation memory search configuration missing – lookup disabled.")

    # ------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._client is not None

    # ------------------------------------------------------------------
    def search(self, query: str, *, top: int = 5) -> List[Dict[str, Any]]:
        if not self._client:
            return []

        try:
            results = self._client.search(
                search_text=query,
                top=top,
                query_type="semantic",
                semantic_configuration_name="conversation-semantic",
                select=[
                    "id",
                    "thread_id",
                    "content",
                    "message_count",
                    "conversation_timestamp",
                    "processed_timestamp",
                ],
            )
            payload: List[Dict[str, Any]] = []
            for item in results:
                payload.append(
                    {
                        "id": item.get("id"),
                        "thread_id": item.get("thread_id"),
                        "content": item.get("content"),
                        "message_count": item.get("message_count"),
                        "conversation_timestamp": item.get("conversation_timestamp"),
                        "processed_timestamp": item.get("processed_timestamp"),
                        "score": item.get("@search.score"),
                        "reranker_score": item.get("@search.reranker_score"),
                    }
                )
            return payload
        except Exception as exc:
            print(f"❌ Conversation search failed: {exc}")
            return []


# ----------------------------------------------------------------------
# Helper utilities for composing conversation content
# ----------------------------------------------------------------------


def _to_utc_iso(value: Optional[_dt.datetime]) -> Optional[str]:
    if not value:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    else:
        value = value.astimezone(_dt.timezone.utc)

    return value.isoformat().replace("+00:00", "Z")

def format_messages_for_index(messages: Iterable[Dict[str, Any]]) -> str:
    """Combine conversation messages into a single string ready for indexing."""
    formatted_parts: List[str] = []
    for message in messages:
        role = message.get("role", "unknown").upper()
        timestamp = message.get("created_at")
        text = message.get("text", "")
        formatted = f"[{timestamp}] {role}: {text}" if timestamp else f"{role}: {text}"
        formatted_parts.append(formatted.strip())
    return "\n".join(part for part in formatted_parts if part)
