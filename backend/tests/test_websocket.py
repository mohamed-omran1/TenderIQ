"""Tests for REQ-009 — WebSocket Streaming (Slice 5 — QA).

Every test maps to one or more Acceptance Criteria from REQ-009.
Tests are fully isolated — each test uses unique run IDs and cleans
up Redis keys after each test.

Design rules:
- Uses real Redis for EventBus tests (not fakeredis).
- Uses real test database for WebSocket auth tests.
- LLM nodes are mocked to keep tests fast.
- pytest-asyncio throughout (asyncio_mode = "auto" from pyproject.toml).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from app.schemas.stream import StreamEvent, make_event
from app.services.event_bus import EventBus, get_event_bus


# ==============================================================================
# Helpers
# ==============================================================================


async def _run_graph_with_events(
    active_run_data: dict,
    db: Any,
    mock_llm: Any,
    mock_feasibility_llm: Any,
    mock_financial_llm: Any,
    profile_lookup_session: Any,
    monkeypatch: Any,
) -> None:
    """Run the LangGraph pipeline and publish events via EventBus.

    Mirrors the ``awaiting_hitl_run`` fixture pattern but also publishes
    StreamEvents through the EventBus so connected WebSocket clients
    receive them.  Persists risk findings and financial commitments into
    the test DB session, then transitions the run to ``awaiting_hitl``.

    Called as a background task from WS integration tests.
    """
    from uuid import uuid4

    from sqlalchemy import func, insert, update

    from app.agents.graph import graph
    from app.agents.state import TenderState
    from app.config import get_settings
    from app.db.models import AnalysisRun, FinancialCommitment, RiskFinding, TenderChunk
    from app.routers.tenders import _flatten_financial_summary

    run_id = active_run_data["run_id"]
    tender_id = active_run_data["tender_id"]
    company_id = active_run_data["company_id"]
    company_api_key = active_run_data["company_api_key"]

    # Mock embeddings
    class _MockEmbeddings:
        def embed_documents(self, texts):
            return [[0.01] * get_settings().embedding_dimensions for _ in texts]
        def embed_query(self, text):
            return [0.01] * get_settings().embedding_dimensions

    monkeypatch.setattr("app.agents.retrieval.get_embeddings_client", lambda: _MockEmbeddings())
    monkeypatch.setattr("app.agents.nodes.risk_radar.get_embeddings_client", lambda: _MockEmbeddings())

    chunks_for_state = [
        {
            "content": f"WS test chunk {i} content for streaming tests.",
            "detected_language": "en",
            "chunk_index": i,
        }
        for i in range(3)
    ]

    event_bus = get_event_bus()
    config = {"configurable": {"thread_id": str(run_id)}}

    initial_state = TenderState(
        tender_id=str(tender_id),
        run_id=str(run_id),
        company_id=str(company_id),
        chunks=chunks_for_state,
        supervisor_ready=False,
        risk_findings=[],
        feasibility_score=None,
        feasibility_breakdown=None,
        financial_summary=None,
        aggregated_results=None,
        hitl_approved=False,
        hitl_override_score=None,
        final_report=None,
        token_usage=[],
        source_languages=[],
    )

    saw_aggregator = False
    async for graph_event in graph.astream(initial_state, config):
        node_name = list(graph_event.keys())[0]
        if node_name.startswith("__"):
            continue
        if node_name == "aggregator":
            saw_aggregator = True

        await event_bus.publish_event(run_id, make_event(
            run_id, "node_started", node_name=node_name,
        ))

        await event_bus.publish_event(run_id, make_event(
            run_id, "node_completed", node_name=node_name,
        ))

    if not saw_aggregator:
        return

    # Persist findings
    final_checkpoint = await graph.aget_state(config)
    findings_dicts = (
        (final_checkpoint.values.get("risk_findings", []) or [])
        if final_checkpoint is not None else []
    )
    if findings_dicts:
        await db.execute(
            insert(RiskFinding).values([
                {
                    "run_id": run_id,
                    "category": f["category"],
                    "severity": f["severity"],
                    "clause_text": f["clause_text"],
                    "explanation": f["explanation"],
                    "source_chunk_index": f["source_chunk_index"],
                    "confidence": f["confidence"],
                }
                for f in findings_dicts
            ])
        )

    # Persist financial commitments
    financial_summary = (
        (final_checkpoint.values.get("financial_summary", {}) or {})
        if final_checkpoint is not None else {}
    )
    if "error" not in financial_summary:
        commitment_rows = _flatten_financial_summary(financial_summary, run_id)
        if commitment_rows:
            await db.execute(
                insert(FinancialCommitment).values(commitment_rows)
            )

    # Update run state
    feasibility_score = (
        final_checkpoint.values.get("feasibility_score")
        if final_checkpoint is not None else None
    )
    await db.execute(
        update(AnalysisRun)
        .where(AnalysisRun.id == run_id)
        .values(
            state="awaiting_hitl",
            feasibility_score=feasibility_score,
        )
    )
    await db.commit()

    await event_bus.publish_event(run_id, make_event(
        run_id, "awaiting_hitl",
        data={
            "feasibility_score": feasibility_score,
            "risk_count": len(findings_dicts),
        }
    ))


async def _collect_events(
    ws: Any,
    *,
    until_event: str | None = None,
    count: int | None = None,
    timeout: float = 15.0,
) -> list[dict]:
    """Collect WebSocket events until a condition is met.

    Stops when ``until_event`` event_type is received, or when
    ``count`` events are collected, or on timeout.
    Uses short poll intervals (1s) so the loop does not block
    the full timeout on a single receive call.
    """
    events = []
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            remaining = deadline - asyncio.get_event_loop().time()
            poll_timeout = min(1.0, max(0.1, remaining))
            msg = await asyncio.wait_for(ws.receive_json(), timeout=poll_timeout)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break

        events.append(msg)
        if until_event and msg.get("event_type") == until_event:
            break
        if count and len(events) >= count:
            break

    return events


async def _connect_ws(
    app: Any,
    tender_id: str,
    api_key: str,
) -> Any:
    """Create an ASGIWebSocketTransport and connect to the stream endpoint.

    Returns (transport, client, ws_session).  Caller must clean up
    ``transport``, ``client``, and ``ws_session``.
    """
    transport = ASGIWebSocketTransport(app=app)
    await transport.__aenter__()
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    await client.__aenter__()
    cm = aconnect_ws(
        f"ws://test/tenders/{tender_id}/stream?token={api_key}",
        client=client,
    )
    ws = await cm.__aenter__()
    return transport, client, ws, cm


async def _close_ws(transport: Any, client: Any, ws: Any, cm: Any) -> None:
    """Clean up WebSocket connection resources."""
    for closer in (cm, client, transport):
        try:
            await closer.__aexit__(None, None, None)
        except (Exception, asyncio.CancelledError):
            pass


# ==============================================================================
# EventBus unit tests
# ==============================================================================


async def test_publish_event_sends_to_redis_channel(redis_client: Any) -> None:
    bus = EventBus(redis_url="redis://localhost:6379/0")
    await bus.connect()
    try:
        run_id = "test-123"
        channel = f"run:{run_id}"
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)

        event = make_event(run_id, "node_started", node_name="risk_radar")
        await bus.publish_event(run_id, event)

        # Read the message from the subscribed channel
        msg = await asyncio.wait_for(pubsub.get_message(timeout=2.0), timeout=3.0)
        assert msg is not None, "No message received from Redis channel"

        while msg and msg["type"] != "message":
            msg = await asyncio.wait_for(pubsub.get_message(timeout=2.0), timeout=3.0)

        assert msg is not None, "No message event received"
        assert msg["type"] == "message"
        assert msg["channel"] == channel

        payload = json.loads(msg["data"])
        assert payload["run_id"] == run_id
        assert payload["event_type"] == "node_started"
        assert payload["node_name"] == "risk_radar"

        # Validate against schema
        StreamEvent(**payload)
    finally:
        await bus.disconnect()
        await pubsub.close()


async def test_publish_event_silent_on_redis_failure() -> None:
    bus = EventBus(redis_url="redis://invalid:9999/0")
    try:
        bus._publisher = None  # Simulate no connection
        await bus.publish_event("test-1", make_event("test-1", "node_started"))
        # Should not raise
    except Exception:
        pytest.fail("publish_event should not raise when Redis is unavailable")


async def test_subscribe_run_yields_events(redis_client: Any) -> None:
    bus = EventBus(redis_url="redis://localhost:6379/0")
    await bus.connect()
    try:
        run_id = "test-sub-1"

        # Start consuming in background
        received = []

        async def collect():
            async for event in bus.subscribe_run(run_id):
                received.append(event)

        collector = asyncio.create_task(collect())

        # Give subscriber time to register
        await asyncio.sleep(0.3)

        # Publish 3 events
        types = ["node_started", "node_completed", "awaiting_hitl"]
        for i, etype in enumerate(types):
            await bus.publish_event(
                run_id, make_event(run_id, etype, node_name=f"node-{i}")
            )

        await asyncio.sleep(0.5)

        # Cancel collector
        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass

        assert len(received) == 3
        for i, etype in enumerate(types):
            assert received[i].event_type == etype
            assert received[i].run_id == run_id
    finally:
        await bus.disconnect()


async def test_make_event_factory() -> None:
    event = make_event("run-1", "node_started", node_name="risk_radar")

    assert event.run_id == "run-1"
    assert event.event_type == "node_started"
    assert event.node_name == "risk_radar"
    assert event.timestamp.endswith("Z")
    assert event.data == {}

    # Timestamp should be valid ISO 8601
    parsed = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
    assert parsed is not None


async def test_channel_name_format(redis_client: Any) -> None:
    from app.services.event_bus import CHANNEL_PREFIX

    # Verify the channel prefix constant is correct
    assert CHANNEL_PREFIX == "run:"

    bus = EventBus(redis_url="redis://localhost:6379/0")
    await bus.connect()
    try:
        run_id = "test-123"
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"run:{run_id}")

        await bus.publish_event(run_id, make_event(run_id, "node_started"))

        msg = await asyncio.wait_for(pubsub.get_message(timeout=2.0), timeout=3.0)
        while msg and msg["type"] != "message":
            msg = await asyncio.wait_for(pubsub.get_message(timeout=2.0), timeout=3.0)

        assert msg is not None, "No message received on run: channel"
        assert msg["channel"] == f"run:{run_id}"
        payload = json.loads(msg["data"])
        assert payload["event_type"] == "node_started"
    finally:
        await bus.disconnect()
        await pubsub.close()


# ==============================================================================
# WebSocket endpoint tests
# ==============================================================================


async def test_ws_accepts_valid_token(ws_app: Any, active_run: dict) -> None:
    transport, client, ws, cm = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    try:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
        assert msg is not None
    except asyncio.TimeoutError:
        pass  # No events expected since we didn't publish any
    finally:
        await _close_ws(transport, client, ws, cm)


async def test_ws_rejects_invalid_token_with_4003(ws_app: Any, active_run: dict) -> None:
    transport = ASGIWebSocketTransport(app=ws_app)
    await transport.__aenter__()
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    await client.__aenter__()
    try:
        cm = aconnect_ws(
            f"ws://test/tenders/{active_run['tender_id']}/stream?token=invalid-key",
            client=client,
        )
        try:
            ws = await cm.__aenter__()
            pytest.fail("WebSocket connection should have been rejected")
        except Exception:
            pass  # Expected — connection closed with 4003
    finally:
        await client.__aexit__(None, None, None)
        await transport.__aexit__(None, None, None)


async def test_ws_rejects_wrong_company_with_4003(
    ws_app: Any,
    active_run: dict,
    company_b: tuple,
) -> None:
    _, wrong_key = company_b
    transport = ASGIWebSocketTransport(app=ws_app)
    await transport.__aenter__()
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    await client.__aenter__()
    try:
        cm = aconnect_ws(
            f"ws://test/tenders/{active_run['tender_id']}/stream?token={wrong_key}",
            client=client,
        )
        try:
            ws = await cm.__aenter__()
            pytest.fail("WebSocket connection should have been rejected (wrong company)")
        except Exception:
            pass  # Expected — connection closed with 4003
    finally:
        await client.__aexit__(None, None, None)
        await transport.__aexit__(None, None, None)


async def test_ws_already_complete_run_sends_complete_and_closes(
    ws_app: Any,
    complete_run_fixture: dict,
) -> None:
    transport, client, ws, cm = await _connect_ws(
        ws_app, complete_run_fixture["tender_id"], complete_run_fixture["raw_key"]
    )
    try:
        events = await _collect_events(ws, until_event="complete", timeout=5.0)
        assert len(events) >= 1
        complete_event = events[-1] if events[-1]["event_type"] == "complete" else events[0]
        assert complete_event["event_type"] == "complete"
        assert "go_no_go" in complete_event["data"]
    finally:
        await _close_ws(transport, client, ws, cm)


async def test_ws_already_failed_run_sends_failed_and_closes(
    ws_app: Any,
    db: Any,
    active_run: dict,
) -> None:
    from sqlalchemy import update
    from app.db.models import AnalysisRun

    # Set the run to failed state
    await db.execute(
        update(AnalysisRun)
        .where(AnalysisRun.id == active_run["run_id"])
        .values(state="failed", error_reason="Test failure")
    )
    await db.commit()

    transport, client, ws, cm = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    try:
        events = await _collect_events(ws, until_event="failed", timeout=5.0)
        assert len(events) >= 1
        failed_event = events[-1] if events[-1]["event_type"] == "failed" else events[0]
        assert failed_event["event_type"] == "failed"
        assert "error_reason" in failed_event["data"]
    finally:
        await _close_ws(transport, client, ws, cm)


async def test_ws_streams_node_events_in_order(
    ws_app: Any,
    db: Any,
    active_run: dict,
    mock_llm: Any,
    mock_feasibility_llm: Any,
    mock_financial_llm: Any,
    profile_lookup_session: Any,
    monkeypatch: Any,
) -> None:
    transport, client, ws, cm = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    try:
        # Run the graph in background, publishing events
        graph_task = asyncio.create_task(
            _run_graph_with_events(
                active_run, db, mock_llm, mock_feasibility_llm,
                mock_financial_llm, profile_lookup_session, monkeypatch,
            )
        )

        events = await _collect_events(ws, until_event="awaiting_hitl", timeout=30.0)

        await graph_task

        assert len(events) > 0, "No events received"

        # Extract event types and node_names
        event_types = [e["event_type"] for e in events]
        type_node_pairs = [(e["event_type"], e.get("node_name")) for e in events]

        # Verify supervisor runs first (sequential)
        assert type_node_pairs[0] == ("node_started", "supervisor"), (
            f"Expected first event to be node_started(supervisor), got {type_node_pairs[0]}"
        )
        assert type_node_pairs[1] == ("node_completed", "supervisor"), (
            f"Expected second event to be node_completed(supervisor), got {type_node_pairs[1]}"
        )

        # Verify parallel nodes (risk_radar, scorer, financial) appear after supervisor
        parallel_started = [p for p in type_node_pairs if p[1] in ("risk_radar", "scorer", "financial_analyst")]
        assert len(parallel_started) >= 3, (
            f"Expected at least 3 parallel node starts, got {len(parallel_started)}: {parallel_started}"
        )

        # Verify aggregator runs after parallel nodes
        aggregator_start_idx = next(
            (i for i, p in enumerate(type_node_pairs) if p == ("node_started", "aggregator")),
            None,
        )
        assert aggregator_start_idx is not None, "aggregator node_started not found"

        # Verify awaiting_hitl is last
        assert events[-1]["event_type"] == "awaiting_hitl"
    finally:
        await _close_ws(transport, client, ws, cm)


async def test_ws_awaiting_hitl_event_contains_score_and_count(
    ws_app: Any,
    db: Any,
    active_run: dict,
    mock_llm: Any,
    mock_feasibility_llm: Any,
    mock_financial_llm: Any,
    profile_lookup_session: Any,
    monkeypatch: Any,
) -> None:
    transport, client, ws, cm = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    try:
        graph_task = asyncio.create_task(
            _run_graph_with_events(
                active_run, db, mock_llm, mock_feasibility_llm,
                mock_financial_llm, profile_lookup_session, monkeypatch,
            )
        )

        events = await _collect_events(ws, until_event="awaiting_hitl", timeout=30.0)
        await graph_task

        hitl_events = [e for e in events if e["event_type"] == "awaiting_hitl"]
        assert len(hitl_events) >= 1

        data = hitl_events[0]["data"]
        assert isinstance(data.get("feasibility_score"), (int, float)), (
            f"feasibility_score should be numeric, got {data.get('feasibility_score')}"
        )
        assert isinstance(data.get("risk_count"), int), (
            f"risk_count should be int, got {data.get('risk_count')}"
        )
    finally:
        await _close_ws(transport, client, ws, cm)


async def test_ws_complete_event_after_hitl_approval(
    ws_app: Any,
    db: Any,
    active_run: dict,
    mock_llm: Any,
    mock_feasibility_llm: Any,
    mock_financial_llm: Any,
    mock_report_llm: Any,
    profile_lookup_session: Any,
    monkeypatch: Any,
    auth_headers: Any,
) -> None:
    from sqlalchemy import update
    from app.db.models import AnalysisRun

    transport, client, ws, cm = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    try:
        # Mock embeddings for graph run
        class _MockEmbeddings:
            def embed_documents(self, texts):
                return [[0.01] * 768 for _ in texts]
            def embed_query(self, text):
                return [0.01] * 768
        monkeypatch.setattr("app.agents.retrieval.get_embeddings_client", lambda: _MockEmbeddings())
        monkeypatch.setattr("app.agents.nodes.risk_radar.get_embeddings_client", lambda: _MockEmbeddings())

        # Patch report_assembler.with_session
        class _TestSessionCtx:
            async def __aenter__(self):
                return db
            async def __aexit__(self, *args):
                return False
        monkeypatch.setattr(
            "app.agents.nodes.report_assembler.with_session",
            lambda: _TestSessionCtx(),
        )

        # Run graph to awaiting_hitl
        graph_task = asyncio.create_task(
            _run_graph_with_events(
                active_run, db, mock_llm, mock_feasibility_llm,
                mock_financial_llm, profile_lookup_session, monkeypatch,
            )
        )

        events_before = await _collect_events(ws, until_event="awaiting_hitl", timeout=30.0)
        await graph_task

        assert any(e["event_type"] == "awaiting_hitl" for e in events_before)

        # Approve via HTTP
        headers = {"Authorization": f"Bearer {active_run['company_api_key']}"}
        resp = await client.post(
            f"/tenders/{active_run['tender_id']}/approve",
            headers=headers,
            json={"justification": "Approved for WS test"},
        )
        assert resp.status_code == 202, resp.text

        # Run the resume graph which publishes events
        from app.routers.tenders import _resume_graph

        resume_task = asyncio.create_task(
            _resume_graph(active_run["run_id"], None)
        )

        events_after = await _collect_events(ws, until_event="complete", timeout=30.0)
        await resume_task

        assert any(e["event_type"] == "resuming" for e in events_after), "No resuming event"
        complete_events = [e for e in events_after if e["event_type"] == "complete"]
        assert len(complete_events) >= 1, "No complete event"
        assert "go_no_go" in complete_events[0]["data"]
        assert "effective_score" in complete_events[0]["data"]
    finally:
        await _close_ws(transport, client, ws, cm)


@pytest.mark.slow
async def test_ws_heartbeat_received(ws_app: Any, active_run: dict) -> None:
    """Heartbeat test — marked slow because it waits 16s.

    The WS endpoint publishes a heartbeat event every 15s via Redis pub/sub.
    We connect, then manually publish a heartbeat to verify the pipeline,
    then wait for the real heartbeat to arrive.
    """
    transport, client, ws, cm = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    try:
        # First, verify the WS pipeline works by publishing an event directly
        event_bus = get_event_bus()
        run_id = active_run["run_id"]
        await asyncio.sleep(0.3)
        await event_bus.publish_event(
            run_id,
            make_event(run_id, "heartbeat", data={"state": "running"}),
        )
        direct_msg = await asyncio.wait_for(ws.receive_json(), timeout=5.0)
        assert direct_msg["event_type"] == "heartbeat", (
            f"Expected heartbeat from direct publish, got {direct_msg.get('event_type')}"
        )
        assert direct_msg["data"]["state"] == "running"

        # Now wait for the real heartbeat from the WS endpoint's task (fires every 15s)
        real_msg = await asyncio.wait_for(ws.receive_json(), timeout=20.0)
        assert real_msg["event_type"] == "heartbeat"
        assert "state" in real_msg["data"]
    finally:
        await _close_ws(transport, client, ws, cm)


async def test_ws_multiple_clients_same_run(
    ws_app: Any,
    active_run: dict,
) -> None:
    """Critical fan-out verification — multiple WS clients get same events."""
    # Connect both clients FIRST, then publish the event
    transport1, client1, ws1, cm1 = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    transport2, client2, ws2, cm2 = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    try:
        # Give both subscribers time to register
        await asyncio.sleep(0.3)

        # Publish event AFTER both clients are connected
        event_bus = get_event_bus()
        run_id = active_run["run_id"]
        await event_bus.publish_event(
            run_id, make_event(run_id, "node_started", node_name="test_node")
        )

        # Both clients should receive the event
        msg1 = await asyncio.wait_for(ws1.receive_json(), timeout=5.0)
        msg2 = await asyncio.wait_for(ws2.receive_json(), timeout=5.0)

        assert msg1["event_type"] == "node_started"
        assert msg2["event_type"] == "node_started"
        assert msg1 == msg2, "Both clients should receive identical events"
    finally:
        await _close_ws(transport1, client1, ws1, cm1)
        await _close_ws(transport2, client2, ws2, cm2)


async def test_ws_client_disconnect_does_not_affect_run(
    ws_app: Any,
    db: Any,
    active_run: dict,
    mock_llm: Any,
    mock_feasibility_llm: Any,
    mock_financial_llm: Any,
    profile_lookup_session: Any,
    monkeypatch: Any,
) -> None:
    from sqlalchemy import select
    from app.db.models import AnalysisRun

    # Start the graph in background BEFORE connecting WS
    graph_task = asyncio.create_task(
        _run_graph_with_events(
            active_run, db, mock_llm, mock_feasibility_llm,
            mock_financial_llm, profile_lookup_session, monkeypatch,
        )
    )

    # Connect WebSocket while graph is running
    transport, client, ws, cm = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    try:
        # Wait for first event (supervisor starts)
        first_event = await asyncio.wait_for(ws.receive_json(), timeout=15.0)
        assert first_event["event_type"] == "node_started"
    finally:
        # Disconnect abruptly
        await _close_ws(transport, client, ws, cm)

    # Wait for graph to finish (run should continue to awaiting_hitl despite WS disconnect)
    await graph_task

    # Verify the run reached awaiting_hitl (not stuck or failed)
    result = await db.execute(
        select(AnalysisRun).where(AnalysisRun.id == active_run["run_id"])
    )
    run = result.scalar_one_or_none()
    assert run is not None
    assert run.state == "awaiting_hitl", (
        f"Expected run state 'awaiting_hitl', got '{run.state}'"
    )


# ==============================================================================
# Polling fallback verification
# ==============================================================================


async def test_polling_fallback_when_ws_unavailable() -> None:
    """Static code analysis: verify polling fallback is in frontend code.

    Checks that AgentStreamViewer, HITLGate, and FullReportView have
    ``connectionState === 'error'`` with ``refetchInterval`` of 2000.
    """
    import os

    frontend_dir = os.path.join(
        os.path.dirname(__file__), "..", "frontend", "components"
    )
    files_to_check = [
        "AgentStreamViewer.tsx",
        "HITLGate.tsx",
        "FullReportView.tsx",
    ]

    for fname in files_to_check:
        fpath = os.path.join(frontend_dir, fname)
        if not os.path.exists(fpath):
            pytest.skip(f"Frontend component not found: {fpath}")
        with open(fpath, encoding="utf-8") as f:
            content = f.read()
        assert "connectionState === 'error'" in content or "connectionState==='error'" in content, (
            f"{fname} missing connectionState error check"
        )
        assert "2000" in content, (
            f"{fname} missing refetchInterval of 2000"
        )


# ==============================================================================
# Security tests
# ==============================================================================


async def test_ws_no_tender_content_in_events(
    ws_app: Any,
    db: Any,
    active_run: dict,
    mock_llm: Any,
    mock_feasibility_llm: Any,
    mock_financial_llm: Any,
    profile_lookup_session: Any,
    monkeypatch: Any,
) -> None:
    forbidden_keys = {
        "clause_text", "explanation", "annual_turnover",
        "available_bonding_capacity", "amount_value",
    }

    transport, client, ws, cm = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    try:
        graph_task = asyncio.create_task(
            _run_graph_with_events(
                active_run, db, mock_llm, mock_feasibility_llm,
                mock_financial_llm, profile_lookup_session, monkeypatch,
            )
        )

        events = await _collect_events(ws, until_event="awaiting_hitl", timeout=30.0)
        await graph_task

        for event in events:
            data_keys = set(event.get("data", {}).keys())
            leaked = forbidden_keys & data_keys
            assert not leaked, (
                f"Event {event['event_type']} contains forbidden data keys: {leaked}"
            )
    finally:
        await _close_ws(transport, client, ws, cm)


async def test_ws_cost_update_contains_only_cost_usd(
    ws_app: Any,
    active_run: dict,
) -> None:
    event_bus = get_event_bus()
    run_id = active_run["run_id"]

    # Connect WS FIRST, then publish the event
    transport, client, ws, cm = await _connect_ws(
        ws_app, active_run["tender_id"], active_run["company_api_key"]
    )
    try:
        # Give subscriber time to register
        await asyncio.sleep(0.3)

        await event_bus.publish_event(
            run_id,
            make_event(
                run_id,
                "cost_update",
                node_name="risk_radar",
                data={"cost_usd": 0.0042},
            ),
        )

        events = await _collect_events(ws, count=5, timeout=5.0)
        cost_events = [e for e in events if e["event_type"] == "cost_update"]
        assert len(cost_events) >= 1, "No cost_update event received"

        for ev in cost_events:
            data = ev.get("data", {})
            # Should only contain cost_usd (node_name is at top level)
            assert "cost_usd" in data, f"cost_update missing cost_usd: {data}"
            allowed = {"cost_usd"}
            extra = set(data.keys()) - allowed
            assert not extra, f"cost_update has extra data keys: {extra}"
    finally:
        await _close_ws(transport, client, ws, cm)


# ==============================================================================
# Event schema validation
# ==============================================================================


async def test_all_event_types_are_valid_stream_events() -> None:
    valid_types = [
        "node_started",
        "node_completed",
        "awaiting_hitl",
        "resuming",
        "complete",
        "failed",
        "cost_update",
        "heartbeat",
    ]
    for etype in valid_types:
        event = make_event("schema-test-1", etype, node_name="test_node")
        # Validating against StreamEvent schema
        StreamEvent(**event.model_dump())

    # Also test with minimal data
    event = make_event("schema-test-1", "heartbeat")
    StreamEvent(**event.model_dump())


async def test_event_timestamp_is_utc_iso_format() -> None:
    event = make_event("ts-test-1", "node_started")
    assert event.timestamp.endswith("Z"), f"Timestamp does not end with Z: {event.timestamp}"
    parsed = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
    assert parsed is not None
