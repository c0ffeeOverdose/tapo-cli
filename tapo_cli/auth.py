"""TP-Link cloud v2 login and request signing.

Requests are signed with HMAC-SHA1 over
``"<content-md5>\\n<timestamp>\\n<nonce>\\n<path>"``. The cloud serves its leaf
and intermediate certificates but not the roots, so the anchors in
``certs/tplink-ca-chain.pem`` are shipped; ``TAPO_CLI_CA_BUNDLE`` overrides
them. Wire format: docs/PROTOCOL.md.
"""

import base64
import hashlib
import hmac
import json
import os
import pathlib
import uuid

import requests

ACCESS_KEY = "4d11b6b9d5ea4d19a829adbb9714b057"
SECRET_KEY = "6ed7d97f3e73467f8a5bab90b577ba4c"
APP_TYPE = "TP-Link_Tapo_Android"
APP_NAME = "TP-Link_Tapo_Android"
APP_VERSION = "3.4.451"  # the account cloud expects this one
APP_SERVER_VERSION = "3.20.753"
SIGNING_TIMESTAMP = "9999999999"  # fixed in the app and its SDK

REGIONAL_HOST = "https://n-wap.i.tplinkcloud.com"

PATH_ACCOUNT_STATUS = "/api/v2/account/getAccountStatusAndUrl"
PATH_LOGIN = "/api/v2/account/login"
PATH_MFA_LOGIN = "/api/v2/account/checkMFACodeAndLogin"
PATH_APP_SERVICE_URL = "/api/v2/common/getAppServiceUrl"
PATH_REFRESH_TOKEN = "/api/v2/account/refreshToken"

ERR_MFA_REQUIRED = -20677
ERR_TOKEN_EXPIRED = -20651
ERR_REFRESH_TOKEN_EXPIRED = -20655
ERR_WRONG_CREDENTIALS = -20601
ERR_ACCOUNT_LOCKED = -20675

USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel Build/UP1A)"

CA_BUNDLE = pathlib.Path(__file__).with_name("certs") / "tplink-ca-chain.pem"


def ca_bundle():
    """Path to the CA bundle to verify TP-Link's servers against."""
    return os.environ.get("TAPO_CLI_CA_BUNDLE") or str(CA_BUNDLE)


class AuthError(Exception):
    """Wrong credentials, a locked account, or an unexpected cloud reply."""

    def __init__(self, message, error_code=None):
        super().__init__(message)
        self.error_code = error_code


class MFARequired(AuthError):
    """The account has two-factor authentication and no callback was given."""

    def __init__(self, message, mfa_type=None, email=None, error_code=None):
        super().__init__(message, error_code=error_code)
        self.mfa_type = mfa_type
        self.email = email


def content_md5(body):
    """Base64 MD5 of the request body, as the signing scheme wants it."""
    return base64.b64encode(hashlib.md5(body.encode()).digest()).decode()


def signing_headers(body_json, path):
    """Content-MD5 and X-Authorization for one signed request."""
    digest = content_md5(body_json)
    nonce = str(uuid.uuid4())
    signature = hmac.new(
        SECRET_KEY.encode(),
        f"{digest}\n{SIGNING_TIMESTAMP}\n{nonce}\n{path}".encode(),
        hashlib.sha1,
    ).hexdigest()
    return {
        "Content-MD5": digest,
        "X-Authorization": (
            f"Timestamp={SIGNING_TIMESTAMP}, Nonce={nonce}, "
            f"AccessKey={ACCESS_KEY}, Signature={signature}"
        ),
    }


