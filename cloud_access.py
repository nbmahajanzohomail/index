"""Password-gated access for the hosted index research dashboard."""
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import urlsplit


class Access:
    def __init__(self, enabled=False, env=None):
        env = os.environ if env is None else env
        self.enabled = enabled
        self.password = env.get('INDEX_DASHBOARD_PASSWORD', '')
        configured_secret = env.get('INDEX_SESSION_SECRET', '')
        self.key = configured_secret.encode('utf-8') if configured_secret else secrets.token_bytes(32)
        self.origins = set()
        configured_origins = env.get('INDEX_ALLOWED_ORIGINS', '')
        render_origin = env.get('RENDER_EXTERNAL_URL', '')
        for value in (configured_origins + ',' + render_origin).split(','):
            value = value.strip().rstrip('/')
            if not value:
                continue
            parsed = urlsplit(value)
            if (not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username
                    or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')))):
                raise ValueError('Hosted dashboard origins must be HTTPS origins.')
            self.origins.add(value)
        if enabled and len(self.password) < 12:
            raise ValueError('Set INDEX_DASHBOARD_PASSWORD in Render to a private password with at least 12 characters.')
        if enabled and len(self.key) < 32:
            raise ValueError('INDEX_SESSION_SECRET must contain at least 32 characters.')
        if enabled and not self.origins:
            raise ValueError('Render did not provide an HTTPS service origin for the hosted sign-in gate.')
        self.attempts = []
        self.lock = threading.Lock()

    def origin_allowed(self, origin):
        return not origin or origin.rstrip('/') in self.origins

    def login(self, password, now=None):
        now = int(time.time() if now is None else now)
        with self.lock:
            self.attempts = [attempt for attempt in self.attempts if attempt > now - 60]
            if len(self.attempts) >= 12:
                raise ValueError('Too many sign-in attempts. Wait one minute and retry.')
            if not secrets.compare_digest(str(password).encode('utf-8'), self.password.encode('utf-8')):
                self.attempts.append(now)
                raise ValueError('Password not recognised.')
        payload = base64.urlsafe_b64encode(json.dumps(
            {'exp': now + 12 * 60 * 60, 'nonce': secrets.token_hex(12)}, separators=(',', ':')
        ).encode('utf-8')).decode('ascii').rstrip('=')
        signature = hmac.new(self.key, payload.encode('ascii'), hashlib.sha256).hexdigest()
        return payload + '.' + signature

    def valid(self, authorization, now=None):
        if not self.enabled:
            return True
        try:
            prefix, token = authorization.split(' ', 1)
            payload, signature = token.split('.', 1)
            expected = hmac.new(self.key, payload.encode('ascii'), hashlib.sha256).hexdigest()
            if prefix != 'Bearer' or not hmac.compare_digest(signature, expected):
                return False
            padded = payload + '=' * (-len(payload) % 4)
            details = json.loads(base64.urlsafe_b64decode(padded.encode('ascii')))
            return (time.time() if now is None else now) < details['exp']
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error):
            return False
