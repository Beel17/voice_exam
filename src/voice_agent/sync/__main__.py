"""Sync this centre with the cloud.

    python -m voice_agent.sync pull      # before the exam: download the exam packages
    python -m voice_agent.sync push      # after the exam: upload every result
    python -m voice_agent.sync status    # what is waiting to go up, and when it last went

Set up in the centre's configuration, under [sync]: url and centre_id; the token goes in
<data_dir>/sync-token.txt (or the VOICE_AGENT_SYNC__TOKEN environment variable).
"""

import argparse
import asyncio
import datetime

from voice_agent.config import Settings, load_settings
from voice_agent.examstore.store import ExamStore
from voice_agent.sync.client import SyncClient, SyncError


def token_for(settings: Settings) -> str:
    if settings.sync.token:
        return settings.sync.token
    path = settings.sync.token_file or settings.data_dir / "sync-token.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        raise SyncError(f"no sync token: put this centre's token in {path}") from None


def when(value: str | None) -> str:
    if value is None:
        return "never"
    return datetime.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")


async def run(command: str, settings: Settings, store: ExamStore) -> None:
    if command == "status":
        waiting, uploaded = store.upload_counts()
        print(f"results waiting to upload: {waiting} (uploaded: {uploaded})")
        print(
            f"last pull: {when(store.meta('last_pull'))}; "
            f"last push: {when(store.meta('last_push'))}"
        )
        return
    if not settings.sync.url or not settings.sync.centre_id:
        raise SyncError("sync is not set up: add url and centre_id under [sync]")
    client = SyncClient(store, settings.sync.url, settings.sync.centre_id, token_for(settings))
    try:
        if command == "pull":
            report = await client.pull()
            for package in report.imported:
                print(f"imported: {package} (closed; open it with voice_agent.examstore open)")
            for package in report.already_held:
                print(f"already held: {package}")
            for problem in report.refused:
                print(f"REFUSED: {problem}")
            if report.refused:
                raise SystemExit(1)
        else:
            print(f"uploaded {await client.push()} results; nothing is waiting")
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("pull", "push", "status"))
    args = parser.parse_args()
    settings = load_settings()
    store = ExamStore(settings.data_dir / "exams.db")
    try:
        asyncio.run(run(args.command, settings, store))
    except SyncError as e:
        raise SystemExit(f"sync failed: {e}") from None
    finally:
        store.close()


if __name__ == "__main__":
    main()
