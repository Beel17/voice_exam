"""A stand-in cloud: the sync API, enough to develop and test against.

    python -m voice_agent.sync.standin --packages DIR --results DIR --centre C1 --token T
    python -m voice_agent.sync.standin --spec spec/sync.openapi.json

Serves every ``*.zip`` in the packages folder as this centre's packages, and keeps the
results it receives in ``results.jsonl`` in the results folder (one operation per line,
each once). The real cloud implements the same API (spec/sync.openapi.json).
"""

import argparse
import hashlib
import hmac
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

from voice_agent.examstore.package import read_package
from voice_agent.sync import models
from voice_agent.sync.models import BatchReceipt, PackageInfo, PackageList, ResultBatch


def create_app(packages: Path, results: Path, centre_id: str, token: str) -> FastAPI:
    app = FastAPI(title="Voice exam sync API (stand-in cloud)", version="1")
    results.mkdir(parents=True, exist_ok=True)
    log = results / "results.jsonl"
    received: dict[str, dict[str, object]] = {}
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            op = json.loads(line)
            received[op["operation_id"]] = op

    def authorised(request: Request) -> None:
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not this centre's token")

    def check_centre(centre: str) -> None:
        if centre != centre_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such centre")

    def files() -> dict[str, tuple[Path, PackageInfo]]:
        found = {}
        for path in sorted(packages.glob("*.zip")):
            data = path.read_bytes()
            package = read_package(data).package
            info = PackageInfo(
                package_id=package.package_id,
                exam_id=package.exam_id,
                title=package.title,
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
            )
            found[package.package_id] = (path, info)
        return found

    Auth = Annotated[None, Depends(authorised)]

    @app.get("/v1/centres/{centre}/packages")
    def list_packages(centre: str, _: Auth) -> PackageList:
        check_centre(centre)
        return PackageList(packages=tuple(info for _, info in files().values()))

    @app.get("/v1/packages/{package_id}")
    def get_package(package_id: str, _: Auth) -> Response:
        found = files().get(package_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such package")
        return Response(found[0].read_bytes(), media_type="application/zip")

    @app.post("/v1/centres/{centre}/results")
    def post_results(centre: str, batch: ResultBatch, _: Auth) -> BatchReceipt:
        check_centre(centre)
        if batch.centre_id != centre:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "batch is for another centre")
        accepted = []
        with log.open("a", encoding="utf-8") as out:
            for op in batch.operations:
                record = op.model_dump(mode="json")
                seen = received.get(op.operation_id)
                if seen is not None and seen != record:
                    raise HTTPException(status.HTTP_409_CONFLICT, f"{op.operation_id} differs")
                if seen is None:
                    out.write(json.dumps(record) + "\n")
                    received[op.operation_id] = record
                accepted.append(op.operation_id)
        return BatchReceipt(accepted=tuple(accepted))

    return app


def openapi_json() -> str:
    """The API's description, for the cloud team: spec/sync.openapi.json."""
    with TemporaryDirectory() as tmp:
        spec = create_app(Path(tmp), Path(tmp) / "results", "centre", "token").openapi()
    spec["info"]["description"] = models.__doc__
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--spec", type=Path, help="write the API description here and stop")
    parser.add_argument("--packages", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--centre")
    parser.add_argument("--token")
    parser.add_argument("--port", type=int, default=8800)
    args = parser.parse_args()
    if args.spec:
        args.spec.write_text(openapi_json(), encoding="utf-8")
        return
    if not (args.packages and args.results and args.centre and args.token):
        parser.error("--packages, --results, --centre and --token are needed")
    app = create_app(args.packages, args.results, args.centre, args.token)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
