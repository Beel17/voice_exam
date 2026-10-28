"""Take an exam by typing, in a terminal: the phase-1 stand-in for the voice client.

    uv run python tools/text_client.py --token <device token> --reg 10000001AA --exam UTME-SAMPLE

Type sentences as a candidate would say them. ``/key N`` presses a key (1-5, R, N, P,
Enter, Esc); ``/quit`` leaves. ``--resume <session id>`` reconnects to a session.
"""

import argparse
import asyncio
import sys

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from voice_agent.contract import (
    SCHEMA_VERSION,
    Error,
    Heard,
    Hello,
    KeyPress,
    Login,
    Say,
    SessionInfo,
    State,
    TextInput,
    parse_server_message,
)


def show(raw: str | bytes) -> None:
    message = parse_server_message(raw)
    match message:
        case Say(text=text):
            print(f"  \N{SPEAKER WITH THREE SOUND WAVES} {text}")
        case Heard(text=text):
            print(f"  (heard: {text})")
        case State(question=q, of=of, answered=a, time_left_s=t, pending_confirm=p):
            pending = f", waiting for yes/no ({p.kind})" if p else ""
            time_left = f", {t // 60} min left" if t is not None else ""
            print(f"  [question {q}/{of}, {a} answered{time_left}{pending}]")
        case SessionInfo(session_id=sid, resumed=resumed):
            print(f"  [session {sid}{' resumed' if resumed else ''}]")
        case Error(code=code, message=text):
            print(f"  [error {code}: {text}]")
        case _:
            pass


async def run(args: argparse.Namespace) -> None:
    async with connect(args.url) as ws:
        hello = Hello(
            schema_version=SCHEMA_VERSION,
            device_id=args.device,
            token=args.token,
            resume_session_id=args.resume,
        )
        await ws.send(hello.model_dump_json())
        if args.resume is None:
            await ws.send(Login(reg_no=args.reg, exam_code=args.exam).model_dump_json())

        async def receive() -> None:
            async for raw in ws:
                show(raw)

        receiver = asyncio.create_task(receive())
        loop = asyncio.get_running_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line or receiver.done():  # end of input, or the service closed
                    break
                line = line.strip()
                if not line:
                    continue
                if line == "/quit":
                    break
                message: KeyPress | TextInput
                if line.startswith("/key "):
                    message = KeyPress.model_validate({"key": line[5:].strip()})
                else:
                    message = TextInput(text=line)
                await ws.send(message.model_dump_json())
                await asyncio.sleep(0.2)  # let the replies print before the next prompt
        except ConnectionClosed:
            pass
        finally:
            receiver.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default="ws://127.0.0.1:8765/v1/session")
    parser.add_argument("--token", required=True)
    parser.add_argument("--device", default="text-client")
    parser.add_argument("--reg", default="10000001AA")
    parser.add_argument("--exam", default="UTME-SAMPLE")
    parser.add_argument("--resume")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
