"""The centre's side of sync: pull exam packages, push results.

Both are safe to run again at any time: a package already held is not downloaded twice,
and results the cloud has already accepted are not sent again (and if a reply is lost,
the cloud recognises the repeat). Nothing here runs during an exam unless asked.
"""

import hashlib
import time
from dataclasses import dataclass
from itertools import groupby
from urllib.parse import quote, urlsplit

import httpx

from voice_agent.config import Settings
from voice_agent.examstore.package import read_package
from voice_agent.examstore.store import ExamStore, StoreError, UploadRow
from voice_agent.sync.models import (
    MAX_BATCH,
    BatchReceipt,
    CandidatePackage,
    CandidateRecord,
    PackageInfo,
    PackageList,
    ResultBatch,
    ResultOperation,
)

MAX_PACKAGE_BYTES = 512 * 1024 * 1024


class SyncError(Exception):
    """Sync could not finish; the message says why in plain words."""


@dataclass(frozen=True, slots=True)
class PullReport:
    imported: tuple[str, ...]
    already_held: tuple[str, ...]
    refused: tuple[str, ...]  # "package: reason"


def check_url(url: str) -> str:
    """HTTPS only, except to this machine (the stand-in cloud, tests)."""
    parts = urlsplit(url)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise SyncError(f"the cloud address must be https: {url}")
    return url.rstrip("/")


class SyncClient:
    def __init__(
        self,
        store: ExamStore,
        url: str,
        centre_id: str | None,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._store = store
        self._centre = centre_id
        self._http = httpx.AsyncClient(
            base_url=check_url(url),
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            timeout=timeout_s,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(
        self, method: str, path: str, *, missing_ok: bool = False, **kwargs: object
    ) -> httpx.Response:
        """The cloud's answer; a 404 is returned as it is when ``missing_ok``."""
        try:
            response = await self._http.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as e:
            raise SyncError(f"the cloud could not be reached ({type(e).__name__})") from None
        if missing_ok and response.status_code == 404:
            return response
        if response.status_code == 401:
            raise SyncError("the cloud refused this centre's token")
        if response.is_error:
            raise SyncError(f"the cloud answered {response.status_code}: {response.text[:200]}")
        return response

    def _centre_or_default(self, centre: str | None) -> str:
        if centre := centre or self._centre:
            return centre
        raise SyncError("no centre to sync: set centre_id under [sync], or name one")

    async def pull(self, centre: str | None = None) -> PullReport:
        """Download and import every package assigned to ``centre`` (this centre's own by
        default) not already held. Papers are imported closed: opening one for sign-in is
        the centre's decision."""
        centre = self._centre_or_default(centre)
        listing = PackageList.model_validate_json(
            (await self._call("GET", f"/v1/centres/{_part(centre)}/packages")).content
        )
        held = self._store.package_hashes()
        imported, already, refused = [], [], []
        for info in listing.packages:
            if info.sha256 in held:
                already.append(info.package_id)
            elif problem := await self.fetch(info, centre):
                refused.append(f"{info.package_id}: {problem}")
            else:
                imported.append(info.package_id)
        self._store.set_meta("last_pull", str(time.time()))
        return PullReport(tuple(imported), tuple(already), tuple(refused))

    async def fetch(self, info: PackageInfo, centre: str) -> str | None:
        """Download and import one package for ``centre``: None, or why it was refused."""
        if info.size > MAX_PACKAGE_BYTES:
            return "too large"
        data = (await self._call("GET", f"/v1/packages/{_part(info.package_id)}")).content
        if len(data) != info.size or hashlib.sha256(data).hexdigest() != info.sha256:
            return "damaged in transfer (checksum differs)"
        try:
            self._store.import_package(read_package(data), centre)
        except (StoreError, ValueError) as e:
            return str(e)
        return None

    async def push(self) -> int:
        """Upload every logged write the cloud does not have yet, each to the centre its
        paper was pulled for (this centre's own for papers loaded by hand), oldest first.
        Returns how many."""
        sent = 0
        while rows := self._store.pending_uploads(MAX_BATCH):
            for centre, group in groupby(rows, key=lambda r: r.centre or self._centre):
                if centre is None:
                    raise SyncError(
                        "results for a paper loaded by hand: set centre_id under [sync]"
                    )
                sent += await self._push_batch(centre, list(group))
        self._store.set_meta("last_push", str(time.time()))
        return sent

    async def _push_batch(self, centre: str, rows: list[UploadRow]) -> int:
        batch = ResultBatch(
            centre_id=centre,
            operations=tuple(
                ResultOperation(
                    operation_id=r.operation_id, seq=r.seq, candidate_id=r.candidate_id,
                    exam_id=r.exam_id, kind=r.kind,  # type: ignore[arg-type]
                    question=r.question, choice=r.choice,  # type: ignore[arg-type]
                    at=r.at,
                )
                for r in rows
            ),
        )  # fmt: skip
        response = await self._call(
            "POST",
            f"/v1/centres/{_part(centre)}/results",
            content=batch.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        accepted = set(BatchReceipt.model_validate_json(response.content).accepted)
        mine = [r.operation_id for r in rows if r.operation_id in accepted]
        if not mine:
            raise SyncError("the cloud accepted none of the results sent")
        self._store.mark_uploaded(mine)
        return len(mine)

    async def candidate_package(self, candidate_id: str) -> CandidatePackage | None:
        """The package the platform says ``candidate_id`` sits; None if it does not say
        (no such candidate, or a platform without this call)."""
        response = await self._call(
            "GET", f"/v1/candidates/{_part(candidate_id)}/package", missing_ok=True
        )
        if response.status_code == 404:
            return None
        try:
            return CandidatePackage.model_validate_json(response.content)
        except ValueError:
            raise SyncError(
                f"the cloud's answer about a candidate's package was not understood: "
                f"{response.text[:200]}"
            ) from None

    async def look_up(self, candidate: str) -> CandidateRecord | None:
        """Who the platform says ``candidate`` (a candidate ID or examination number) is;
        None if it does not know them."""
        response = await self._call("GET", f"/v1/candidates/{_part(candidate)}", missing_ok=True)
        if response.status_code == 404:
            return None
        try:
            return CandidateRecord.model_validate_json(response.content)
        except ValueError:
            raise SyncError(
                f"the cloud's answer about a candidate was not understood: {response.text[:200]}"
            ) from None


def token_for(settings: Settings) -> str:
    if settings.sync.token:
        return settings.sync.token
    path = settings.sync.token_file or settings.data_dir / "sync-token.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        raise SyncError(f"no sync token: put this centre's token in {path}") from None


def client_for(settings: Settings, store: ExamStore, timeout_s: float = 60.0) -> SyncClient:
    """The client the centre's configuration describes; SyncError if it is not set up."""
    if not settings.sync.url:
        raise SyncError("sync is not set up: add url under [sync]")
    token = token_for(settings)
    return SyncClient(store, settings.sync.url, settings.sync.centre_id, token, timeout_s=timeout_s)


def _part(value: str) -> str:
    """``value`` as one segment of a path: a "/" in an ID never reaches another call."""
    return quote(value, safe="")
