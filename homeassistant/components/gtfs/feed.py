"""Download, validate, and import GTFS feeds."""

import asyncio
from collections.abc import Callable
import csv
from dataclasses import dataclass, field
from functools import partial
import io
from pathlib import Path
import threading
from typing import IO, BinaryIO
import zipfile

from aiohttp import ClientError, ClientTimeout
import pygtfs
from pygtfs.gtfs_entities import Agency, Stop
from sqlalchemy.exc import IntegrityError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_PATH

# National feeds run to hundreds of megabytes, so only stalls are capped.
DOWNLOAD_TIMEOUT = ClientTimeout(total=None, sock_connect=30, sock_read=60)
CHUNK_SIZE = 2**20
_REQUIRED_FILES = frozenset(
    {"agency.txt", "routes.txt", "stop_times.txt", "stops.txt", "trips.txt"}
)
_CALENDAR_FILES = frozenset({"calendar.txt", "calendar_dates.txt"})

type GtfsConfigEntry = ConfigEntry[GtfsFeed]


class CannotConnect(Exception):
    """The feed could not be downloaded."""


class InvalidFeed(Exception):
    """The feed is not a usable GTFS feed."""


@dataclass
class GtfsFeed:
    """An imported GTFS schedule shared by an entry's calendars."""

    schedule: pygtfs.Schedule
    # The schedule's SQLAlchemy session is not thread-safe and calendars read
    # from executor threads.
    lock: threading.Lock = field(default_factory=threading.Lock)


def feed_database_path(hass: HomeAssistant, database: str) -> Path:
    """Return the path of a feed's SQLite database."""
    return Path(hass.config.path(DEFAULT_PATH, database))


def _open_destination(destination: Path) -> BinaryIO:
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination.open("wb")


async def _async_download_feed(
    hass: HomeAssistant, url: str, destination: Path
) -> None:
    """Stream the feed at url to destination."""
    session = async_get_clientsession(hass)
    file = await hass.async_add_executor_job(_open_destination, destination)
    buffer = bytearray()
    try:
        async with session.get(url, timeout=DOWNLOAD_TIMEOUT) as response:
            response.raise_for_status()
            async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                buffer += chunk
                if len(buffer) >= CHUNK_SIZE:
                    await hass.async_add_executor_job(file.write, buffer)
                    buffer.clear()
            if buffer:
                await hass.async_add_executor_job(file.write, buffer)
    except (ClientError, TimeoutError) as err:
        raise CannotConnect(str(err)) from err
    finally:
        await hass.async_add_executor_job(file.close)


async def async_fetch_feed(
    hass: HomeAssistant, url: str, db_path: Path, cancelled: threading.Event
) -> str | None:
    """Download, validate, and import the feed at url; return its agency name.

    Setting cancelled stops the import from leaving a database behind. It is
    set here when this task is cancelled, and by the caller when it removes the
    database, since the import thread outlives the cancelled task.
    """
    zip_path = db_path.with_suffix(".zip")
    try:
        await _async_download_feed(hass, url, zip_path)
        try:
            name = await hass.async_add_executor_job(
                _validate_and_import, zip_path, db_path, cancelled
            )
        except asyncio.CancelledError:
            cancelled.set()
            raise
    finally:
        await hass.async_add_executor_job(partial(zip_path.unlink, missing_ok=True))
    return name


def _validate_and_import(
    source: Path, db_path: Path, cancelled: threading.Event
) -> str | None:
    name = validate_feed(source)
    import_feed(source, db_path, cancelled)
    return name


def validate_feed(path: Path) -> str | None:
    """Check path is a GTFS zip or folder and return its first agency name."""
    if path.is_dir():
        try:
            return _validate_files(
                {child.name for child in path.iterdir() if child.is_file()},
                lambda name: (path / name).open("rb"),
            )
        except OSError as err:
            raise InvalidFeed(str(err)) from err
    if not zipfile.is_zipfile(path):
        raise InvalidFeed("Not a zip file")
    try:
        with zipfile.ZipFile(path) as archive:
            return _validate_files(set(archive.namelist()), archive.open)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as err:
        raise InvalidFeed(str(err)) from err


