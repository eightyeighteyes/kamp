"""PyInstaller runtime hook: point OpenSSL and requests at the bundled CA bundle.

On macOS, Python's ssl module compiled against OpenSSL does not know about the
system Keychain and falls back to OpenSSL's compile-time default cert paths
(e.g. /etc/ssl/certs), which do not exist on macOS.  This causes
ssl.create_default_context() to fail certificate verification for all HTTPS
connections made via httpx/httpcore (Last.fm scrobbling, etc.).

requests is unaffected because it explicitly passes certifi.where() as cafile.
httpx uses ssl.create_default_context() with no explicit cafile, so it hits
the OpenSSL default paths and fails.

Setting SSL_CERT_FILE makes OpenSSL (and therefore Python's ssl module) use
the certifi CA bundle bundled by PyInstaller via collect_data_files("certifi")
in kamp.spec.  REQUESTS_CA_BUNDLE is set as belt-and-suspenders for requests.
"""

import os

import certifi

_ca = certifi.where()
os.environ.setdefault("SSL_CERT_FILE", _ca)
os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca)
