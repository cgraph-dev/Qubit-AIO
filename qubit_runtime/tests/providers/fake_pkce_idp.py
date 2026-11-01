"""Local fake OAuth IdP on 127.0.0.1 for the PKCE plugin helper (tests + live evidence).

/authorize  → 302 to redirect_uri?code=…&state=…  (records code_challenge and requested scope)
/token      → grant_type=authorization_code: verifies S256(code_verifier) == challenge, issues tokens
              grant_type=refresh_token: rotates (old refresh token becomes invalid → 400)

``start(tls_dir=...)`` serves HTTPS with a throwaway CA (``ca_pem``) so a caller can trust it: the
plugin OAuth contract only talks to https endpoints.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse


def _write_tls_material(directory: Path) -> tuple[Path, Path, Path]:
    """A throwaway CA and a leaf for 127.0.0.1; returns ``(ca_pem, leaf_pem, leaf_key)``."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory.mkdir(parents=True, exist_ok=True)
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake pkce idp CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - _dt.timedelta(minutes=5)).not_valid_after(now + _dt.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
            .issuer_name(ca_name).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - _dt.timedelta(minutes=5)).not_valid_after(now + _dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    ca_pem, leaf_pem, key_pem = directory / "ca.pem", directory / "leaf.pem", directory / "leaf.key"
    ca_pem.write_bytes(ca.public_bytes(pem))
    leaf_pem.write_bytes(leaf.public_bytes(pem))
    key_pem.write_bytes(leaf_key.private_bytes(pem, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    return ca_pem, leaf_pem, key_pem


class FakeIdP:
    def __init__(self) -> None:
        self.codes: dict[str, tuple[str, str]] = {}  # code -> (code_challenge, requested scope)
        self.refresh_tokens: set[str] = set()
        self.refresh_scopes: dict[str, str] = {}
        self.token_requests: list[dict] = []
        self.issued = 0
        self.override_state: Optional[str] = None
        # Test hooks: a forced token answer and the scope the user "granted" (partial consent).
        self.token_answer: Optional[tuple[int, dict]] = None
        self.granted_scope: Optional[str] = None
        self.ca_pem: Optional[Path] = None
        self.base = ""
        idp = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: A003
                return

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/authorize":
                    self.send_response(404); self.end_headers(); return
                q = parse_qs(parsed.query)
                assert q["code_challenge_method"] == ["S256"], q
                code = secrets.token_urlsafe(16)
                idp.codes[code] = (q["code_challenge"][0], q.get("scope", [""])[0])
                state = idp.override_state if idp.override_state is not None else q["state"][0]
                self.send_response(302)
                self.send_header("Location", f"{q['redirect_uri'][0]}?{urlencode({'code': code, 'state': state})}")
                self.end_headers()

            def do_POST(self):  # noqa: N802
                if urlparse(self.path).path != "/token":
                    self.send_response(404); self.end_headers(); return
                body = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
                form = {k: v[0] for k, v in body.items()}
                idp.token_requests.append(form)
                if idp.token_answer is not None:
                    return self._json(idp.token_answer[0], idp.token_answer[1])
                grant = form.get("grant_type")
                scope = ""
                if grant == "authorization_code":
                    recorded = idp.codes.pop(form.get("code", ""), None)
                    digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
                    challenge = recorded[0] if recorded is not None else None
                    if challenge is None or base64.urlsafe_b64encode(digest).decode().rstrip("=") != challenge:
                        return self._json(400, {"error": "invalid_grant"})
                    scope = recorded[1]
                elif grant == "refresh_token":
                    if form.get("refresh_token") not in idp.refresh_tokens:
                        return self._json(400, {"error": "invalid_grant"})
                    scope = idp.refresh_scopes.pop(form["refresh_token"], "")
                    idp.refresh_tokens.discard(form["refresh_token"])
                else:
                    return self._json(400, {"error": "unsupported_grant_type"})
                idp.issued += 1
                refresh = f"rt-{idp.issued}-{secrets.token_hex(4)}"
                idp.refresh_tokens.add(refresh)
                idp.refresh_scopes[refresh] = scope
                payload = {"access_token": f"at-{idp.issued}-{secrets.token_hex(4)}",
                           "refresh_token": refresh, "expires_in": 3600, "token_type": "Bearer"}
                granted = idp.granted_scope if idp.granted_scope is not None else scope
                if granted:
                    payload["scope"] = granted
                return self._json(200, payload)

            def _json(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._handler = Handler
        self.server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, tls_dir: Optional[Path] = None) -> "FakeIdP":
        server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler)
        server.daemon_threads = True
        scheme = "http"
        if tls_dir is not None:
            self.ca_pem, leaf, key = _write_tls_material(Path(tls_dir))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(leaf), str(key))
            server.socket = context.wrap_socket(server.socket, server_side=True)
            scheme = "https"
        self.server = server
        self.base = f"{scheme}://127.0.0.1:{server.server_address[1]}"
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self.server is None:
            return
        server, self.server = self.server, None
        server.shutdown()
        server.server_close()


PLUGIN_TEMPLATE = '''
from providers import register_provider
from providers.base import ProviderProfile
from qubit_cli.auth_oauth_pkce_plugin import OAuthPKCEConfig, pkce_auth_handler, pkce_refresh_credential

cfg = OAuthPKCEConfig(client_id="hermes-example", authorize_url="{base}/authorize", token_url="{base}/token",
                      scopes=("inference",), timeout_seconds=20)
register_provider(ProviderProfile(name="example-pkce", auth_type="oauth_external",
                                  base_url="https://example.invalid/v1", fallback_models=("example-model",),
                                  auth_handler=pkce_auth_handler(cfg), refresh_credential=pkce_refresh_credential(cfg)))
'''