def _validate_files(
    names: set[str], open_file: Callable[[str], IO[bytes]]
) -> str | None:
    if missing := _REQUIRED_FILES - names:
        raise InvalidFeed(f"Missing {', '.join(sorted(missing))}")
    if not names & _CALENDAR_FILES:
        raise InvalidFeed("Missing calendar.txt or calendar_dates.txt")
    try:
        with open_file("agency.txt") as agency_file:
            row = next(
                csv.DictReader(io.TextIOWrapper(agency_file, encoding="utf-8-sig")),
                None,
            )
    except (csv.Error, UnicodeDecodeError) as err:
        raise InvalidFeed(str(err)) from err
    if row is None:
        # pygtfs crashes on a header-only agency.txt.
        raise InvalidFeed("No agency in agency.txt")
    return row.get("agency_name") or None


def _append_feed(db_path: Path, source: Path) -> None:
    schedule: pygtfs.Schedule | None = None
    try:
        schedule = pygtfs.Schedule(str(db_path))
        pygtfs.append_feed(schedule, str(source))
    except (
        ValueError,
        KeyError,
        StopIteration,
        csv.Error,
        zipfile.BadZipFile,
        IntegrityError,
    ) as err:
        raise InvalidFeed(str(err)) from err
    finally:
        if schedule is not None:
            schedule.session.close()
            schedule.engine.dispose()


def import_feed(source: Path, db_path: Path, cancelled: threading.Event) -> None:
    """Import source into db_path, replacing it only on success.

    A cancelled import discards its result, even if cancelled after the swap.
    """
    tmp_path = db_path.with_name(f"{db_path.name}.tmp")
    tmp_path.unlink(missing_ok=True)
    try:
        _append_feed(tmp_path, source)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    if cancelled.is_set():
        tmp_path.unlink(missing_ok=True)
        return
    tmp_path.replace(db_path)
    if cancelled.is_set():
        db_path.unlink(missing_ok=True)


def open_feed(db_path: Path) -> GtfsFeed:
    """Open an imported feed for calendars reading from executor threads."""
    return GtfsFeed(pygtfs.Schedule(f"{db_path}?check_same_thread=False"))


def close_feed(feed: GtfsFeed) -> None:
    """Release the feed's database connections."""
    with feed.lock:
        feed.schedule.session.close()
        feed.schedule.engine.dispose()


def list_stops(feed: GtfsFeed) -> dict[str, str]:
    """Return stop names keyed by stop ID, ordered by name."""
    with feed.lock:
        stops = (
            feed.schedule.session.query(Stop.stop_id, Stop.stop_name)
            .order_by(Stop.stop_name, Stop.stop_id)
            .all()
        )
    return {stop_id: stop_name or stop_id for stop_id, stop_name in stops}


def agency_name(feed: GtfsFeed) -> str | None:
    """Return the name of the feed's first agency."""
    with feed.lock:
        return feed.schedule.session.query(Agency.agency_name).limit(1).scalar()


def _has_feed(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    feed = open_feed(db_path)
    try:
        return bool(feed.schedule.feeds)
    finally:
        close_feed(feed)


def load_local_feed(source: Path, db_path: Path) -> tuple[str | None, dict[str, str]]:
    """Import a local feed unless db_path holds one; return agency and stops.

    Raises FileNotFoundError if db_path holds no feed and source does not exist.
    """
    if not _has_feed(db_path):
        if not source.exists():
            raise FileNotFoundError(source)
        validate_feed(source)
        import_feed(source, db_path, threading.Event())
    feed = open_feed(db_path)
    try:
        return agency_name(feed), list_stops(feed)
    finally:
        close_feed(feed)
