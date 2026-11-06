"""HTTPS: the self-signed certificate, and the https settings of config.toml."""
import os
import ssl
import stat
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address

import pytest
from cryptography import x509

from rpapp.certificates import CERT_FILE, KEY_FILE, ensure_self_signed
from rpapp.config import ConfigError, https_setup, load_config

ON_WINDOWS = os.name == 'nt'  # (file permissions below are Unix's: on Windows, a user's folder is theirs alone)


def read(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_made_once_then_reused(tmp_path):
    made = ensure_self_signed(tmp_path / 'certificates', addresses=['192.168.1.20', '0.0.0.0'])
    assert made.created and made.cert == tmp_path / 'certificates' / CERT_FILE
    assert ON_WINDOWS or stat.S_IMODE(made.key.stat().st_mode) == 0o600  # the key is for you only
    assert not list(made.cert.parent.glob('*.tmp'))

    cert = read(made.cert)
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert 'localhost' in names.get_values_for_type(x509.DNSName)
    ips = names.get_values_for_type(x509.IPAddress)
    assert ip_address('127.0.0.1') in ips and ip_address('192.168.1.20') in ips
    assert ip_address('0.0.0.0') not in ips
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days == 826
    assert made.fingerprint.count(':') == 31

    # Python's own TLS accepts the pair as a server certificate.
    ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(made.cert, made.key)

    again = ensure_self_signed(tmp_path / 'certificates')
    assert not again.created and again.fingerprint == made.fingerprint


def test_renewed_before_it_expires_or_when_broken(tmp_path):
    folder = tmp_path / 'certificates'
    first = ensure_self_signed(folder)
    later = datetime.now(timezone.utc) + timedelta(days=800)  # within 30 days of the end
    renewed = ensure_self_signed(folder, now=later)
    assert renewed.created and renewed.fingerprint != first.fingerprint

    (folder / KEY_FILE).write_text('garbage', encoding='utf-8')
    assert ensure_self_signed(folder).created

    other = ensure_self_signed(tmp_path / 'other')
    (folder / KEY_FILE).write_bytes(other.key.read_bytes())  # a key that doesn't go with the certificate
    assert ensure_self_signed(folder).created


def test_https_settings(tmp_path):
    def setup(text):
        (tmp_path / 'config.toml').write_text(text, encoding='utf-8')
        return https_setup(load_config(tmp_path))

    assert setup('[server]\nhttps = false\n') is None  # plain HTTP

    self_signed = setup('[server]\n')  # HTTPS by default, with the app's own certificate
    assert self_signed.self_signed.created and self_signed.cert == tmp_path / 'certificates' / CERT_FILE
    assert not setup('[server]\nhttps = true\n').self_signed.created  # made once, then reused

    own = ensure_self_signed(tmp_path / 'mine')  # stands in for files made with mkcert
    mine = setup('[server]\nhttps = true\ncertificate = "mine/self-signed.pem"\nkey = "mine/self-signed-key.pem"\n')
    assert mine.self_signed is None and mine.cert == own.cert and mine.key == own.key

    with pytest.raises(ConfigError, match='give both server.certificate and server.key'):
        setup('[server]\nhttps = true\ncertificate = "mine/self-signed.pem"\n')
    with pytest.raises(ConfigError, match='server.key is ".*nope.pem", which doesn\'t exist'):
        setup('[server]\nhttps = true\ncertificate = "mine/self-signed.pem"\nkey = "nope.pem"\n')
