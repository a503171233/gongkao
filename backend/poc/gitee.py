# -*- coding: utf-8 -*-
"""Gitee OAuth2 客户端（第三方登录）。标准库实现（urllib），无新依赖。
对应 Gitee 开放平台 OAuth 流程：
    1. 前端跳转授权页 → 2. Gitee 回调带 code → 3. code 换 access_token →
    4. 用 access_token 拉用户信息(含 id/name/avatar)

配置（env / .env）：
    GITEE_CLIENT_ID     应用 Client ID（必填才启用）
    GITEE_CLIENT_SECRET 应用 Client Secret
    GITEE_REDIRECT_URI  回调地址，如 http://YOUR_SERVER_IP:3000/api/auth/gitee/callback
        （必须与 Gitee 后台填写的回调地址一致）

未配置 client_id → enabled=False，登录端点返回 503 禁用（前端隐藏按钮）。
"""
import os
import urllib.parse
import urllib.request

from .config import get

AUTHORIZE_URL = "https://gitee.com/oauth/authorize"
TOKEN_URL = "https://gitee.com/oauth/token"
USER_API = "https://gitee.com/api/v5/user"


class GiteeOAuth:
    def __init__(self, client_id: str = "", client_secret: str = "",
                 redirect_uri: str = "", _http=None):
        """_http: 可注入的 HTTP 函数 (method, url, data_dict_or_None) -> (status, json_dict)，
        供测试 mock，默认用 urllib 真实请求。"""
        self.client_id = client_id or get("GITEE_CLIENT_ID")
        self.client_secret = client_secret or get("GITEE_CLIENT_SECRET")
        self.redirect_uri = redirect_uri or get("GITEE_REDIRECT_URI")
        self._http = _http
        self.enabled = bool(self.client_id and self.client_secret)

    def authorize_url(self, state: str) -> str:
        """构造授权页跳转 URL。state 用于防 CSRF（随机串）。"""
        params = urllib.parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "user_info",
            "state": state,
        })
        return f"{AUTHORIZE_URL}?{params}"

    def _request(self, method: str, url: str, data: dict | None = None) -> tuple[int, dict]:
        if self._http:
            return self._http(method, url, data)
        body = urllib.parse.urlencode(data or {}).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        if data:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                import json
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            import json
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}

    def exchange_code(self, code: str) -> dict:
        """code 换 access_token。成功返回 {'access_token':..., 'token_type':...}。"""
        status, data = self._request("POST", TOKEN_URL, {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
        })
        if status != 200 or "access_token" not in data:
            raise ValueError(f"Gitee 换取 token 失败: {data.get('error_description') or data.get('error') or status}")
        return data

    def fetch_user(self, access_token: str) -> dict:
        """拉取 Gitee 用户信息。返回含 id/name/avatar_url 的 dict。"""
        url = f"{USER_API}?access_token={urllib.parse.quote(access_token)}"
        status, data = self._request("GET", url)
        if status != 200 or "id" not in data:
            raise ValueError(f"Gitee 拉取用户失败: {status}")
        return data

    def login_with_code(self, code: str) -> dict:
        """完整流程：code → token → 用户信息。返回用户 dict（含 gitee uid）。"""
        tok = self.exchange_code(code)
        user = self.fetch_user(tok["access_token"])
        return user
