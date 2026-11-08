"""Response models for the JSON REST surface of the engine API.

One Pydantic v2 model per JSON REST handler in ``src/server.py``. Wired as
``response_model=`` on each route so the OpenAPI schema (and thus generated
SDKs) describes exactly what each endpoint returns.

Two model flavours:

- **Real models** declare every key the handler returns. FastAPI serializes
  the return value THROUGH the model, so for these the declared fields must
  cover the handler's output exactly - any undeclared key would be dropped.
- **TYPED-OPEN models** carry the known fields AND
  ``model_config = ConfigDict(extra="allow")``. They wrap opaque subsystem
  results (channel/asset/skill ops, the memory bundle) where extra keys vary
  at runtime and must survive serialization untouched.

Streaming routes (``/execute``, ``/execute/replay``, ``/channel/{id}/stream``)
and the OAuth browser redirect are NOT typed here: they do not return a JSON
body shaped by a model.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class OkResponse(BaseModel):
    """A bare ``{"ok": true}`` acknowledgement. Shared by every handler whose
    only success payload is the ok flag."""

    ok: bool


class OpaqueResponse(BaseModel):
    """Base for endpoints that pass an opaque subsystem result straight through.

    Carries no declared fields and ``extra="allow"`` so every runtime key
    survives serialization untouched. Each endpoint subclasses this for a
    distinct OpenAPI/SDK type name while sharing the one open-model config."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class ChannelSubscribeResponse(BaseModel):
    """POST /channel/subscribe - the new session id plus the ``as → channel_id``
    alias map for the subscribed read-query channels."""

    session_id: str
    channels: dict[str, str] = Field(default_factory=dict)


class ChannelActionResponse(OpaqueResponse):
    """POST /channel/action - opaque result of the write op + forced refresh
    (TYPED-OPEN: the channel manager's action payload varies by op)."""


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class PendingInput(BaseModel):
    """One block currently awaiting a block_input answer."""

    block_id: str
    format: str
    thread_id: str


class PendingInputsResponse(BaseModel):
    """GET /inputs/pending - the blocks waiting on an answer and their count."""

    pending: list[PendingInput] = Field(default_factory=list)
    count: int


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """GET /health (happy path) - boot-time subsystem status. The booting and
    not-operational branches return a raw JSONResponse and bypass this model."""

    status: str
    subsystems: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Any] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    boot_duration_ms: int


class HealthDeepResponse(BaseModel):
    """GET /health/deep (happy path) - live citadel reachability. The
    error/unreachable branches return a raw JSONResponse and bypass this model."""

    status: str
    citadel_url: str
    citadel_status: int
    latency_ms: int


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


class ServerEntry(BaseModel):
    """One catalog MCP server row (TYPED-OPEN: registry columns may evolve)."""

    model_config = ConfigDict(extra="allow")
    server_name: str
    display_name: str | None = None
    transport: str | None = None
    description: str | None = None
    auth_method: str | None = None
    category: str | None = None
    configured: bool = False


class ServersResponse(BaseModel):
    """GET /assets/servers - every catalog server with a configured flag."""

    servers: list[ServerEntry] = Field(default_factory=list)


class ConnectionEntry(BaseModel):
    """One active/inactive service connection (TYPED-OPEN: row columns may evolve)."""

    model_config = ConfigDict(extra="allow")
    connection_id: str
    server_name: str
    status: str | None = None
    enabled: bool = True  # per-asset pause: false = ACTIVE but excluded from use
    label: str | None = None  # product-set human name for this connection
    account_label: str | None = None  # human identifier for the account
    created_at: Any | None = None


class ConnectionsResponse(BaseModel):
    """GET /assets/connections - the user's service connections."""

    connections: list[ConnectionEntry] = Field(default_factory=list)


class ConnectResponse(BaseModel):
    """POST /assets/connect - opaque connect result. Carries connection_id +
    server_name + status, optionally oauth_url / error (TYPED-OPEN)."""

    model_config = ConfigDict(extra="allow")
    connection_id: str | None = None
    server_name: str | None = None
    status: str | None = None
    oauth_url: str | None = None
    error: str | None = None


class CallbackResponse(OpaqueResponse):
    """POST /assets/callback - opaque complete_connect result (TYPED-OPEN)."""


class DisconnectResponse(OpaqueResponse):
    """DELETE /assets/connections/{connection_id} - opaque disconnect result.
    Carries the mutation ack (TYPED-OPEN)."""


