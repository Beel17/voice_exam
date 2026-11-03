"""The centre's side of sync: pull exam packages, push results.

Both are safe to run again at any time: a package already held is not downloaded twice,
and results the cloud has already accepted are not sent again (and if a reply is lost,
the cloud recognises the repeat). Nothing here runs during an exam unless asked.
"""

import hashlib
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from voice_agent.examstore.package import read_package
from voice_agent.examstore.store import ExamStore, StoreError
from voice_agent.sync.models import (
    MAX_BATCH,
    BatchReceipt,
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
        centre_id: str,
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

    async def _call(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as e:
            raise SyncError(f"the cloud could not be reached ({type(e).__name__})") from None
        if response.status_code == 401:
            raise SyncError("the cloud refused this centre's token")
        if response.is_error:
            raise SyncError(f"the cloud answered {response.status_code}: {response.text[:200]}")
        return response

    async def pull(self) -> PullReport:
        """Download and import every package assigned to this centre not already held.
        Papers are imported closed: opening one for sign-in is the centre's decision."""
        listing = PackageList.model_validate_json(
            (await self._call("GET", f"/v1/centres/{self._centre}/packages")).content
        )
        held = self._store.package_hashes()
        imported, already, refused = [], [], []
        for info in listing.packages:
            if info.sha256 in held:
                already.append(info.package_id)
                continue
            if info.size > MAX_PACKAGE_BYTES:
                refused.append(f"{info.package_id}: too large")
                continue
            data = (await self._call("GET", f"/v1/packages/{info.package_id}")).content
            if len(data) != info.size or hashlib.sha256(data).hexdigest() != info.sha256:
                refused.append(f"{info.package_id}: damaged in transfer (checksum differs)")
                continue
            try:
                self._store.import_package(read_package(data))
            except (StoreError, ValueError) as e:
                refused.append(f"{info.package_id}: {e}")
                continue
            imported.append(info.package_id)
        self._store.set_meta("last_pull", str(time.time()))
        return PullReport(tuple(imported), tuple(already), tuple(refused))

    async def push(self) -> int:
        """Upload every logged write the cloud does not have yet. Returns how many."""
        sent = 0
        while rows := self._store.pending_uploads(MAX_BATCH):
            batch = ResultBatch(
                centre_id=self._centre,
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
                f"/v1/centres/{self._centre}/results",
                content=batch.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            accepted = set(BatchReceipt.model_validate_json(response.content).accepted)
            mine = [r.operation_id for r in rows if r.operation_id in accepted]
            if not mine:
                raise SyncError("the cloud accepted none of the results sent")
            self._store.mark_uploaded(mine)
            sent += len(mine)
        self._store.set_meta("last_push", str(time.time()))
        return sent
