"""AgentCore Runtime application and Quorum's public HTTP surfaces."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Annotated, Protocol

from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.runtime import RequestContext
from pydantic import Field, TypeAdapter, ValidationError, model_validator
from sqlalchemy import Engine
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles
from strands.types.interrupt import InterruptResponseContent

from quorum.database import DatabaseLedger, DatabaseSettings, create_database_engine
from quorum.decision_graph import ACTION_REQUEST_STATE_KEY, build_decision_graph
from quorum.decision_store import DecisionPolicyStore
from quorum.execution import (
    ActionExecutionService,
    ExecutionConflictError,
    UndoTokenError,
    build_action_execution_service,
)
from quorum.gateway import gateway_executor_tools
from quorum.memory import AgentCoreMemorySettings, build_memory_session_manager
from quorum.models import (
    ActionRequest,
    CanonicalMessageEvent,
    DataClassification,
    OpaqueId,
    StrictModel,
)
from quorum.observability import (
    configure_strands_trace_redaction,
    safe_trace_attributes,
    traced_operation,
)
from quorum.orchestration import (
    BedrockSettings,
    OnlineConfigurationError,
    build_bedrock_model,
    build_ledger_graph,
    process_event_async,
)
from quorum.replay import ReplayStore
from quorum.slack import build_slack_notifier
from quorum.slack_ingress import SlackEventConverter, SlackIngressError, SlackSignatureVerifier

DEMO_ASSET_DIRECTORY = Path(__file__).with_name("demo")
RuntimeSessionId = Annotated[
    str, Field(min_length=33, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
]
_SESSION_ID_ADAPTER = TypeAdapter(RuntimeSessionId)


class RuntimeConfigurationError(ValueError):
    pass


class RuntimeInterruptResponse(StrictModel):
    interrupt_id: Annotated[str, Field(min_length=1, max_length=300)]
    response: Annotated[str, Field(min_length=1, max_length=30)]


class RuntimeInvocation(StrictModel):
    organization_id: OpaqueId
    prompt: Annotated[str, Field(min_length=1, max_length=8000)]
    data_classification: DataClassification
    action_request: ActionRequest
    interrupt_responses: list[RuntimeInterruptResponse] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def require_matching_organization(self) -> RuntimeInvocation:
        if self.action_request.organization_id != self.organization_id:
            raise ValueError("action_request organization does not match invocation")
        return self


class SlackProcessor(Protocol):
    async def __call__(self, event: CanonicalMessageEvent) -> dict[str, object]: ...


class RuntimeInvoker(Protocol):
    async def __call__(
        self, payload: RuntimeInvocation, session_id: RuntimeSessionId
    ) -> dict[str, object]: ...


class UndoExecutor(Protocol):
    def undo(self, token: str) -> object: ...


class ProductionRuntimeInvoker:
    """Build a tenant-scoped five-node Graph for one AgentCore Runtime session."""

    def __init__(self, engine: Engine) -> None:
        self._store = DecisionPolicyStore(engine)

    async def __call__(
        self, payload: RuntimeInvocation, session_id: RuntimeSessionId
    ) -> dict[str, object]:
        region = BedrockSettings.from_environment().region_name
        gateway_url = os.environ.get("QUORUM_AGENTCORE_GATEWAY_URL", "").strip()
        if not gateway_url:
            raise RuntimeConfigurationError("QUORUM_AGENTCORE_GATEWAY_URL is required")
        memory_manager = build_memory_session_manager(
            AgentCoreMemorySettings.from_environment(),
            organization_id=payload.organization_id,
            session_id=session_id,
        )
        attributes = safe_trace_attributes(
            {
                "quorum.organization_id": payload.organization_id,
                "quorum.session_id": session_id,
                "quorum.data_classification": payload.data_classification.value,
                "quorum.action_id": payload.action_request.action_id,
            }
        )
        try:
            with gateway_executor_tools(endpoint=gateway_url, region_name=region) as tools:
                graph = build_decision_graph(
                    self._store,
                    model=build_bedrock_model(BedrockSettings.from_environment()),
                    executor_tools=list(tools),
                    approval_notifier=build_slack_notifier(),
                    session_manager=memory_manager,
                    trace_attributes=attributes,
                )
                task: str | list[InterruptResponseContent] = payload.prompt
                if payload.interrupt_responses:
                    task = [
                        {
                            "interruptResponse": {
                                "interruptId": item.interrupt_id,
                                "response": item.response,
                            }
                        }
                        for item in payload.interrupt_responses
                    ]
                with traced_operation("quorum.runtime.invoke", attributes):
                    result = await graph.invoke_async(
                        task,
                        invocation_state={ACTION_REQUEST_STATE_KEY: payload.action_request},
                    )
        finally:
            memory_manager.close()
        return {
            "session_id": session_id,
            "status": result.status.value,
            "execution_order": [node.node_id for node in result.execution_order],
            "interrupts": [interrupt.to_dict() for interrupt in result.interrupts],
            "usage": dict(result.accumulated_usage),
        }


class LazyUndoExecutor:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._service: ActionExecutionService | None = None

    def undo(self, token: str) -> object:
        if self._service is None:
            self._service = build_action_execution_service(self._engine)
        return self._service.undo(token)


class ProductionSlackProcessor:
    """Process a verified, redacted event after Slack has received its acknowledgement."""

    def __init__(self, engine: Engine) -> None:
        self._ledger = DatabaseLedger(engine)

    async def __call__(self, event: CanonicalMessageEvent) -> dict[str, object]:
        session_manager = build_memory_session_manager(
            AgentCoreMemorySettings.from_environment(),
            organization_id=event.organization_id,
            session_id=event.message_id,
        )
        attributes = safe_trace_attributes(
            {
                "quorum.organization_id": event.organization_id,
                "quorum.session_id": event.message_id,
                "quorum.data_classification": event.data_classification.value,
                "quorum.graph_node": "listener",
            }
        )
        try:
            graph = build_ledger_graph(
                model=build_bedrock_model(BedrockSettings.from_environment()),
                session_manager=session_manager,
                trace_attributes=attributes,
            )
            changes = await process_event_async(graph, event, self._ledger)
            return {
                "message_id": event.message_id,
                "upserted_count": len(changes.upserted),
                "rejected_count": len(changes.rejected),
                "duplicate_event": changes.duplicate_event,
            }
        finally:
            session_manager.close()


def create_app(
    *,
    runtime_invoker: RuntimeInvoker | None = None,
    slack_processor: SlackProcessor | None = None,
    undo_executor: UndoExecutor | None = None,
    replay_store: ReplayStore | None = None,
    slack_signing_secret: bytes | None = None,
    slack_pseudonym_key: bytes | None = None,
    engine: Engine | None = None,
) -> BedrockAgentCoreApp:
    """Build one AgentCore-compatible app without requiring cloud credentials at import time."""

    configure_strands_trace_redaction()
    active_engine = engine or create_database_engine(DatabaseSettings.from_environment())
    invoker = runtime_invoker or ProductionRuntimeInvoker(active_engine)
    active_slack_processor = slack_processor or ProductionSlackProcessor(active_engine)
    undoer = undo_executor or LazyUndoExecutor(active_engine)
    replays = replay_store or ReplayStore()
    signing_secret = slack_signing_secret or _secret_from_environment("QUORUM_SLACK_SIGNING_SECRET")
    pseudonym_key = slack_pseudonym_key or _secret_from_environment("QUORUM_SLACK_PSEUDONYM_KEY")
    verifier = SlackSignatureVerifier(signing_secret) if signing_secret is not None else None
    converter = SlackEventConverter(pseudonym_key) if pseudonym_key is not None else None
    app = BedrockAgentCoreApp()

    @app.entrypoint
    async def invoke(raw: object, context: RequestContext) -> dict[str, object] | JSONResponse:
        try:
            payload = RuntimeInvocation.model_validate(raw)
            session_id = _validated_session_id(context.session_id)
            return await invoker(payload, session_id)
        except ValidationError:
            return JSONResponse({"error": "invalid invocation payload"}, status_code=422)
        except (OnlineConfigurationError, RuntimeConfigurationError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    async def index(_request: Request) -> Response:
        return FileResponse(DEMO_ASSET_DIRECTORY / "index.html", media_type="text/html")

    async def favicon(_request: Request) -> Response:
        return FileResponse(DEMO_ASSET_DIRECTORY / "favicon.svg", media_type="image/svg+xml")

    async def start_replay(_request: Request) -> Response:
        snapshot = replays.start()
        with traced_operation(
            "quorum.demo.replay",
            {
                "quorum.replay_id": snapshot.replay_id,
                "quorum.data_classification": snapshot.data_classification,
                "quorum.interrupt_count": snapshot.quorum.interruption_count,
            },
        ):
            return JSONResponse(snapshot.model_dump(mode="json"))

    async def get_metrics(request: Request) -> Response:
        replay_id = request.path_params["replay_id"]
        snapshot = replays.get(replay_id)
        if snapshot is None:
            return JSONResponse({"error": "replay not found"}, status_code=404)
        return JSONResponse(snapshot.model_dump(mode="json"))

    async def slack_events(request: Request) -> Response:
        if verifier is None or converter is None:
            return JSONResponse({"error": "Slack ingress is not configured"}, status_code=503)
        body = await request.body()
        try:
            verifier.verify(
                body,
                request.headers.get("x-slack-request-timestamp"),
                request.headers.get("x-slack-signature"),
            )
            payload = converter.parse(body)
            challenge = converter.challenge(payload)
            if challenge is not None:
                return JSONResponse({"challenge": challenge})
            event = converter.to_canonical(payload)
            if event is None:
                return JSONResponse({"accepted": False, "reason": "ignored_event"})
            if slack_processor is None:
                try:
                    BedrockSettings.from_environment()
                except OnlineConfigurationError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=503)
            return JSONResponse(
                {"accepted": True, "event_id": event.message_id},
                background=BackgroundTask(active_slack_processor, event),
            )
        except SlackIngressError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)

    async def confirm_undo(request: Request) -> Response:
        token = request.query_params.get("token", "")
        if not token:
            return JSONResponse({"error": "undo token is required"}, status_code=400)
        safe_token = html.escape(token, quote=True)
        return HTMLResponse(
            "<!doctype html><html lang='en'><meta name='viewport' content='width=device-width'>"
            "<title>Confirm undo · Quorum</title><body>"
            "<main><h1>Undo this Quorum action?</h1>"
            "<p>The signed link can be used once and expires after 24 hours.</p>"
            "<form method='post' action='/actions/undo'>"
            f"<input type='hidden' name='token' value='{safe_token}'>"
            "<button type='submit'>Undo action</button></form></main></body></html>",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def perform_undo(request: Request) -> Response:
        form = await request.form()
        token = form.get("token")
        if not isinstance(token, str) or not token:
            return JSONResponse({"error": "undo token is required"}, status_code=400)
        try:
            result = undoer.undo(token)
        except UndoTokenError:
            return JSONResponse({"error": "undo token is invalid or unavailable"}, status_code=410)
        except ExecutionConflictError:
            return JSONResponse({"error": "action cannot be undone"}, status_code=409)
        model_dump = getattr(result, "model_dump", None)
        payload = model_dump(mode="json") if callable(model_dump) else result
        return JSONResponse(payload)

    app.add_route("/", index, methods=["GET"], include_in_schema=False)
    app.add_route("/favicon.svg", favicon, methods=["GET"], include_in_schema=False)
    app.mount("/assets", StaticFiles(directory=DEMO_ASSET_DIRECTORY), name="assets")
    app.add_route("/demo/replays/synthetic-week", start_replay, methods=["POST"])
    app.add_route("/demo/metrics/{replay_id}", get_metrics, methods=["GET"])
    app.add_route("/integrations/slack/events", slack_events, methods=["POST"])
    app.add_route("/actions/undo", confirm_undo, methods=["GET"])
    app.add_route("/actions/undo", perform_undo, methods=["POST"])
    return app


def _validated_session_id(value: str | None) -> RuntimeSessionId:
    if value is None:
        raise RuntimeConfigurationError("AgentCore Runtime session ID is required")
    try:
        return _SESSION_ID_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise RuntimeConfigurationError("AgentCore Runtime session ID is invalid") from exc


def _secret_from_environment(name: str) -> bytes | None:
    value = os.environ.get(name, "")
    return value.encode() if value else None


app = create_app()


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