# ---------------------------------------------------------------------------
# Assets - control plane (request bodies)
# ---------------------------------------------------------------------------


class PermissionRuleModel(BaseModel):
    """One per-tool permission rule in a bulk/managed permission write.

    ``action_name`` may be a specific tool or the wildcard ``'*'`` (server-wide
    default). ``locked`` is managed policy and only takes effect for a managed
    setter; a non-managed setter passing locked=True is rejected downstream."""

    action_name: str = Field(..., max_length=256)
    behavior: str = Field(..., max_length=16)
    locked: bool = False
    # Per-tool on/off. None leaves the flag untouched; false hides the tool from
    # the agent entirely (distinct from behavior 'deny').
    enabled: bool | None = None


class ProvisionRequest(BaseModel):
    """POST /assets/provision - a product-brokered managed connection the engine
    takes custody of. Idempotent on ``idempotency_key``."""

    server_name: str = Field(..., max_length=256)
    provenance: str = Field(default="managed", max_length=32)
    owner_ref: str | None = Field(default=None, max_length=256)
    account_ref: str | None = Field(default=None, max_length=256)
    idempotency_key: str | None = Field(default=None, max_length=256)
    server_url: str | None = Field(default=None, max_length=1000)
    auth_method: str = Field(default="oauth", max_length=50)
    scopes: list[str] = Field(default_factory=list)
    credentials: dict[str, Any] = Field(default_factory=dict)
    oauth_refresh: dict[str, Any] | None = None
    # Cross-App Access (SEP-990): when set, the engine self-mints the token
    # via RFC 8693 + RFC 7523 instead of taking product-brokered credentials.
    # Keys: idp_token_endpoint, id_token, idp_client_id, idp_client_secret?,
    # client_id, client_secret (client registered at the MCP server's AS).
    xaa: dict[str, Any] | None = None
    permissions: list[PermissionRuleModel] = Field(default_factory=list)
    # Human-facing naming for the product set (label = the display name for
    # this connection; account_label = the account identifier, e.g. an email).
    label: str | None = Field(default=None, max_length=256)
    account_label: str | None = Field(default=None, max_length=256)


class VetServerRequest(BaseModel):
    """Body for POST /assets/servers/{server_name}/vet.

    ``vetted`` is the decision itself; ``actor`` is recorded for the audit
    trail. There is deliberately no vetted field on any registration payload:
    a caller that could set it there would be certifying its own server.
    """

    vetted: bool = True
    actor: str = ""
    # Why. A rejection is a durable decision, so the reason is worth keeping
    # next to it; a later reviewer should not have to guess.
    reason: str = Field(default="", max_length=512)


class RegisterRequest(BaseModel):
    """POST /assets/register - register a custom/arbitrary MCP server (engine tier)."""

    server_url: str = Field(..., max_length=1000)
    display_name: str | None = Field(default=None, max_length=200)
    transport: str = Field(default="http", max_length=20)
    auth_method: str = Field(default="oauth", max_length=50)
    auto_auth: bool = False
    oauth_config: dict[str, Any] | None = None
    # Credential-delivery config (auth_method=api_key): how to present the key.
    # {"api_key": {"in": "header"|"query", "name": "X-API-Key", "scheme": "Bearer"?}}.
    # Absent = the default Authorization: Bearer.
    auth_config: dict[str, Any] | None = None


class SetActionPermissionRequest(BaseModel):
    """PUT /assets/connections/{id}/permissions/{action} - set one tool's behavior."""

    behavior: str = Field(..., max_length=16)
    locked: bool = False
    set_by: str = Field(default="user", max_length=32)
    # Per-tool on/off. None leaves the flag untouched.
    enabled: bool | None = None


class SetPermissionsRequest(BaseModel):
    """PUT /assets/connections/{id}/permissions - bulk set per-tool behaviors."""

    set_by: str = Field(default="user", max_length=32)
    rules: list[PermissionRuleModel] = Field(default_factory=list)


class ResolvePermissionRequest(BaseModel):
    """POST /assets/connections/{id}/permissions/resolve - resolve one action."""

    action_name: str = Field(..., max_length=256)
    read_only_hint: bool | None = None


class PermissionTargetModel(BaseModel):
    """Target selector for a managed permission apply (by provenance and/or server)."""

    provenance: str | None = Field(default=None, max_length=32)
    server_name: str | None = Field(default=None, max_length=256)


