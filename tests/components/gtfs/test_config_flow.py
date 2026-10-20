"""Tests for the GTFS config flow."""

from contextlib import AbstractContextManager
import json
from pathlib import Path
import threading
from typing import Any
from unittest.mock import ANY, AsyncMock, patch
import zipfile

from aiohttp import ClientError
import pygtfs
import pytest
from pytest_homeassistant.common import MockConfigEntry, get_schema_suggested_value
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker
from sqlalchemy.exc import OperationalError
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.gtfs.const import (
    CONF_DATA,
    CONF_DATABASE,
    CONF_DESTINATION,
    CONF_ORIGIN,
    DOMAIN,
    SUBENTRY_TYPE_ROUTE,
)
from homeassistant.components.gtfs.feed import (
    InvalidFeed,
    async_fetch_feed,
    feed_database_path,
    import_feed,
    load_local_feed,
)
from homeassistant.config_entries import (
    SOURCE_IMPORT,
    SOURCE_USER,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlowResult,
    ConfigSubentryDataWithId,
)
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from . import FEED_URL, build_feed_zip, gtfs_files, setup_integration

AGENCY_HEADER = "agency_id,agency_name,agency_url,agency_timezone\n"
STOP_TIMES_HEADER = "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"


async def _start_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return result["flow_id"]


async def _submit_url(hass: HomeAssistant, flow_id: str) -> ConfigFlowResult:
    """Submit FEED_URL and return the result once the feed has loaded."""
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: FEED_URL}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(flow_id)


def _assert_load_error(
    hass: HomeAssistant, result: ConfigFlowResult, error: str
) -> None:
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    assert (
        get_schema_suggested_value(result["data_schema"].schema, CONF_URL) == FEED_URL
    )
    assert gtfs_files(hass) == []


async def _assert_recovers(hass: HomeAssistant, flow_id: str) -> None:
    result = await _submit_url(hass, flow_id)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert gtfs_files(hass) == [result["data"][CONF_DATABASE]]


