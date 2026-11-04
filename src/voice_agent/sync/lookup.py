"""Signing in candidates the platform knows, from any centre.

A candidate not found in an open paper here is looked up on the platform by what they
signed in with. If it confirms them, their own paper is downloaded (or, from a platform
that cannot say which it is, all their centre's papers), once however many candidates
sign in at the same moment, and they are signed in to it, whether or not the centre
opened it: the platform has just said they may sit.
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine, Iterable
from typing import Literal

from voice_agent.adapters.base import AdapterRejected, AdapterUnavailable, RejectReason
from voice_agent.examstore.store import ExamStore
from voice_agent.sync.client import SyncClient, SyncError
from voice_agent.sync.models import CandidatePackage

log = logging.getLogger(__name__)


class CandidateLookup:
    def __init__(
        self,
        client: SyncClient,
        store: ExamStore,
        refuse_statuses: Iterable[str] = (),
        centre_from: Literal["centre_id", "centre_code"] = "centre_id",
    ) -> None:
        self._client = client
        self._centre_from = centre_from
        self._store = store
        self._refused = frozenset(s.upper() for s in refuse_statuses)
        # Downloads under way, by package or centre, each shared by whoever needs it.
        self._downloads: dict[str, asyncio.Task[str | None]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __call__(self, signed_in_with: str) -> str:
        """The candidate ID, held here, of the candidate the platform confirms."""
        try:
            record = await self._client.look_up(signed_in_with)
        except SyncError as e:
            log.warning("candidate lookup failed: %s", e)
            raise AdapterUnavailable(str(e)) from None
        if record is None:
            raise AdapterRejected(RejectReason.NOT_REGISTERED)
        centre = getattr(record, self._centre_from)
        if centre is None:
            log.warning("the platform did not say the candidate's %s", self._centre_from)
            raise AdapterRejected(RejectReason.NOT_REGISTERED)
        if record.status and record.status.upper() in self._refused:
            log.info("candidate of centre %s kept out: status %s", centre, record.status)
            raise AdapterRejected(RejectReason.NOT_REGISTERED)
        held = self._store.has_candidate(record.candidate_id)
        if not held and (problem := await self._bring_paper(record.candidate_id, centre)):
            raise AdapterUnavailable(problem)
        if not self._store.has_candidate(record.candidate_id):
            log.warning(
                "the platform confirmed a candidate of centre %s, but none of its papers has them",
                centre,
            )
            raise AdapterRejected(RejectReason.NOT_REGISTERED)
        return record.candidate_id

    async def _bring_paper(self, candidate_id: str, centre: str) -> str | None:
        """None, or why the candidate's paper could not be brought here."""
        try:
            info = await self._client.candidate_package(candidate_id)
        except SyncError as e:
            log.warning("asking for a candidate's paper failed: %s", e)
            return str(e)
        if info is None:
            return await self._once(f"centre:{centre}", lambda: self._pull_now(centre))
        if info.sha256 in self._store.package_hashes():
            return None  # held already, yet without them: nothing more to bring
        for_centre = info.centre_id or centre
        return await self._once(f"package:{info.sha256}", lambda: self._fetch(info, for_centre))

    async def _once(
        self, key: str, start: Callable[[], Coroutine[None, None, str | None]]
    ) -> str | None:
        task = self._downloads.get(key)
        if task is None or task.done():
            task = self._downloads[key] = asyncio.create_task(start())
        # Shielded: a sign-in that gives up waiting never cuts a download short.
        return await asyncio.shield(task)

    async def _fetch(self, info: CandidatePackage, centre: str) -> str | None:
        try:
            problem = await self._client.fetch(info, centre)
        except SyncError as e:
            log.warning("downloading %s failed: %s", info.package_id, e)
            return str(e)
        if problem:
            log.warning("refused from centre %s: %s: %s", centre, info.package_id, problem)
        else:
            log.info("downloaded for centre %s: %s", centre, info.package_id)
        return None

    async def _pull_now(self, centre: str) -> str | None:
        """None, or why the pull failed."""
        try:
            report = await self._client.pull(centre)
        except SyncError as e:
            log.warning("pulling centre %s's papers failed: %s", centre, e)
            return str(e)
        for package in report.imported:
            log.info("pulled for centre %s: %s", centre, package)
        for problem in report.refused:
            log.warning("refused from centre %s: %s", centre, problem)
        return None