class ApplyPermissionsRequest(BaseModel):
    """POST /assets/permissions/apply - managed policy across many assets."""

    set_by: str = Field(default="managed", max_length=32)
    target: PermissionTargetModel = Field(default_factory=PermissionTargetModel)
    rules: list[PermissionRuleModel] = Field(default_factory=list)


class DesiredEntryModel(BaseModel):
    """One managed desired-state entry for POST /assets/reconcile (TYPED-OPEN)."""

    model_config = ConfigDict(extra="allow")
    server_name: str = Field(..., max_length=256)
    provenance: str = Field(default="managed", max_length=32)
    owner_ref: str | None = Field(default=None, max_length=256)
    account_ref: str | None = Field(default=None, max_length=256)
    idempotency_key: str | None = Field(default=None, max_length=256)
    server_url: str | None = Field(default=None, max_length=1000)
    auth_method: str = Field(default="oauth", max_length=50)
    scopes: list[str] = Field(default_factory=list)
    credentials: dict[str, Any] = Field(default_factory=dict)
    oauth_refresh: dict[str, Any] | None = None
    permissions: list[PermissionRuleModel] = Field(default_factory=list)
    disqualified: bool = False
    # Human-facing naming carried through reconcile onto the connection.
    label: str | None = Field(default=None, max_length=256)
    account_label: str | None = Field(default=None, max_length=256)


class ReconcileRequest(BaseModel):
    """POST /assets/reconcile - the product's full managed desired set."""

    desired: list[DesiredEntryModel] = Field(default_factory=list)


class DisconnectRequest(BaseModel):
    """DELETE /assets/connections/{id} optional body - a disconnect reason."""

    reason: str | None = Field(default=None, max_length=256)


# ---------------------------------------------------------------------------
# Assets - control plane (responses)
# ---------------------------------------------------------------------------


class AckResponse(BaseModel):
    """The mutation ack returned by every asset mutation (TYPED-OPEN)."""

    model_config = ConfigDict(extra="allow")
    connection_id: str | None = None
    server_name: str | None = None
    status: str | None = None
    provenance: str | None = None
    account_ref: str | None = None
    enabled: bool | None = None  # per-asset pause state (present on connection acks)
    label: str | None = None  # product-set human name for this connection
    account_label: str | None = None  # human identifier for the account
    applied_at: str | None = None
    etag: str | None = None


class ConnectionDetailResponse(OpaqueResponse):
    """GET /assets/connections/{id} - one connection record (TYPED-OPEN)."""

    label: str | None = None  # product-set human name for this connection
    account_label: str | None = None  # human identifier for the account


class ConnectionStatusResponse(BaseModel):
    """GET /assets/connections/{id}/status - auth/health/expiry snapshot (TYPED-OPEN)."""

    model_config = ConfigDict(extra="allow")
    connection_id: str
    server_name: str | None = None
    provenance: str | None = None
    status: str | None = None
    enabled: bool = True  # per-asset pause: false = ACTIVE but excluded from use
    label: str | None = None  # product-set human name for this connection
    account_label: str | None = None  # human identifier for the account
    needs_auth: bool = False
    oauth_url: str | None = None
    expires_at: str | None = None
    last_refresh_at: str | None = None
    health: str | None = None


class ServerRegisterResponse(BaseModel):
    """POST /assets/register - the registered server + whether auth is still needed."""

    model_config = ConfigDict(extra="allow")
    server_name: str
    registered: bool = True
    provenance: str = "engine"
    needs_auth: bool = False
    oauth_url: str | None = None
    # The INITIATED connection behind oauth_url (also on the connection_events log).
    connection_id: str | None = None
    # How the oauth_config was obtained: "configured" | "discovered" | None.
    auth_source: str | None = None
    # True when auto_auth stopped because the server has not been vetted yet.
    # An expected state for a new server, not an error: vet it, then retry.
    pending_review: bool = False
    # Human-readable reason when auto_auth could not produce an oauth_url.
    detail: str | None = None


class RegistryResponse(BaseModel):
    """GET /assets/registry - September first-party registry listing ([] when dormant)."""

    servers: list[dict[str, Any]] = Field(default_factory=list)


class ActionsResponse(BaseModel):
    """GET /assets/connections/{id}/actions - discovered actions + effective permission."""

    model_config = ConfigDict(extra="allow")
    connection_id: str
    server_name: str | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)


