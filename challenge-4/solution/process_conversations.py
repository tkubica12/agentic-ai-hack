"""Batch processor for conversation memory indexing.

This script identifies unprocessed conversation threads stored in Cosmos DB,
fetches their messages from Azure AI Agents, and stores a searchable summary in
Azure AI Search with integrated vectorization. Successful runs mark each thread
as processed.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
from typing import List, Tuple

from azure.identity.aio import DefaultAzureCredential
from semantic_kernel.agents import AzureAIAgent
from dotenv import find_dotenv, load_dotenv

from conversation_memory import (
    ConversationSearchIndexer,
    CosmosConversationStore,
    ConversationStatus,
    format_messages_for_index,
)


def _load_environment() -> None:
    """Load environment variables from .env if available."""
    env_path = find_dotenv(usecwd=True)
    if env_path:
        load_dotenv(env_path)
    else:
        load_dotenv()


async def _ensure_agent_client() -> Tuple[AzureAIAgent, DefaultAzureCredential]:
    endpoint = os.getenv("AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError("AI_FOUNDRY_PROJECT_ENDPOINT environment variable is missing")

    credential = DefaultAzureCredential()
    client = AzureAIAgent.create_client(credential=credential, endpoint=endpoint)
    return client, credential


async def _fetch_thread_messages(
    client: AzureAIAgent, thread_id: str
) -> Tuple[List[dict], dt.datetime | None]:
    messages: List[dict] = []
    thread_timestamp: dt.datetime | None = None

    try:
        threads_client = getattr(client.agents, "threads", None)
        get_method = getattr(threads_client, "get", None) if threads_client else None
        if callable(get_method):
            thread = await get_method(thread_id=thread_id)
            created_at = getattr(thread, "created_at", None)
            if isinstance(created_at, dt.datetime):
                thread_timestamp = created_at
            elif isinstance(created_at, (int, float)):
                thread_timestamp = dt.datetime.fromtimestamp(created_at, tz=dt.timezone.utc)
    except Exception as exc:
        print(f"⚠️ Unable to fetch metadata for thread {thread_id}: {exc}")

    async for msg in client.agents.messages.list(thread_id=thread_id):
        created_at_raw = getattr(msg, "created_at", None)
        created_at_iso: str | None = None
        if isinstance(created_at_raw, dt.datetime):
            created_at_iso = created_at_raw.isoformat()
        elif isinstance(created_at_raw, (int, float)):
            created_at_iso = dt.datetime.fromtimestamp(created_at_raw, tz=dt.timezone.utc).isoformat()

        text_fragments: List[str] = []
        for content in getattr(msg, "content", []):
            if getattr(content, "type", None) == "text" and getattr(content, "text", None):
                text_fragments.append(content.text.value)

        full_text = "\n".join(fragment for fragment in text_fragments if fragment)
        messages.append(
            {
                "id": msg.id,
                "role": getattr(msg, "role", "assistant"),
                "created_at": created_at_iso,
                "text": full_text,
            }
        )

    if not thread_timestamp and messages:
        # Use the timestamp of the first message as a fallback
        first = messages[0].get("created_at")
        if first:
            try:
                thread_timestamp = dt.datetime.fromisoformat(first)
            except ValueError:
                thread_timestamp = None

    return messages, thread_timestamp


async def process_conversation_thread(
    status: ConversationStatus,
    *,
    client: AzureAIAgent,
    store: CosmosConversationStore,
    indexer: ConversationSearchIndexer,
) -> bool:
    thread_id = status.id
    print(f"\n🧵 Processing thread: {thread_id}")

    messages, conversation_timestamp = await _fetch_thread_messages(client, thread_id)
    if not messages:
        print(f"⚠️ Thread {thread_id} has no messages; skipping")
        return False

    content = format_messages_for_index(messages)
    processed_timestamp = dt.datetime.utcnow()

    try:
        indexer.upsert_conversation(
            thread_id=thread_id,
            content=content,
            conversation_timestamp=conversation_timestamp,
            processed_timestamp=processed_timestamp,
            message_count=len(messages),
        )
    except Exception as exc:
        print(f"❌ Failed to index conversation {thread_id}: {exc}")
        return False

    store.mark_processed(thread_id)
    print(f"✅ Thread {thread_id} marked as processed")
    return True


async def run_batch() -> None:
    _load_environment()

    required_vars = ["COSMOS_ENDPOINT", "COSMOS_KEY", "SEARCH_SERVICE_ENDPOINT", "SEARCH_ADMIN_KEY"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Ensure your .env is loaded or export them before running."
        )

    store = CosmosConversationStore()
    if not store.container:
        raise RuntimeError("Cosmos conversation container is not available")

    pending = store.get_unprocessed_threads()
    if not pending:
        print("📭 No unprocessed conversations found")
        return

    indexer = ConversationSearchIndexer()
    if not indexer.ready:
        raise RuntimeError("Azure AI Search indexer is not ready; check configuration")

    client, credential = await _ensure_agent_client()

    processed_count = 0
    try:
        for status in pending:
            success = await process_conversation_thread(
                status, client=client, store=store, indexer=indexer
            )
            if success:
                processed_count += 1
    finally:
        close_client = getattr(client, "close", None)
        if callable(close_client):
            try:
                await close_client()
            except TypeError:
                close_client()
        elif hasattr(client, "_client") and hasattr(client._client, "close"):
            close_inner = client._client.close  # type: ignore[attr-defined]
            if callable(close_inner):
                try:
                    await close_inner()
                except TypeError:
                    close_inner()

        await credential.close()

    print(f"\n🎯 Batch processing complete – {processed_count}/{len(pending)} threads processed.")


if __name__ == "__main__":
    asyncio.run(run_batch())