@pytest.mark.usefixtures("mock_feed_download")
@pytest.mark.parametrize(
    ("feed_zip", "title"),
    [
        pytest.param(build_feed_zip(), "Test Agency", id="agency_name"),
        pytest.param(
            build_feed_zip(
                replace={
                    "agency.txt": (
                        f"{AGENCY_HEADER}TA,,https://example.com,America/New_York\n"
                    )
                }
            ),
            "example.com",
            id="host_fallback",
        ),
    ],
)
async def test_user_flow(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, title: str
) -> None:
    """Test adding a feed by URL, titled by its agency or else the URL host."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: FEED_URL}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["progress_action"] == "load_feed"

    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == title
    assert result["data"] == {CONF_URL: FEED_URL, CONF_DATABASE: ANY}
    assert result["result"].unique_id is None
    assert len(mock_setup_entry.mock_calls) == 1

    # Let any cleanup scheduled when the flow was removed run.
    await hass.async_block_till_done()
    db_path = feed_database_path(hass, result["data"][CONF_DATABASE])
    assert db_path.is_file()
    assert sorted(path.name for path in db_path.parent.iterdir()) == [db_path.name]


@pytest.mark.usefixtures("mock_setup_entry")
@pytest.mark.parametrize(
    ("response", "error"),
    [
        pytest.param({"exc": ClientError()}, "cannot_connect", id="connection_error"),
        pytest.param({"exc": TimeoutError()}, "cannot_connect", id="timeout"),
        pytest.param({"status": 500}, "cannot_connect", id="server_error"),
        pytest.param({"text": "<html>Not here</html>"}, "invalid_feed", id="html_page"),
        pytest.param(
            {"content": build_feed_zip(exclude=frozenset({"stops.txt"}))},
            "invalid_feed",
            id="missing_stops",
        ),
        pytest.param(
            {
                "content": build_feed_zip(
                    exclude=frozenset({"calendar.txt", "calendar_dates.txt"})
                )
            },
            "invalid_feed",
            id="missing_calendar",
        ),
        pytest.param(
            {"content": build_feed_zip(replace={"agency.txt": AGENCY_HEADER})},
            "invalid_feed",
            id="no_agency",
        ),
        pytest.param(
            {
                "content": build_feed_zip(
                    replace={"agency.txt": AGENCY_HEADER.encode() + b"TA,\xff\n"}
                )
            },
            "invalid_feed",
            id="agency_not_utf8",
        ),
        pytest.param(
            {
                "content": build_feed_zip(
                    replace={"stop_times.txt": f"{STOP_TIMES_HEADER}T1,8:xx:00,,S1,1\n"}
                )
            },
            "invalid_feed",
            id="malformed_stop_time",
        ),
        pytest.param(
            {"content": build_feed_zip(replace={"calendar.txt": b""})},
            "invalid_feed",
            id="empty_file",
        ),
        pytest.param(
            {
                "content": build_feed_zip(
                    replace={
                        "stops.txt": (
                            "stop_id,stop_name,stop_lat,stop_lon\n"
                            "S1,First Street,40.0,-75.0\n"
                            "S1,First Street Again,40.0,-75.0\n"
                        )
                    }
                )
            },
            "invalid_feed",
            id="duplicate_stop",
        ),
    ],
)
async def test_user_flow_download_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    feed_zip: bytes,
    response: dict[str, Any],
    error: str,
) -> None:
    """Test feeds that fail to load return to the form, which can then recover."""
    aioclient_mock.get(FEED_URL, **response)
    flow_id = await _start_flow(hass)

    _assert_load_error(hass, await _submit_url(hass, flow_id), error)

    aioclient_mock.clear_requests()
    aioclient_mock.get(FEED_URL, content=feed_zip)
    await _assert_recovers(hass, flow_id)


@pytest.mark.usefixtures("mock_feed_download", "mock_setup_entry")
@pytest.mark.parametrize(
    ("patcher", "error"),
    [
        pytest.param(
            patch.object(zipfile.ZipFile, "open", side_effect=zipfile.BadZipFile),
            "invalid_feed",
            id="bad_zip",
        ),
        pytest.param(
            patch.object(zipfile.ZipFile, "open", side_effect=NotImplementedError),
            "invalid_feed",
            id="unsupported_compression",
        ),
        pytest.param(
            patch.object(zipfile.ZipFile, "open", side_effect=RuntimeError),
            "invalid_feed",
            id="encrypted",
        ),
        pytest.param(
            patch(
                "homeassistant.components.gtfs.feed.pygtfs.append_feed",
                side_effect=OperationalError("stmt", {}, Exception("disk full")),
            ),
            "unknown",
            id="database_error",
        ),
        pytest.param(
            patch(
                "homeassistant.components.gtfs.config_flow.async_fetch_feed",
                side_effect=RuntimeError,
            ),
            "unknown",
            id="unexpected_error",
        ),
    ],
)
async def test_user_flow_load_errors(
    hass: HomeAssistant, patcher: AbstractContextManager[Any], error: str
) -> None:
    """Test load failures that need a patch to force return to the form."""
    flow_id = await _start_flow(hass)

    with patcher:
        result = await _submit_url(hass, flow_id)
    _assert_load_error(hass, result, error)

    await _assert_recovers(hass, flow_id)


@pytest.mark.usefixtures("mock_feed_download", "mock_setup_entry")
@pytest.mark.parametrize(
    "url",
    [
        pytest.param("ftp://example.com/gtfs.zip", id="ftp"),
        pytest.param("http://", id="no_host"),
        pytest.param("not a url", id="relative"),
        pytest.param("http://[::1", id="unparsable"),
    ],
)
async def test_user_flow_invalid_url(hass: HomeAssistant, url: str) -> None:
    """Test a URL that is not http or https is rejected before downloading."""
    flow_id = await _start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_URL: url})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_URL: "invalid_url"}
    assert get_schema_suggested_value(result["data_schema"].schema, CONF_URL) == url

    await _assert_recovers(hass, flow_id)


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test a URL that is already configured aborts, ignoring whitespace."""
    mock_config_entry.add_to_hass(hass)
    flow_id = await _start_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: f"  {FEED_URL}  "}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert gtfs_files(hass) == []