class CloudAuth:
    """A logged-in TP-Link account session (Tapo cloud)."""

    def __init__(
        self,
        username=None,
        password=None,
        mfa_callback=None,
        token=None,
        host=None,
        term_id=None,
        refresh_token=None,
        verbose=False,
        timeout=30,
        session=None,
    ):
        self.username = username
        self.password = password
        self.term_id = term_id or str(uuid.uuid4())
        self.verbose = verbose
        self.timeout = timeout
        self.session = session or requests.Session()
        self.host = host or REGIONAL_HOST
        self.token = token
        self.refresh_token = refresh_token
        if token is None:
            self._login(mfa_callback)

    def _post(self, host, path, body, token=None):
        body_json = json.dumps(body)
        params = {
            "appName": APP_NAME,
            "appVer": APP_VERSION,
            "netType": "wifi",
            "termID": self.term_id,
            "ospf": "Android 14",
            "brand": "TPLINK",
            "locale": "en_US",
            "model": "Pixel",
            "termName": "Pixel",
            "termMeta": "Pixel",
        }
        if token:
            params["token"] = token
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json;charset=UTF-8",
        }
        headers.update(signing_headers(body_json, path))
        if self.verbose:
            print(f"POST {host}{path}")
            print(f"Body: {body_json}")
        response = self.session.post(
            host + path,
            data=body_json,
            params=params,
            headers=headers,
            verify=ca_bundle(),
            timeout=self.timeout,
        )
        if self.verbose:
            print(f"<- {response.status_code} {response.text[:400]}")
        try:
            return response.json()
        except ValueError:
            raise AuthError(
                f"unexpected reply ({response.status_code}) from {path}"
            )

    def _login(self, mfa_callback):
        status = self._post(
            self.host,
            PATH_ACCOUNT_STATUS,
            {"appType": APP_TYPE, "cloudUserName": self.username},
        )
        if status.get("error_code") == 0:
            regional = (status.get("result") or {}).get("appServerUrl")
            if regional:
                self.host = regional
        reply = self._post(
            self.host,
            PATH_LOGIN,
            {
                "appType": APP_TYPE,
                "appVersion": APP_VERSION,
                "cloudPassword": self.password,
                "cloudUserName": self.username,
                "platform": "Android",
                "refreshTokenNeeded": True,
                "supportBindAccount": False,
                "terminalUUID": self.term_id,
                "terminalName": "Pixel",
                "terminalMeta": "Pixel",
            },
        )
        code = reply.get("error_code")
        if code == 0:
            self._store_token(reply)
            return
        if code == ERR_MFA_REQUIRED:
            self._login_with_mfa(reply, mfa_callback)
            return
        if code in (ERR_WRONG_CREDENTIALS, ERR_ACCOUNT_LOCKED):
            raise AuthError(
                reply.get("msg") or "wrong username or password",
                error_code=code,
            )
        raise AuthError(
            reply.get("msg") or f"login failed (error {code})",
            error_code=code,
        )

    def _login_with_mfa(self, challenge, mfa_callback):
        mfa_type = (challenge.get("result") or {}).get(
            "mfaType", "verifyCodeLogin"
        )
        if mfa_callback is None:
            raise MFARequired(
                "two-factor authentication is required",
                mfa_type=mfa_type,
                email=self.username,
                error_code=ERR_MFA_REQUIRED,
            )
        code = mfa_callback(mfa_type, self.username)
        reply = self._post(
            self.host,
            PATH_MFA_LOGIN,
            {
                "appType": APP_TYPE,
                "cloudPassword": self.password,
                "cloudUserName": self.username,
                "code": code,
                "terminalUUID": self.term_id,
            },
        )
        if reply.get("error_code") == 0:
            self._store_token(reply)
            return
        raise AuthError(
            reply.get("msg") or "the verification code was rejected",
            error_code=reply.get("error_code"),
        )

    def _store_token(self, reply):
        result = reply.get("result") or {}
        self.token = result.get("token")
        self.refresh_token = result.get("refreshToken")
        if not self.token:
            raise AuthError("the cloud did not return a session token")

    def refresh(self):
        """Extend the session with its refresh token; False when it fails."""
        if not self.refresh_token:
            return False
        reply = self._post(
            self.host,
            PATH_REFRESH_TOKEN,
            {
                "appType": APP_TYPE,
                "refreshToken": self.refresh_token,
                "terminalUUID": self.term_id,
            },
        )
        if reply.get("error_code") != 0:
            return False
        result = reply.get("result") or {}
        self.token = result.get("token") or self.token
        self.refresh_token = result.get("refreshToken") or self.refresh_token
        return bool(result.get("token"))

    def get_app_server_url(self, service_id):
        """Resolve an NBU service host (the app-server) for this account."""
        reply = self._post(
            self.host,
            PATH_APP_SERVICE_URL,
            {"serviceIds": [service_id]},
            token=self.token,
        )
        if reply.get("error_code") != 0:
            raise AuthError(
                reply.get("msg") or "could not resolve the app-server",
                error_code=reply.get("error_code"),
            )
        urls = (reply.get("result") or {}).get("serviceUrls") or {}
        url = urls.get(service_id)
        if not url:
            raise AuthError(f"the cloud returned no URL for {service_id}")
        return url
