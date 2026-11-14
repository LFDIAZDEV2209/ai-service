"""Reconcilia exclusivamente transcripts finales obtenidos del proveedor autenticado.

No acepta mensajes del cliente ni invoca un modelo. El vínculo de sesión del
servidor y el mismo lock del runtime protegen identidad, grupo y checkpoint.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.types import Overwrite

from app.core.config import get_settings
from app.vita.client import VitaAccessError, read_catalog
from app.vita.locking import ThreadBusyError, thread_guard
from app.voice.store import VitaVoiceSession, VoiceContextError, voice_store

MAX_TRANSCRIPT_BYTES = 1_000_000
MAX_ROWS = 500
MAX_TEXT_CHARS = 8000


class TranscriptUnavailableError(Exception):
    pass


class TranscriptProcessingError(Exception):
    pass


@dataclass(frozen=True)
class VerifiedTranscript:
    messages: list[BaseMessage]
    digest: str


def parse_transcript(payload: object, record: VitaVoiceSession) -> VerifiedTranscript:
    if (
        not isinstance(payload, dict)
        or payload.get("agent_id") != record.agent_id
        or payload.get("conversation_id") != record.conversation_id
    ):
        raise TranscriptUnavailableError("voice_transcript_binding")
    if (
        record.version_id
        and payload.get("version_id") is not None
        and payload["version_id"] != record.version_id
    ):
        raise TranscriptUnavailableError("voice_transcript_version")
    if payload.get("status") in {"initiated", "in-progress", "processing"}:
        raise TranscriptProcessingError
    rows = payload.get("transcript")
    if payload.get("status") != "done" or not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise TranscriptUnavailableError("voice_transcript_not_final")
    messages, seen, occurrences = [], set(), {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("role") not in {"user", "agent"}:
            continue
        text = row.get("message")
        if not isinstance(text, str) or not text.strip():
            continue
        if len(text) > MAX_TEXT_CHARS:
            raise TranscriptUnavailableError("voice_transcript_text_limit")
        if row.get("ignored_as_backchannel") is True:
            continue
        if any(
            row.get(flag) is not None and type(row[flag]) is not bool
            for flag in ("interrupted", "ignored_as_backchannel")
        ):
            raise TranscriptUnavailableError("voice_transcript_invalid_flag")
        role, provider_id, event_id = row["role"], row.get("id"), row.get("source_event_id")
        if provider_id is not None:
            if isinstance(provider_id, bool) or not isinstance(provider_id, (str, int)):
                raise TranscriptUnavailableError("voice_transcript_invalid_id")
            provider_id = str(provider_id)
            if not provider_id or len(provider_id) > 200:
                raise TranscriptUnavailableError("voice_transcript_invalid_id")
        if event_id is not None and (type(event_id) is not int or not 0 <= event_id < 2**63):
            raise TranscriptUnavailableError("voice_transcript_invalid_event")
        if provider_id is not None:
            identity, basis = [record.conversation_id, role, "id", provider_id], "message_id"
        elif event_id is not None:
            key = (role, event_id)
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            identity, basis = (
                [record.conversation_id, role, "event", event_id, occurrence],
                "event_role",
            )
        else:
            identity, basis = [record.conversation_id, role, "index", index], "index"
        suffix = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        message_id = f"voice:{record.id}:provider:{suffix}"
        if message_id in seen:
            raise TranscriptUnavailableError("voice_transcript_duplicate_id")
        seen.add(message_id)
        metadata = {
            "channel": "voice",
            "source": "elevenlabs_transcript",
            "voice_session_id": record.id,
            "provider_message_id": provider_id,
            "source_event_id": event_id,
            "provider_identity": basis,
            "provider_row_index": index,
            "status": "interrupted" if row.get("interrupted") is True else "provider_final",
            "revision": 1,
        }
        cls = HumanMessage if role == "user" else AIMessage
        messages.append(cls(id=message_id, content=text, additional_kwargs=metadata))
    digest = hashlib.sha256(
        json.dumps(
            [
                {"id": m.id, "role": m.type, "text": m.content, "metadata": m.additional_kwargs}
                for m in messages
            ],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return VerifiedTranscript(messages, digest)


async def fetch_transcript(record: VitaVoiceSession) -> VerifiedTranscript:
    settings = get_settings()
    if not settings.elevenlabs_api_key:
        raise TranscriptUnavailableError("voice_transcript_unconfigured")
    url = (
        f"{settings.elevenlabs_base_url.rstrip('/')}/v1/convai/conversations/"
        f"{quote(record.conversation_id, safe='')}"
    )
    async with (
        httpx.AsyncClient(
            timeout=min(settings.vita_transcript_sync_timeout, 3), follow_redirects=False
        ) as client,
        client.stream(
            "GET",
            url,
            headers={"xi-api-key": settings.elevenlabs_api_key},
        ) as response,
    ):
        if response.status_code != 200:
            raise TranscriptUnavailableError("voice_transcript_upstream")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_TRANSCRIPT_BYTES:
                raise TranscriptUnavailableError("voice_transcript_size")
    return parse_transcript(json.loads(body), record)


def _belongs(message: BaseMessage, session_id: str) -> bool:
    return message.additional_kwargs.get("voice_session_id") == session_id or str(
        message.id or ""
    ).startswith(f"voice:{session_id}:")


def reconcile_messages(
    existing: list[BaseMessage],
    transcript: VerifiedTranscript,
    session_id: str,
) -> list[BaseMessage]:
    previous = {m.id: m for m in existing if _belongs(m, session_id)}
    incoming = []
    for message in transcript.messages:
        old = previous.get(message.id)
        metadata = dict(message.additional_kwargs)
        if old is not None:
            prior = {k: v for k, v in old.additional_kwargs.items() if k != "revision"}
            current = {k: v for k, v in metadata.items() if k != "revision"}
            changed = old.content != message.content or prior != current
            metadata["revision"] = int(old.additional_kwargs.get("revision", 1)) + int(changed)
        incoming.append(message.model_copy(update={"additional_kwargs": metadata}))
    kept, position, runtime_block = [], None, False
    for message in existing:
        belongs = _belongs(message, session_id)
        if isinstance(message, HumanMessage):
            runtime_block = belongs
        remove = belongs or runtime_block
        if remove:
            if position is None:
                position = len(kept)
            if isinstance(message, AIMessage) and belongs and not message.tool_calls:
                runtime_block = False
        else:
            kept.append(message)
    at = len(kept) if position is None else position
    return [*kept[:at], *incoming, *kept[at:]]


async def sync_closed_thread(user_id: str, thread_id: str, graph) -> tuple[str, bool]:
    """BACK solo solicita este método con consentimiento chat+voz vigente.

    AI vuelve a comprobar chat/paciente y propiedad; los IDs del proveedor salen
    exclusivamente de sus filas reservadas, nunca del body/query del paciente.
    """
    settings = get_settings()
    if not settings.vita_transcript_sync_enabled:
        return "idle", False
    bound, statuses = False, []
    try:
        async with asyncio.timeout(settings.vita_transcript_sync_timeout):
            candidates = await voice_store.closed_for_thread(user_id, thread_id)
            bound = bool(candidates)
            if not bound:
                return "idle", False
            await read_catalog(user_id)
            config = {"configurable": {"thread_id": f"{user_id}::{thread_id}"}}
            async with thread_guard(config["configurable"]["thread_id"]):
                for candidate in candidates:
                    record = await voice_store.get_owned(candidate.id, user_id, thread_id)
                    if not record.revoked and record.expires_at > datetime.now(UTC):
                        statuses.append("processing")
                        continue
                    try:
                        transcript = await fetch_transcript(record)
                    except TranscriptProcessingError:
                        statuses.append("processing")
                        continue
                    except (TranscriptUnavailableError, httpx.HTTPError, ValueError):
                        statuses.append("unavailable")
                        continue
                    snapshot = await graph.aget_state(config)
                    existing = list((snapshot.values or {}).get("messages", [])) if snapshot else []
                    reconciled = reconcile_messages(existing, transcript, record.id)
                    if existing != reconciled:
                        await graph.aupdate_state(
                            config,
                            {"messages": Overwrite(reconciled)},
                            as_node="agent",
                        )
                    await voice_store.mark_transcript_synced(record.id, transcript.digest)
                    statuses.append("synchronized")
    except ThreadBusyError:
        return "busy", bound
    except VitaAccessError as exc:
        if str(exc) == "vita_forbidden":
            raise VoiceContextError("voice_sync_not_authorized") from exc
        return "unavailable", bound
    except VoiceContextError:
        raise
    except Exception:
        # Fallos de red/DB nunca borran ni inventan contenido. CancelledError
        # continúa propagándose; no se imprime payload, transcript ni credenciales.
        return "unavailable", bound
    return next(
        (s for s in ("unavailable", "processing", "synchronized") if s in statuses), "idle"
    ), bound