@pytest.mark.usefixtures("mock_feed_download")
async def test_user_flow_duplicate_finished_first(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test the import is discarded if the URL was configured while it ran."""
    flow_id = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: FEED_URL}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS

    mock_config_entry.add_to_hass(hass)
    await hass.async_block_till_done()
    assert len(gtfs_files(hass)) == 1

    result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # Flow removal deletes the database in a background executor job.
    await hass.async_block_till_done(wait_background_tasks=True)
    assert gtfs_files(hass) == []


@pytest.mark.usefixtures("mock_feed_download")
async def test_user_flow_aborted_during_import(hass: HomeAssistant) -> None:
    """Test aborting the flow mid-import discards the imported database."""
    append_feed = pygtfs.append_feed
    appended = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    cancel_events: list[threading.Event] = []

    async def recording_fetch_feed(
        hass: HomeAssistant, url: str, db_path: Path, cancelled: threading.Event
    ) -> str | None:
        cancel_events.append(cancelled)
        return await async_fetch_feed(hass, url, db_path, cancelled)

    # Block after appending so the import has a database to discard.
    def blocking_append_feed(schedule: pygtfs.Schedule, feed_filename: str) -> None:
        append_feed(schedule, feed_filename)
        appended.set()
        release.wait(10)

    def signalling_import_feed(
        zip_path: Path, db_path: Path, cancelled: threading.Event
    ) -> None:
        try:
            import_feed(zip_path, db_path, cancelled)
        finally:
            finished.set()

    with (
        patch(
            "homeassistant.components.gtfs.feed.pygtfs.append_feed",
            blocking_append_feed,
        ),
        patch("homeassistant.components.gtfs.feed.import_feed", signalling_import_feed),
        patch(
            "homeassistant.components.gtfs.config_flow.async_fetch_feed",
            recording_fetch_feed,
        ),
    ):
        flow_id = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            flow_id, {CONF_URL: FEED_URL}
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert await hass.async_add_executor_job(appended.wait, 10)

        try:
            hass.config_entries.flow.async_abort(flow_id)
            # Set before the task's cancellation is delivered, which can be
            # after the database unlink is scheduled.
            assert [event.is_set() for event in cancel_events] == [True]
            await hass.async_block_till_done()
            # The download is cleaned up while the import thread is still running.
            assert [Path(name).suffix for name in gtfs_files(hass)] == [".tmp"]
        finally:
            release.set()
        assert await hass.async_add_executor_job(finished.wait, 10)

    assert gtfs_files(hass) == []


def test_import_cancelled_after_swap(tmp_path: Path) -> None:
    """Test a cancellation that arrives during the swap discards the database."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    zip_path = work_dir / "feed.zip"
    zip_path.write_bytes(build_feed_zip())
    db_path = work_dir / "feed.sqlite"
    cancelled = threading.Event()
    replace = Path.replace

    def replace_then_cancel(self: Path, target: Path) -> Path:
        result = replace(self, target)
        cancelled.set()
        return result

    with patch.object(Path, "replace", replace_then_cancel):
        import_feed(zip_path, db_path, cancelled)

    assert list(work_dir.iterdir()) == [zip_path]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("feed.zip", id="zip"),
        pytest.param("feed", id="folder"),
    ],
)
@pytest.mark.usefixtures("feed_file", "feed_folder")
def test_load_local_feed(hass_config_dir: str, source: str) -> None:
    """Test a local zip or folder is imported and its agency and stops returned."""
    gtfs_dir = Path(hass_config_dir) / "gtfs"

    agency, stops = load_local_feed(gtfs_dir / source, gtfs_dir / "out.sqlite")

    assert agency == "Test Agency"
    assert stops["S1"] == "First Street"
    assert (gtfs_dir / "out.sqlite").exists()


