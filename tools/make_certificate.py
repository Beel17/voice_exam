"""Make a certificate for a centre server, so candidates' browsers reach it over HTTPS.

    uv run python -m tools.make_certificate --name exam-server --ip 192.168.1.10

Writes exam-server.crt and exam-server.key (to --out, default the current folder). Point
[tls] in the server's centre.toml at them, and install the .crt on each candidate PC as
a trusted root (double-click it: Install Certificate, Local Machine, "Trusted Root
Certification Authorities"), or the browser warns and may refuse the microphone. A centre
with its own certificate authority should use a certificate from it instead.

Needs openssl (Git for Windows has it). It is self-signed and valid for two years: make a
new one before then.
"""

import argparse
import ipaddress
import shutil
import subprocess
import tempfile
from pathlib import Path

DAYS = 730
# openssl needs a configuration file, and some Windows builds look for one that is not
# there: this is all it needs for a self-signed certificate.
MINIMAL_CONFIG = "[req]\ndistinguished_name = dn\nprompt = no\n[dn]\nCN = exam-server\n"


def make_certificate(out: Path, name: str, ips: list[str]) -> tuple[Path, Path]:
    """``name``.crt and ``name``.key in ``out``, valid for ``name`` and ``ips``."""
    openssl = shutil.which("openssl")
    if openssl is None:
        raise SystemExit("openssl not found: install Git for Windows, or use the centre's CA")
    for ip in ips:
        ipaddress.ip_address(ip)  # refuses anything that is not an address
    names = [f"DNS:{name}", "DNS:localhost", "IP:127.0.0.1", *(f"IP:{ip}" for ip in ips)]
    out.mkdir(parents=True, exist_ok=True)
    cert, key = out / f"{name}.crt", out / f"{name}.key"
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "openssl.cnf"
        config.write_text(MINIMAL_CONFIG, encoding="ascii")
        subprocess.run(
            [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", str(DAYS),
             "-config", str(config), "-subj", f"/CN={name}",
             "-addext", f"subjectAltName={','.join(dict.fromkeys(names))}",
             "-keyout", str(key), "-out", str(cert)],
            check=True, capture_output=True,
        )  # fmt: skip
    return cert, key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--name", required=True, help="the server's name on the LAN")
    parser.add_argument("--ip", action="append", default=[], help="its LAN address (repeatable)")
    parser.add_argument("--out", type=Path, default=Path())
    args = parser.parse_args()
    cert, key = make_certificate(args.out, args.name, args.ip)
    print(f"written: {cert} and {key} (keep the key on the server only)")


if __name__ == "__main__":
    main()