class RefreshActionsResponse(AckResponse):
    """POST /assets/connections/{id}/actions/refresh - ack + discovered action names."""


class PermissionsResponse(BaseModel):
    """GET /assets/connections/{id}/permissions - full effective + explicit map."""

    model_config = ConfigDict(extra="allow")
    connection_id: str
    server_name: str | None = None
    provenance: str | None = None
    wildcard: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)


class ActionPermissionResponse(BaseModel):
    """GET /assets/connections/{id}/permissions/{action} - one tool's resolution."""

    model_config = ConfigDict(extra="allow")
    connection_id: str
    action_name: str
    effective: str | None = None
    source: str | None = None
    explicit: str | None = None
    enabled: bool = True  # per-tool on/off (distinct from behavior 'deny')
    locked: bool = False
    set_by: str | None = None
    read_only_hint: bool = False


class SetPermissionsResponse(BaseModel):
    """PUT /assets/connections/{id}/permissions - the bulk write result."""

    model_config = ConfigDict(extra="allow")
    connection_id: str
    updated: int = 0
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    effective: list[dict[str, Any]] = Field(default_factory=list)
    etag: str | None = None


class ApplyPermissionsResponse(BaseModel):
    """POST /assets/permissions/apply - matched connection count + per-asset acks."""

    matched_connections: int = 0
    applied: list[dict[str, Any]] = Field(default_factory=list)


class ReconcileResponse(BaseModel):
    """POST /assets/reconcile - the converged diff + one ack per created/updated.

    ``failed`` carries one record per desired entry that could not be applied.
    It is non-empty only for a partial apply, and when it is, the drop sweep was
    deliberately skipped: an unrealized desired set cannot be used to decide
    which live connections are surplus.
    """

    applied: dict[str, Any] = Field(default_factory=dict)
    acks: list[dict[str, Any]] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)


class ExecutionsResponse(BaseModel):
    """GET /assets/executions - the execution log page + a forward cursor."""

    executions: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None


class ConnectionEventsResponse(BaseModel):
    """GET /assets/connection-events - the out-of-run connection state-change
    log page + a forward cursor. The durable pull complement to the in-run
    asset status block. Each event carries event_id, connection_id,
    server_name, status, needs_auth (bool), oauth_url, and created_at; the
    dict value type stays open so the row shape can evolve."""

    events: list[dict[str, Any]] = Field(default_factory=list)
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class MemorySearchResponse(BaseModel):
    """POST /memory/{knowledge,episodes,social}/search - the matched records."""

    results: list[Any] = Field(default_factory=list)


class MemoryExportResponse(OpaqueResponse):
    """GET /memory/export - the versioned memory bundle. TYPED-OPEN: the bundle
    body is the portability envelope and is passed through verbatim."""


class MemoryImportResponse(BaseModel):
    """POST /memory/import - the ok flag plus the opaque import result summary."""

    model_config = ConfigDict(extra="allow")
    ok: bool
    imported: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Soul
# ---------------------------------------------------------------------------


class SoulResponse(BaseModel):
    """GET /memory/soul - the core soul document and the evolved (expressed) traits."""

    core_soul: dict[str, Any] | None = None
    evolved_traits: list[Any] = Field(default_factory=list)


class SoulFullResponse(BaseModel):
    """GET /memory/soul/full - the core soul plus all traits (incl. unexpressed)."""

    core_soul: dict[str, Any] | None = None
    all_traits: list[Any] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

# POST /feedback returns the shared OkResponse ({"ok": true}).


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


class SkillSummary(BaseModel):
    """One row in the skills listing."""

    name: str
    source: str
    version: str
    description: str = ""
    category: str = ""
    has_prompt: bool = False
    has_scripts: bool = False


class SkillsResponse(BaseModel):
    """GET /skills - all available skills with source labels."""

    skills: list[SkillSummary] = Field(default_factory=list)


class SkillDetailResponse(OpaqueResponse):
    """GET /skills/{skill_name} - the skill dict, optionally with prompt_content
    and gotchas_content. TYPED-OPEN: the catalog skill shape varies by skill."""


class SkillCreateResponse(OpaqueResponse):
    """POST /skills/create - opaque create_user_skill result (TYPED-OPEN)."""