def test_load_local_feed_reuses_database(feed_file: Path) -> None:
    """Test an existing database is read without its source."""
    db_path = feed_file.with_suffix(".sqlite")
    import_feed(feed_file, db_path, threading.Event())
    feed_file.unlink()

    agency, _ = load_local_feed(feed_file, db_path)

    assert agency == "Test Agency"


def test_load_local_feed_missing(tmp_path: Path) -> None:
    """Test a missing source without a database raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_local_feed(tmp_path / "feed.zip", tmp_path / "feed.sqlite")
    assert not (tmp_path / "feed.sqlite").exists()


def test_load_local_feed_invalid_folder(feed_folder: Path) -> None:
    """Test a folder missing a required file is rejected before importing."""
    (feed_folder / "stops.txt").unlink()

    with pytest.raises(InvalidFeed, match="Missing stops.txt"):
        load_local_feed(feed_folder, feed_folder.with_suffix(".sqlite"))
    assert not feed_folder.with_suffix(".sqlite").exists()


def test_load_local_feed_unreadable_folder(feed_folder: Path) -> None:
    """Test a folder that cannot be listed is rejected before importing."""
    with (
        patch.object(Path, "iterdir", side_effect=PermissionError("denied")),
        pytest.raises(InvalidFeed, match="denied"),
    ):
        load_local_feed(feed_folder, feed_folder.with_suffix(".sqlite"))
    assert not feed_folder.with_suffix(".sqlite").exists()


def test_load_local_feed_directory_instead_of_file(feed_folder: Path) -> None:
    """Test a directory in place of a required file is rejected before importing."""
    (feed_folder / "agency.txt").unlink()
    (feed_folder / "agency.txt").mkdir()

    with pytest.raises(InvalidFeed, match="Missing agency.txt"):
        load_local_feed(feed_folder, feed_folder.with_suffix(".sqlite"))
    assert not feed_folder.with_suffix(".sqlite").exists()


def test_load_local_feed_replaces_empty_database(feed_file: Path) -> None:
    """Test a database without an imported feed is imported again."""
    db_path = feed_file.with_suffix(".sqlite")
    schedule = pygtfs.Schedule(str(db_path))
    schedule.session.close()
    schedule.engine.dispose()

    agency, stops = load_local_feed(feed_file, db_path)

    assert agency == "Test Agency"
    assert "S1" in stops


@pytest.mark.usefixtures("mock_feed_download")
async def test_user_flow_removed_before_finish(hass: HomeAssistant) -> None:
    """Test removing the flow after the import but before finishing deletes it."""
    flow_id = await _start_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: FEED_URL}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    assert len(gtfs_files(hass)) == 1

    hass.config_entries.flow.async_abort(flow_id)
    # Flow removal deletes the database in a background executor job.
    await hass.async_block_till_done(wait_background_tasks=True)
    assert gtfs_files(hass) == []


async def _start_route_flow(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_ROUTE), context={"source": SOURCE_USER}
    )
    return result["flow_id"]


def _route_input(origin: str, destination: str) -> dict[str, str]:
    return {CONF_ORIGIN: origin, CONF_DESTINATION: destination}


@pytest.mark.usefixtures("mock_feed_download")
async def test_add_route(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test adding a route titled from its stop names."""
    await setup_integration(hass, mock_config_entry)

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_TYPE_ROUTE),
        context={"source": SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["data_schema"].schema[CONF_ORIGIN].config["options"] == snapshot

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], _route_input("S2", "S3")
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Second Street to Third Street"
    assert result["data"] == {CONF_ORIGIN: "S2", CONF_DESTINATION: "S3"}
    assert result["unique_id"] == json.dumps(["S2", "S3"])

    await hass.async_block_till_done()
    assert len(mock_config_entry.subentries) == 2
    assert hass.states.get("calendar.second_street_to_third_street") is not None


