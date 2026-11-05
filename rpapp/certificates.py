"""A self-signed certificate, so the app can be served over HTTPS without any setup.

Made on first start (with https = true in config.toml and no certificate of
your own), kept in the "certificates" folder next to config.toml, and reused:

    certificates/self-signed.pem       the certificate (not secret)
    certificates/self-signed-key.pem   its private key (readable by you only)

It's for this computer's names and addresses at the time (localhost, its host
name, its network addresses). Browsers can't trust it on their own, so they warn
on the first visit: check that the fingerprint printed at startup matches the
one the browser shows, then continue. It's renewed automatically 30 days before
it expires (after about 2 years, the most some browsers accept), which means one
more warning then. To make a new one now (say, the computer's address changed),
delete the two files and restart.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import APP_NAME

CERT_FILE = 'self-signed.pem'
KEY_FILE = 'self-signed-key.pem'
VALID_DAYS = 825      # Apple's limit for certificates you choose to trust
RENEW_BEFORE = timedelta(days=30)


@dataclass
class Certificate:
    cert: Path
    key: Path
    created: bool          # made just now (else reused)
    fingerprint: str       # SHA-256, as browsers show it: 'AB:CD:...'
    expires: datetime


def ensure_self_signed(folder: str | Path, *, addresses: list[str] = (), now: datetime | None = None) -> Certificate:
    """The self-signed certificate in `folder`, made (or remade) if it's missing, unusable or about to expire.

    `addresses` are added to the certificate's names, besides this computer's own.
    """
    folder = Path(folder)
    cert_path, key_path = folder / CERT_FILE, folder / KEY_FILE
    now = now or datetime.now(timezone.utc)
    cert = _usable(cert_path, key_path, now)
    created = cert is None
    if created:
        cert = _make(cert_path, key_path, now, addresses)
    return Certificate(cert_path, key_path, created, fingerprint(cert), cert.not_valid_after_utc)


def fingerprint(cert: x509.Certificate) -> str:
    return ':'.join(f'{b:02X}' for b in cert.fingerprint(hashes.SHA256()))


def _usable(cert_path: Path, key_path: Path, now: datetime) -> x509.Certificate | None:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        return None
    same_key = cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) \
        == key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if not same_key or cert.not_valid_after_utc - now < RENEW_BEFORE:
        return None
    return cert


def local_names() -> tuple[list[str], list[str]]:
    """This computer's host names and IP addresses (loopback included, IPv6 link-local left out)."""
    host = socket.gethostname()
    names = ['localhost', host, f'{host}.local'] if host and host != 'localhost' else ['localhost']
    addresses = ['127.0.0.1', '::1']
    try:
        import ifaddr
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                value = ip.ip if isinstance(ip.ip, str) else ip.ip[0]
                if not ipaddress.ip_address(value).is_link_local:
                    addresses.append(value)
    except Exception:  # listing the network cards is a nicety: localhost always works
        pass
    return names, addresses


def _make(cert_path: Path, key_path: Path, now: datetime, extra: list[str]) -> x509.Certificate:
    names, addresses = local_names()
    ips = []
    for value in [*addresses, *extra]:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            continue
        if not ip.is_unspecified and ip not in ips:  # 0.0.0.0 and :: aren't addresses one connects to
            ips.append(ip)

    key = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname() or 'this computer'
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f'{APP_NAME} on {host}'[:64]),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, f'{APP_NAME} app (self-signed)'),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))  # a little slack for clocks that are behind
        .not_valid_after(now + timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(
            [x509.DNSName(n) for n in dict.fromkeys(names)] + [x509.IPAddress(ip) for ip in ips]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    _write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()), mode=0o600)
    _write(cert_path, cert.public_bytes(serialization.Encoding.PEM), mode=0o644)
    return cert


def _write(path: Path, data: bytes, *, mode: int) -> None:
    """Write atomically, with the given permissions from the start."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix='.tmp')
    try:
        if hasattr(os, 'fchmod'):  # (not on Windows)
            os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
