# Copyright 2025 Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Daytona.get_or_create (sync) and AsyncDaytona.get_or_create (async).

Coverage
--------
1.  First call with no existing sandbox → ``create`` is called once.
2.  Second call with the same key finds the existing sandbox → ``create`` is NOT called.
3.  A matching sandbox in the STOPPED state is restarted before being returned.
4.  A matching sandbox in the ARCHIVED state is restarted before being returned.
5.  A matching sandbox in the STARTING/RESTORING state is waited on (not restarted).
6.  A matching sandbox in ERROR state → DaytonaError raised (recreate_on_error=False).
7.  A matching sandbox in ERROR state + recreate_on_error=True → deleted + recreated.
8.  Multiple label matches → most-recently-created returned + UserWarning emitted.
9.  Empty key (no name, no labels) → DaytonaValidationError.
10. Name-based path: sandbox found by name → returned without calling create.
11. Name-based path: sandbox not found → create is called.
12. Race safety (async): two concurrent calls with the same key are serialized by
    the per-key asyncio.Lock — only one creation occurs.
13. Sandboxes in DESTROYED / other terminal states are skipped; a fresh one is created.

Note on cross-process races
----------------------------
The per-key locks in both the sync (threading.Lock) and async (asyncio.Lock) clients
prevent double-creation within a single process.  Across multiple OS processes no
shared primitive exists; a race can produce two sandboxes.  Subsequent calls will find
both and return the newest (with a warning).  Closing that gap requires a server-side
conditional-create primitive (e.g. a PUT with idempotency-key semantics) that Daytona's
REST API does not currently expose.
"""

from __future__ import annotations

import asyncio
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daytona.common.daytona import CreateSandboxFromSnapshotParams, DaytonaConfig
from daytona.common.errors import DaytonaError, DaytonaValidationError
from daytona_api_client import SandboxState

from .conftest import make_sandbox_dto

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SYNC_MODULE = "daytona._sync.daytona"
ASYNC_MODULE = "daytona._async.daytona"

_BASE_CFG = DaytonaConfig(api_key="test-key", api_url="https://test.io/api", target="us")


def _make_daytona(config=None):
    from daytona._sync.daytona import Daytona
    from daytona_api_client import Configuration

    with (
        patch(f"{SYNC_MODULE}.ApiClient") as mock_api_cls,
        patch(f"{SYNC_MODULE}.ToolboxApiClient") as mock_toolbox_cls,
    ):
        mock_api_instance = MagicMock()
        mock_api_instance.configuration = Configuration(host="https://test.daytona.io/api")
        mock_api_instance.default_headers = {}
        mock_api_instance.user_agent = ""
        mock_api_cls.return_value = mock_api_instance

        mock_toolbox_instance = MagicMock()
        mock_toolbox_instance.default_headers = {}
        mock_toolbox_cls.return_value = mock_toolbox_instance

        return Daytona(config or _BASE_CFG)


def _make_async_daytona(config=None):
    from daytona._async.daytona import AsyncDaytona
    from daytona_api_client_async import Configuration

    with (
        patch(f"{ASYNC_MODULE}.ApiClient") as mock_api_cls,
        patch(f"{ASYNC_MODULE}.ToolboxApiClient") as mock_toolbox_cls,
    ):
        mock_api_instance = MagicMock()
        mock_api_instance.configuration = Configuration(host="https://test.daytona.io/api")
        mock_api_instance.default_headers = {}
        mock_api_instance.user_agent = ""
        mock_api_instance.close = AsyncMock()
        mock_api_cls.return_value = mock_api_instance

        mock_toolbox_instance = MagicMock()
        mock_toolbox_instance.default_headers = {}
        mock_toolbox_instance.close = AsyncMock()
        mock_toolbox_cls.return_value = mock_toolbox_instance

        return AsyncDaytona(config or _BASE_CFG)


# ---------------------------------------------------------------------------
# Sync tests
# ---------------------------------------------------------------------------


class TestGetOrCreateSync:
    # 1 – no existing sandbox → create is called
    def test_first_call_creates_sandbox(self, sandbox_dto):
        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[], next_cursor=None)
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto

        sb = daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"agent": "1"}))

        daytona._sandbox_api.create_sandbox.assert_called_once()
        assert sb.id == sandbox_dto.id

    # 2 – matching sandbox found → no creation
    def test_returns_existing_started_sandbox(self, sandbox_dto):
        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[sandbox_dto], next_cursor=None)

        sb = daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"agent": "1"}))

        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.id == sandbox_dto.id

    # 3 – STOPPED sandbox is restarted
    def test_stopped_sandbox_is_restarted(self):
        stopped_dto = make_sandbox_dto(state=SandboxState.STOPPED)
        started_dto = make_sandbox_dto(state=SandboxState.STARTED)

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[stopped_dto], next_cursor=None)
        daytona._sandbox_api.start_sandbox.return_value = started_dto
        daytona._sandbox_api.get_sandbox.return_value = started_dto

        sb = daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"agent": "stopped"}))

        daytona._sandbox_api.start_sandbox.assert_called_once()
        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.state == SandboxState.STARTED

    # 4 – ARCHIVED sandbox is restarted (key fix vs original implementation)
    def test_archived_sandbox_is_restarted(self):
        archived_dto = make_sandbox_dto(state=SandboxState.ARCHIVED)
        started_dto = make_sandbox_dto(state=SandboxState.STARTED)

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[archived_dto], next_cursor=None)
        daytona._sandbox_api.start_sandbox.return_value = started_dto
        daytona._sandbox_api.get_sandbox.return_value = started_dto

        sb = daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"agent": "archived"}))

        daytona._sandbox_api.start_sandbox.assert_called_once()
        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.state == SandboxState.STARTED

    # 5 – STARTING sandbox is waited on (not restarted)
    def test_starting_sandbox_is_waited_on(self):
        starting_dto = make_sandbox_dto(state=SandboxState.STARTING)
        started_dto = make_sandbox_dto(state=SandboxState.STARTED)

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[starting_dto], next_cursor=None)
        daytona._sandbox_api.get_sandbox.return_value = started_dto

        sb = daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"role": "worker"}))

        daytona._sandbox_api.start_sandbox.assert_not_called()
        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.state == SandboxState.STARTED

    # 6 – ERROR sandbox raises (recreate_on_error=False, default)
    def test_error_sandbox_raises(self):
        error_dto = make_sandbox_dto(state=SandboxState.ERROR, error_reason="OOM")

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[error_dto], next_cursor=None)

        with pytest.raises(DaytonaError, match="error"):
            daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"job": "x"}))

        daytona._sandbox_api.create_sandbox.assert_not_called()

    # 7 – ERROR sandbox + recreate_on_error=True → delete + create
    def test_error_sandbox_recreated_when_flag_set(self, sandbox_dto):
        error_dto = make_sandbox_dto(state=SandboxState.ERROR, sandbox_id="err-id")

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[error_dto], next_cursor=None)
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto

        sb = daytona.get_or_create(
            CreateSandboxFromSnapshotParams(labels={"job": "x"}),
            recreate_on_error=True,
        )

        daytona._sandbox_api.create_sandbox.assert_called_once()
        assert sb.id == sandbox_dto.id

    # 8 – multiple label matches → warning + newest returned
    def test_multiple_matches_warns_and_picks_newest(self):
        older = make_sandbox_dto(sandbox_id="old-id", created_at="2025-01-01T00:00:00Z")
        newer = make_sandbox_dto(sandbox_id="new-id", created_at="2025-06-01T00:00:00Z")

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[older, newer], next_cursor=None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sb = daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"env": "dev"}))

        assert any("get_or_create" in str(w.message) for w in caught)
        assert any(issubclass(w.category, UserWarning) for w in caught)
        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.id == "new-id"

    # 9 – no name, no labels → DaytonaValidationError
    def test_no_key_raises_validation_error(self):
        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match="idempotency key"):
            daytona.get_or_create(CreateSandboxFromSnapshotParams())

    def test_empty_labels_and_no_name_raises(self):
        daytona = _make_daytona()
        with pytest.raises(DaytonaValidationError, match="idempotency key"):
            daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={}))

    # 10 – name-based: sandbox found by name → returned without creating
    def test_name_based_returns_existing(self, sandbox_dto):
        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.get_sandbox.return_value = sandbox_dto

        sb = daytona.get_or_create(CreateSandboxFromSnapshotParams(name="my-sandbox"))

        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.id == sandbox_dto.id

    # 11 – name-based: not found → create
    def test_name_based_creates_when_not_found(self, sandbox_dto):
        from daytona_api_client.exceptions import NotFoundException

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.get_sandbox.side_effect = NotFoundException(status=404, reason="not found")
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto

        sb = daytona.get_or_create(CreateSandboxFromSnapshotParams(name="new-sandbox"))

        daytona._sandbox_api.create_sandbox.assert_called_once()
        assert sb.id == sandbox_dto.id

    # 13 – DESTROYED sandbox skipped; fresh sandbox created
    def test_skips_destroyed_sandbox_and_creates(self, sandbox_dto):
        destroyed_dto = make_sandbox_dto(state=SandboxState.DESTROYED, sandbox_id="dead-id")

        daytona = _make_daytona()
        daytona._sandbox_api = MagicMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[destroyed_dto], next_cursor=None)
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto

        sb = daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"job": "fresh"}))

        daytona._sandbox_api.create_sandbox.assert_called_once()
        assert sb.id != "dead-id"


# ---------------------------------------------------------------------------
# Async tests
# ---------------------------------------------------------------------------


class TestGetOrCreateAsync:
    # 1 – no existing sandbox → create is called
    @pytest.mark.asyncio
    async def test_first_call_creates_sandbox(self, sandbox_dto):
        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[], next_cursor=None)
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto

        sb = await daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"agent": "1"}))

        daytona._sandbox_api.create_sandbox.assert_called_once()
        assert sb.id == sandbox_dto.id

    # 2 – existing sandbox returned without creating
    @pytest.mark.asyncio
    async def test_returns_existing_started_sandbox(self, sandbox_dto):
        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[sandbox_dto], next_cursor=None)

        sb = await daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"agent": "1"}))

        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.id == sandbox_dto.id

    # 3 – STOPPED sandbox restarted
    @pytest.mark.asyncio
    async def test_stopped_sandbox_is_restarted(self):
        stopped_dto = make_sandbox_dto(state=SandboxState.STOPPED)
        started_dto = make_sandbox_dto(state=SandboxState.STARTED)

        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[stopped_dto], next_cursor=None)
        daytona._sandbox_api.start_sandbox.return_value = started_dto
        daytona._sandbox_api.get_sandbox.return_value = started_dto

        sb = await daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"agent": "stopped"}))

        daytona._sandbox_api.start_sandbox.assert_called_once()
        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.state == SandboxState.STARTED

    # 4 – ARCHIVED sandbox restarted
    @pytest.mark.asyncio
    async def test_archived_sandbox_is_restarted(self):
        archived_dto = make_sandbox_dto(state=SandboxState.ARCHIVED)
        started_dto = make_sandbox_dto(state=SandboxState.STARTED)

        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[archived_dto], next_cursor=None)
        daytona._sandbox_api.start_sandbox.return_value = started_dto
        daytona._sandbox_api.get_sandbox.return_value = started_dto

        sb = await daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"agent": "archived"}))

        daytona._sandbox_api.start_sandbox.assert_called_once()
        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.state == SandboxState.STARTED

    # 5 – STARTING sandbox waited on
    @pytest.mark.asyncio
    async def test_starting_sandbox_is_waited_on(self):
        starting_dto = make_sandbox_dto(state=SandboxState.STARTING)
        started_dto = make_sandbox_dto(state=SandboxState.STARTED)

        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[starting_dto], next_cursor=None)
        daytona._sandbox_api.get_sandbox.return_value = started_dto

        sb = await daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"role": "worker"}))

        daytona._sandbox_api.start_sandbox.assert_not_called()
        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.state == SandboxState.STARTED

    # 6 – ERROR sandbox raises
    @pytest.mark.asyncio
    async def test_error_sandbox_raises(self):
        error_dto = make_sandbox_dto(state=SandboxState.ERROR, error_reason="OOM")

        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[error_dto], next_cursor=None)

        with pytest.raises(DaytonaError, match="error"):
            await daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"job": "x"}))

        daytona._sandbox_api.create_sandbox.assert_not_called()

    # 7 – ERROR sandbox + recreate_on_error=True → delete + create
    @pytest.mark.asyncio
    async def test_error_sandbox_recreated_when_flag_set(self, sandbox_dto):
        error_dto = make_sandbox_dto(state=SandboxState.ERROR, sandbox_id="err-id")

        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[error_dto], next_cursor=None)
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto

        sb = await daytona.get_or_create(
            CreateSandboxFromSnapshotParams(labels={"job": "x"}),
            recreate_on_error=True,
        )

        daytona._sandbox_api.create_sandbox.assert_called_once()
        assert sb.id == sandbox_dto.id

    # 8 – multiple label matches → warning + newest returned
    @pytest.mark.asyncio
    async def test_multiple_matches_warns_and_picks_newest(self):
        older = make_sandbox_dto(sandbox_id="old-id", created_at="2025-01-01T00:00:00Z")
        newer = make_sandbox_dto(sandbox_id="new-id", created_at="2025-06-01T00:00:00Z")

        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[older, newer], next_cursor=None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sb = await daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"env": "dev"}))

        assert any("get_or_create" in str(w.message) for w in caught)
        assert any(issubclass(w.category, UserWarning) for w in caught)
        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.id == "new-id"

    # 9 – no name, no labels → DaytonaValidationError
    @pytest.mark.asyncio
    async def test_no_key_raises_validation_error(self):
        daytona = _make_async_daytona()
        with pytest.raises(DaytonaValidationError, match="idempotency key"):
            await daytona.get_or_create(CreateSandboxFromSnapshotParams())

    # 10 – name-based: sandbox found → returned without creating
    @pytest.mark.asyncio
    async def test_name_based_returns_existing(self, sandbox_dto):
        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.get_sandbox.return_value = sandbox_dto

        sb = await daytona.get_or_create(CreateSandboxFromSnapshotParams(name="my-sandbox"))

        daytona._sandbox_api.create_sandbox.assert_not_called()
        assert sb.id == sandbox_dto.id

    # 11 – name-based: not found → create
    @pytest.mark.asyncio
    async def test_name_based_creates_when_not_found(self, sandbox_dto):
        from daytona_api_client_async.exceptions import NotFoundException

        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.get_sandbox.side_effect = NotFoundException(status=404, reason="not found")
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto

        sb = await daytona.get_or_create(CreateSandboxFromSnapshotParams(name="new-sandbox"))

        daytona._sandbox_api.create_sandbox.assert_called_once()
        assert sb.id == sandbox_dto.id

    # 12 – race safety: two concurrent async calls produce exactly one sandbox
    @pytest.mark.asyncio
    async def test_concurrent_calls_create_only_once(self, sandbox_dto):
        """The per-key asyncio.Lock serializes concurrent calls — one sandbox is created."""
        create_count = 0
        list_count = 0

        async def mock_list(**kwargs):
            nonlocal list_count
            list_count += 1
            await asyncio.sleep(0)  # yield — lets the second task attempt lock acquisition
            if list_count == 1:
                return MagicMock(items=[], next_cursor=None)
            return MagicMock(items=[sandbox_dto], next_cursor=None)

        async def mock_create(data, **kwargs):
            nonlocal create_count
            create_count += 1
            await asyncio.sleep(0)
            return sandbox_dto

        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.list_sandboxes.side_effect = mock_list
        daytona._sandbox_api.create_sandbox.side_effect = mock_create

        params = CreateSandboxFromSnapshotParams(labels={"agent": "race-test"})
        results = await asyncio.gather(
            daytona.get_or_create(params),
            daytona.get_or_create(params),
        )

        assert create_count == 1, f"Expected 1 creation, got {create_count}"
        assert results[0].id == sandbox_dto.id
        assert results[1].id == sandbox_dto.id

    # 13 – DESTROYED sandbox skipped; fresh sandbox created
    @pytest.mark.asyncio
    async def test_skips_destroyed_sandbox_and_creates(self, sandbox_dto):
        destroyed_dto = make_sandbox_dto(state=SandboxState.DESTROYED, sandbox_id="dead-id")

        daytona = _make_async_daytona()
        daytona._sandbox_api = AsyncMock()
        daytona._sandbox_api.list_sandboxes.return_value = MagicMock(items=[destroyed_dto], next_cursor=None)
        daytona._sandbox_api.create_sandbox.return_value = sandbox_dto

        sb = await daytona.get_or_create(CreateSandboxFromSnapshotParams(labels={"job": "fresh"}))

        daytona._sandbox_api.create_sandbox.assert_called_once()
        assert sb.id != "dead-id"