@pytest.mark.usefixtures("mock_feed_download")
async def test_add_route_same_stop(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test origin and destination must differ."""
    await setup_integration(hass, mock_config_entry)
    flow_id = await _start_route_flow(hass, mock_config_entry)

    result = await hass.config_entries.subentries.async_configure(
        flow_id, _route_input("S1", "S1")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "same_stop"}
    schema = result["data_schema"].schema
    assert get_schema_suggested_value(schema, CONF_ORIGIN) == "S1"
    assert get_schema_suggested_value(schema, CONF_DESTINATION) == "S1"

    result = await hass.config_entries.subentries.async_configure(
        flow_id, _route_input("S1", "S2")
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_feed_download")
async def test_add_route_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test a route between the same stops cannot be added twice."""
    await setup_integration(hass, mock_config_entry)
    flow_id = await _start_route_flow(hass, mock_config_entry)

    result = await hass.config_entries.subentries.async_configure(
        flow_id, _route_input("S1", "S3")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_add_route_entry_not_loaded(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test routes cannot be added before the feed has loaded."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (mock_config_entry.entry_id, SUBENTRY_TYPE_ROUTE),
        context={"source": SOURCE_USER},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"


IMPORT_ROUTE = {CONF_DATA: "feed.zip", CONF_ORIGIN: "S1", CONF_DESTINATION: "S3"}


async def _import_route(hass: HomeAssistant, **overrides: str) -> ConfigFlowResult:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data={**IMPORT_ROUTE, **overrides}
    )


def _routes(entry: ConfigEntry) -> list[tuple[str, dict[str, Any], str | None]]:
    return [
        (subentry.title, dict(subentry.data), subentry.unique_id)
        for subentry in entry.subentries.values()
    ]


@pytest.mark.parametrize(
    ("name", "route_title"),
    [
        pytest.param({}, "First Street to Third Street", id="stop_names"),
        pytest.param({CONF_NAME: "Commute"}, "Commute", id="yaml_name"),
    ],
)
@pytest.mark.usefixtures("feed_file", "mock_setup_entry")
async def test_import(
    hass: HomeAssistant, name: dict[str, str], route_title: str
) -> None:
    """Test a YAML sensor creates a feed entry holding its route."""
    result = await _import_route(hass, **name)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Agency"
    assert result["data"] == {CONF_DATABASE: "feed.sqlite"}
    assert _routes(result["result"]) == [
        (
            route_title,
            {CONF_ORIGIN: "S1", CONF_DESTINATION: "S3"},
            json.dumps(["S1", "S3"]),
        )
    ]
    assert [
        subentry.subentry_type for subentry in result["result"].subentries.values()
    ] == [SUBENTRY_TYPE_ROUTE]
    assert gtfs_files(hass) == ["feed.sqlite", "feed.zip"]


@pytest.mark.usefixtures("feed_folder", "mock_setup_entry")
async def test_import_folder(hass: HomeAssistant) -> None:
    """Test a YAML sensor reading an unzipped feed folder."""
    result = await _import_route(hass, **{CONF_DATA: "feed"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DATABASE: "feed.sqlite"}
    assert gtfs_files(hass) == ["feed", "feed.sqlite"]


@pytest.mark.parametrize(
    "feed_zip",
    [
        pytest.param(
            build_feed_zip(
                replace={
                    "agency.txt": f"{AGENCY_HEADER}TA,,https://example.com,America/New_York\n"
                }
            ),
            id="unnamed_agency",
        )
    ],
)
@pytest.mark.usefixtures("feed_file", "mock_setup_entry")
async def test_import_title_without_agency_name(hass: HomeAssistant) -> None:
    """Test the entry is named after the data file when the agency has no name."""
    result = await _import_route(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "feed.zip"


@pytest.mark.usefixtures("feed_file", "mock_setup_entry")
async def test_import_route_joins_entry(hass: HomeAssistant) -> None:
    """Test a second YAML sensor on the same file adds a route to the entry."""
    entry = (await _import_route(hass))["result"]

    result = await _import_route(hass, **{CONF_ORIGIN: "S2", CONF_NAME: "Second"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "route_imported"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert _routes(entry) == [
        (
            "First Street to Third Street",
            {CONF_ORIGIN: "S1", CONF_DESTINATION: "S3"},
            json.dumps(["S1", "S3"]),
        ),
        (
            "Second",
            {CONF_ORIGIN: "S2", CONF_DESTINATION: "S3"},
            json.dumps(["S2", "S3"]),
        ),
    ]


@pytest.mark.usefixtures("feed_file", "mock_setup_entry")
async def test_import_already_configured(hass: HomeAssistant) -> None:
    """Test importing a route that is already a subentry aborts."""
    entry = (await _import_route(hass))["result"]

    result = await _import_route(hass, **{CONF_NAME: "Renamed"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert [title for title, _, _ in _routes(entry)] == ["First Street to Third Street"]


@pytest.mark.parametrize(
    ("feed_zip", "overrides", "reason"),
    [
        pytest.param(
            build_feed_zip(),
            {CONF_DATA: "missing.zip"},
            "file_not_found",
            id="file_not_found",
        ),
        pytest.param(
            build_feed_zip(exclude=frozenset({"stops.txt"})),
            {},
            "invalid_feed",
            id="invalid_feed",
        ),
        pytest.param(
            build_feed_zip(), {CONF_ORIGIN: "S9"}, "invalid_stop", id="invalid_origin"
        ),
        pytest.param(
            build_feed_zip(),
            {CONF_DESTINATION: "S9"},
            "invalid_stop",
            id="invalid_destination",
        ),
        pytest.param(
            build_feed_zip(), {CONF_ORIGIN: "S3"}, "same_stop", id="same_stop"
        ),
    ],
)
@pytest.mark.usefixtures("feed_file")
async def test_import_failure(
    hass: HomeAssistant, overrides: dict[str, str], reason: str
) -> None:
    """Test unusable YAML sensors abort without creating an entry."""
    result = await _import_route(hass, **overrides)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert hass.config_entries.async_entries(DOMAIN) == []


@pytest.mark.usefixtures("feed_file")
async def test_import_unexpected_error(hass: HomeAssistant) -> None:
    """Test an unexpected error aborts the import instead of escaping the flow."""
    with patch(
        "homeassistant.components.gtfs.config_flow.load_local_feed",
        side_effect=OSError,
    ):
        result = await _import_route(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unknown"
    assert hass.config_entries.async_entries(DOMAIN) == []


@pytest.mark.usefixtures("mock_setup_entry")
async def test_import_reuses_existing_database(
    hass: HomeAssistant, feed_file: Path
) -> None:
    """Test a database left by the legacy sensor is used when its feed is gone."""
    await hass.async_add_executor_job(
        import_feed, feed_file, feed_file.with_suffix(".sqlite"), threading.Event()
    )
    await hass.async_add_executor_job(feed_file.unlink)

    result = await _import_route(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_DATABASE: "feed.sqlite"}
    assert gtfs_files(hass) == ["feed.sqlite"]


@pytest.mark.usefixtures("feed_file")
async def test_import_rebuilds_missing_database(hass: HomeAssistant) -> None:
    """Test importing into an entry whose database was deleted reloads the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Agency",
        data={CONF_DATABASE: "feed.sqlite"},
        subentries_data=[
            ConfigSubentryDataWithId(
                data={CONF_ORIGIN: "S1", CONF_DESTINATION: "S3"},
                subentry_id="route",
                subentry_type=SUBENTRY_TYPE_ROUTE,
                title="First Street to Third Street",
                unique_id=json.dumps(["S1", "S3"]),
            )
        ],
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR

    result = await _import_route(hass)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.state is ConfigEntryState.LOADED
    assert gtfs_files(hass) == ["feed.sqlite", "feed.zip"]