class SkillUpdateResponse(OpaqueResponse):
    """PUT /skills/{skill_name} - opaque update_user_skill result (TYPED-OPEN)."""


class SkillDeleteResponse(OpaqueResponse):
    """DELETE /skills/{skill_name} - opaque delete_user_skill result (TYPED-OPEN)."""


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class ReloadCoreSoulResponse(BaseModel):
    """POST /admin/reload-core-soul - the reload status and the new soul version."""

    status: str
    version: str


class LcTriggerResponse(BaseModel):
    """POST /admin/lc/trigger - the LC batch run's per-stage counts (names match
    ``LCPipelineResult`` in src/learning_centre/pipeline.py)."""

    ok: bool
    signals_processed: int
    episodes_created: int
    knowledge_written: int
    social_nodes_created: int
    social_edges_written: int
    errors: list[Any] = Field(default_factory=list)


class ReloadCatalogResponse(BaseModel):
    """POST /admin/reload-catalog - the reloaded skill and agent counts."""

    ok: bool
    skills: int
    agents: int


class InstallPendingKeyRequest(BaseModel):
    """POST /admin/install-pending-key - the new key's sha256 hex (the engine
    never sees plaintext) and how long both keys stay valid (bounded
    server-side by the rotation env-var bounds)."""

    new_hash: str
    overlap_seconds: int = 60


class InstallPendingKeyResponse(BaseModel):
    """POST /admin/install-pending-key - the rotation id and the monotonic-clock
    instant at which the old key stops being accepted."""

    rotation_id: str
    overlap_expires_at: float


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class ThreadEntry(BaseModel):
    """One persistent conversation thread the coordinator knows about."""

    thread_id: str
    turn_count: int
    last_activity: Any | None = None
    summary: str | None = None
    current_state: str | None = None
    restored_on_boot: bool = False
    pinned: bool = False


class ThreadListResponse(BaseModel):
    """GET /coordinator/threads - the boot timestamp plus every known thread."""

    boot_time: str
    count: int
    threads: list[ThreadEntry] = Field(default_factory=list)


class PinThreadResponse(BaseModel):
    """POST /coordinator/threads/{thread_id}/pin and /unpin - the ok flag, the
    thread id, and the flag's new value."""

    ok: bool
    thread_id: str
    pinned: bool


# ---------------------------------------------------------------------------
# Raven (telemetry)
# ---------------------------------------------------------------------------


class RavenTrackRequest(BaseModel):
    """POST /raven/track - a typed telemetry event as a dict carrying a ``kind`` key."""

    event: dict[str, Any]


class RavenTrackUntypedRequest(BaseModel):
    """POST /raven/track_untyped - an ad-hoc named event with optional metadata."""

    name: str
    metadata: dict[str, Any] | None = None


class RavenQueryRequest(BaseModel):
    """POST /raven/query - an aggregation over the telemetry data. ``window`` is
    a shorthand string (e.g. "5h") or an explicit range dict."""

    metric: str
    window: str | dict[str, Any] = "5h"
    group_by: list[str] | None = None
    filters: dict[str, Any] | None = None


class RavenQueryResponse(OpaqueResponse):
    """POST /raven/query - the rollup result (TYPED-OPEN: shape varies by metric
    and grouping)."""


class RavenEventsRequest(BaseModel):
    """POST /raven/events - read raw events newer than ``since``, optionally by kind."""

    since: float = 0.0
    kind: str | None = None
    limit: int = 100


class RavenEventsResponse(BaseModel):
    """POST /raven/events - the matched raw events, oldest first, plus the
    cursor a poller needs to continue. Each event dict stays opaque via the
    ``Any`` value type.

    ``next_since`` is what to send as ``since`` on the next poll;
    ``has_more`` says the cap cut this page short, so poll again immediately
    rather than waiting out the interval. A full page alone cannot say that,
    which is why a burst used to be silently unrecoverable."""

    events: list[dict[str, Any]] = Field(default_factory=list)
    has_more: bool = False
    next_since: float = 0.0


class RavenRetentionRequest(BaseModel):
    """POST /raven/run_retention_now - the TTL knobs for the manual retention pass."""

    raw_events_ttl_days: int = 30
    counters_ttl_days: int = 365


class RavenRetentionResponse(OpaqueResponse):
    """POST /raven/run_retention_now - the rollup/drop counts (TYPED-OPEN: the
    retention result summary may evolve)."""
