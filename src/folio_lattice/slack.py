"""Optional Slack edge adapter; core MCP tools remain provider-neutral."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from .auth import get_request_principal
from .external_mcp import ExternalMcpBroker
from .service import FolioError, FolioLattice

SLACK_SEARCH_TOOL = "slack.search"
MAX_SLACK_QUERY_LENGTH = 500
MAX_SLACK_CHANNEL_LENGTH = 255
MAX_SLACK_TIME_LENGTH = 64
MAX_SLACK_MESSAGES = 50
MAX_SLACK_MESSAGE_LENGTH = 20_000
MAX_SLACK_ARTIFACT_BYTES = 512 * 1024
MAX_SLACK_RANGE = timedelta(days=31)
MAX_SLACK_POLICY_CHANNELS = 128
SLACK_POLICY_KEY = "slack"
_SLACK_POLICY_KEYS = frozenset({"channels", "max_time_range_seconds"})


class ApprovedSlackConsumer:
    """Internal adapter for one tenant-admin-approved Slack connection.

    This is deliberately not a general upstream proxy. The connection registry
    remains the source of authority, and this consumer can invoke only the exact
    slack.search tool with the bounded resource arguments below.

    Callers must use :class:`AuthenticatedSlackEdge`; methods accepting raw
    tenant and actor IDs are internal to the trusted edge integration.
    """

    def __init__(self, service: FolioLattice, broker: ExternalMcpBroker) -> None:
        self.service = service
        self.broker = broker

    @staticmethod
    def _text(value: Any, field: str, maximum: int) -> str:
        if not isinstance(value, str) or not value or len(value) > maximum:
            raise FolioError(f"Slack {field} is invalid")
        if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
            raise FolioError(f"Slack {field} is invalid")
        return value

    @classmethod
    def _timestamp(cls, value: Any, field: str) -> datetime:
        text = cls._text(value, field, MAX_SLACK_TIME_LENGTH)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise FolioError(f"Slack {field} must be an ISO-8601 timestamp") from None
        if parsed.tzinfo is None:
            raise FolioError(f"Slack {field} must include a timezone")
        return parsed.astimezone(UTC)

    @classmethod
    def _bounds(
        cls,
        *,
        channel: str,
        start_time: str,
        end_time: str,
        query: str,
        limit: int,
        max_range: timedelta = MAX_SLACK_RANGE,
    ) -> tuple[str, datetime, datetime, str, int]:
        channel = cls._text(channel, "channel", MAX_SLACK_CHANNEL_LENGTH)
        if any(character.isspace() for character in channel):
            raise FolioError("Slack channel is invalid")
        start = cls._timestamp(start_time, "start_time")
        end = cls._timestamp(end_time, "end_time")
        if end <= start:
            raise FolioError("Slack end_time must be after start_time")
        if end - start > max_range:
            raise FolioError("Slack time range exceeds the approved maximum")
        query = cls._text(query, "query", MAX_SLACK_QUERY_LENGTH)
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAX_SLACK_MESSAGES
        ):
            raise FolioError(f"Slack limit must be between 1 and {MAX_SLACK_MESSAGES}")
        return channel, start, end, query, limit

    @classmethod
    def _policy(cls, connection: Mapping[str, Any]) -> tuple[frozenset[str], timedelta]:
        policy = connection.get("policy")
        if not isinstance(policy, Mapping):
            raise FolioError("Slack connection policy is invalid")
        if not policy:
            raise FolioError("Slack connection policy is missing")
        if set(policy) != {SLACK_POLICY_KEY}:
            raise FolioError("Slack connection policy is invalid")
        slack_policy = policy.get(SLACK_POLICY_KEY)
        if not isinstance(slack_policy, Mapping):
            raise FolioError("Slack connection policy is missing")
        if set(slack_policy) != _SLACK_POLICY_KEYS:
            raise FolioError("Slack connection policy is invalid")
        channels = slack_policy.get("channels")
        if isinstance(channels, (str, bytes)) or not isinstance(channels, list):
            raise FolioError("Slack channel policy is invalid")
        if not 1 <= len(channels) <= MAX_SLACK_POLICY_CHANNELS:
            raise FolioError("Slack channel policy is invalid")
        normalized = {
            cls._text(channel, "approved channel", MAX_SLACK_CHANNEL_LENGTH) for channel in channels
        }
        if len(normalized) != len(channels) or any(
            any(character.isspace() for character in channel) for channel in normalized
        ):
            raise FolioError("Slack channel policy is invalid")
        max_seconds = slack_policy.get("max_time_range_seconds")
        if (
            not isinstance(max_seconds, int)
            or isinstance(max_seconds, bool)
            or not 1 <= max_seconds <= int(MAX_SLACK_RANGE.total_seconds())
        ):
            raise FolioError("Slack time-range policy is invalid")
        return frozenset(normalized), timedelta(seconds=max_seconds)

    @staticmethod
    def _wire_timestamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @classmethod
    def _messages(
        cls,
        raw: Any,
        *,
        channel: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, str]]:
        if isinstance(raw, Mapping):
            messages = raw.get("messages")
        else:
            messages = raw
        if not isinstance(messages, list):
            raise FolioError("Slack search returned an invalid message list")
        if len(messages) > limit:
            raise FolioError("Slack search returned more messages than requested")
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in messages:
            if not isinstance(item, Mapping):
                raise FolioError("Slack search returned an invalid message")
            message_id = cls._text(item.get("id"), "message id", 255)
            message_channel = cls._text(
                item.get("channel"), "message channel", MAX_SLACK_CHANNEL_LENGTH
            )
            timestamp_text = cls._text(
                item.get("timestamp"), "message timestamp", MAX_SLACK_TIME_LENGTH
            )
            text = cls._text(item.get("text"), "message text", MAX_SLACK_MESSAGE_LENGTH)
            timestamp = cls._timestamp(timestamp_text, "message timestamp")
            if message_channel != channel or timestamp < start or timestamp >= end:
                raise FolioError("Slack search returned a message outside its resource bounds")
            if message_id in seen:
                continue
            seen.add(message_id)
            result.append(
                {
                    "id": message_id,
                    "channel": message_channel,
                    "timestamp": cls._wire_timestamp(timestamp),
                    "text": text,
                }
            )
            if len(result) == limit:
                break
        return result

    @classmethod
    def _arguments(
        cls,
        *,
        channel: str,
        start: datetime,
        end: datetime,
        query: str,
        limit: int,
    ) -> dict[str, Any]:
        return {
            "channel": channel,
            "start_time": cls._wire_timestamp(start),
            "end_time": cls._wire_timestamp(end),
            "query": query,
            "limit": limit,
        }

    def _search_internal(
        self,
        *,
        tenant_id: str,
        actor: str,
        connection_id: str,
        channel: str,
        start_time: str,
        end_time: str,
        query: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        channel, start, end, query, limit = self._bounds(
            channel=channel,
            start_time=start_time,
            end_time=end_time,
            query=query,
            limit=limit,
        )
        arguments = self._arguments(
            channel=channel,
            start=start,
            end=end,
            query=query,
            limit=limit,
        )
        connection = self.broker.authorize_tool(
            tenant_id=tenant_id,
            actor=actor,
            connection_id=connection_id,
            tool_name=SLACK_SEARCH_TOOL,
            arguments=arguments,
        )
        approved_channels, max_range = self._policy(connection)
        if channel not in approved_channels:
            raise FolioError("Slack channel is not approved")
        if end - start > max_range:
            raise FolioError("Slack time range exceeds the approved maximum")
        messages = self._messages(
            self.broker.call_tool(
                tenant_id=tenant_id,
                actor=actor,
                connection_id=connection_id,
                tool_name=SLACK_SEARCH_TOOL,
                arguments=arguments,
            ),
            channel=channel,
            start=start,
            end=end,
            limit=limit,
        )
        return {
            "provider": "slack",
            "connection_id": connection_id,
            "channel": channel,
            "start_time": self._wire_timestamp(start),
            "end_time": self._wire_timestamp(end),
            "query": query,
            "policy_version": connection["policy_version"],
            "messages": messages,
            "match_count": len(messages),
        }

    @staticmethod
    def _artifact_body(search: Mapping[str, Any]) -> bytes:
        lines = [
            "# Slack approved search",
            "",
            f"Channel: {search['channel']}",
            f"Time range: {search['start_time']} to {search['end_time']}",
            f"Query: {search['query']}",
            "",
        ]
        for message in search["messages"]:
            lines.append(f"- {message['timestamp']} ({message['id']}): {message['text']}")
        body = ("\n".join(lines) + "\n").encode()
        if len(body) > MAX_SLACK_ARTIFACT_BYTES:
            raise FolioError("Slack saved artifact exceeds the allowed size")
        return body

    def _save_internal(
        self,
        *,
        tenant_id: str,
        actor: str,
        connection_id: str,
        name: str,
        channel: str,
        start_time: str,
        end_time: str,
        query: str,
        limit: int = 20,
        reason: str = "saved approved Slack search",
    ) -> dict[str, Any]:
        name = self._text(name, "artifact name", 255)
        reason = self._text(reason, "reason", 2_000)
        search = self._search_internal(
            tenant_id=tenant_id,
            actor=actor,
            connection_id=connection_id,
            channel=channel,
            start_time=start_time,
            end_time=end_time,
            query=query,
            limit=limit,
        )
        message_ids = [message["id"] for message in search["messages"]]
        source_context = {
            "interface": "approved-slack-search",
            "provider": "slack",
            "connection_id": search["connection_id"],
            "channel": search["channel"],
            "start_time": search["start_time"],
            "end_time": search["end_time"],
            "query": search["query"],
            "policy_version": search["policy_version"],
            "message_ids": message_ids,
        }
        created = self.service.create_artifact(
            tenant_id=tenant_id,
            name=name,
            data=self._artifact_body(search),
            media_type="text/markdown",
            actor=actor,
            reason=reason,
            source_context=source_context,
        )
        return {
            "artifact": created["artifact"],
            "version": created["version"],
            "source": source_context,
            "match_count": search["match_count"],
        }


class AuthenticatedSlackEdge:
    """Bind Slack adapter calls to the authenticated request principal."""

    def __init__(self, consumer: ApprovedSlackConsumer) -> None:
        self.consumer = consumer

    @staticmethod
    def _identity(*required_scopes: str, any_scope: tuple[str, ...] = ()) -> tuple[str, str]:
        principal = get_request_principal()
        if (
            principal is None
            or any(scope not in principal.scopes for scope in required_scopes)
            or (any_scope and not any(scope in principal.scopes for scope in any_scope))
        ):
            raise FolioError("operation not permitted")
        return principal.tenant_id, principal.actor_id

    def search(self, *, connection_id: str, **kwargs: Any) -> dict[str, Any]:
        tenant_id, actor = self._identity("artifact:search")
        return self.consumer._search_internal(
            tenant_id=tenant_id, actor=actor, connection_id=connection_id, **kwargs
        )

    def save(self, *, connection_id: str, **kwargs: Any) -> dict[str, Any]:
        tenant_id, actor = self._identity(
            "artifact:write", any_scope=("artifact:search", "artifact:read")
        )
        return self.consumer._save_internal(
            tenant_id=tenant_id, actor=actor, connection_id=connection_id, **kwargs
        )
