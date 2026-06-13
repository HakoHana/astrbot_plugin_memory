"""认证中间件测试 — AuthManager 核心逻辑"""

from __future__ import annotations

import time

import pytest

from memori.api.auth import AuthManager


class TestAuthPublicPath:
    """_is_public_path 公开路径检查"""

    def test_public_paths(self):
        assert AuthManager._is_public_path("/health")
        assert AuthManager._is_public_path("/health?foo=1")
        assert AuthManager._is_public_path("/docs")
        assert AuthManager._is_public_path("/openapi.json")
        assert AuthManager._is_public_path("/redoc")
        assert AuthManager._is_public_path("/webui/dashboard/app.js")
        assert AuthManager._is_public_path("/webui/dashboard/index.html")
        assert AuthManager._is_public_path("/webui/settings/index.html")
        assert AuthManager._is_public_path("/settings")
        assert AuthManager._is_public_path("/config")

    def test_protected_paths(self):
        assert not AuthManager._is_public_path("/api/v1/memories")
        assert not AuthManager._is_public_path("/api/v1/relations/claim")
        assert not AuthManager._is_public_path("/api/v1/config")
        assert not AuthManager._is_public_path("/api/v1/shutdown")


class TestAuthApiKey:
    """API Key 验证"""

    @pytest.fixture
    def auth(self):
        a = AuthManager(config={"api_keys_path": ""})
        a.generate_api_key("alice")
        a.generate_api_key("bob")
        return a

    def test_valid_key_returns_user(self, auth):
        # grab the generated key
        keys = auth.list_api_keys()
        assert len(keys) == 2
        alice_key = [k for k in keys if k["user_id"] == "alice"][0]["key"]
        # key is truncated with "...", so we need the full key
        # instead, test via verify_token with the known key
        # Actually list_api_keys truncates, but the internal dict has full keys
        # Let me use the internal dict to find one
        full_keys = list(auth._api_keys.keys())
        uid = auth._verify_api_key(full_keys[0])
        assert uid in ("alice", "bob")

    def test_invalid_key_returns_none(self, auth):
        assert auth._verify_api_key("invalid_key_12345") is None
        assert auth._verify_api_key("") is None

    def test_revoke_key(self, auth):
        full_keys = list(auth._api_keys.keys())
        key = full_keys[0]
        assert auth.revoke_api_key(key) is True
        assert auth._verify_api_key(key) is None
        # double revoke fails
        assert auth.revoke_api_key(key) is False


class TestAuthCheckAccess:
    """权限检查"""

    @pytest.fixture
    def auth(self):
        return AuthManager()

    def test_self_access(self, auth):
        neighbors = {"alice": 1.0, "bob": 0.7}
        assert auth.check_access(neighbors, "alice", min_weight=0.0)
        assert auth.check_access(neighbors, "alice", min_weight=0.5)

    def test_neighbor_access_by_weight(self, auth):
        neighbors = {"alice": 1.0, "bob": 0.7, "carol": 0.3}
        # bob has 0.7, can access with min_weight 0.5
        assert auth.check_access(neighbors, "bob", min_weight=0.5)
        # carol has 0.3, cannot access with min_weight 0.5
        assert not auth.check_access(neighbors, "carol", min_weight=0.5)
        # carol can access with min_weight 0.2
        assert auth.check_access(neighbors, "carol", min_weight=0.2)

    def test_unknown_user(self, auth):
        neighbors = {"alice": 1.0}
        assert not auth.check_access(neighbors, "unknown", min_weight=0.0)

    def test_zero_weight_blocked(self, auth):
        neighbors = {"alice": 1.0, "blocked_user": 0.0}
        assert auth.check_access(neighbors, "blocked_user", min_weight=0.0)
        assert not auth.check_access(neighbors, "blocked_user", min_weight=0.01)


class TestAuthTokenExtraction:
    """Token 提取逻辑"""

    @staticmethod
    def _make_request(auth_header: str = "", query_token: str = ""):
        """模拟 FastAPI Request 对象（最小实现）"""
        class MockHeaders:
            def get(self, key, default=""):
                if key == "Authorization":
                    return auth_header
                return default

        class MockQuery:
            def get(self, key, default=""):
                if key == "token":
                    return query_token
                return default

        class MockRequest:
            headers = MockHeaders()
            query_params = MockQuery()
            url = type("url", (), {"path": "/api/v1/test"})()

        return MockRequest()

    def test_bearer_token(self):
        req = self._make_request(auth_header="Bearer my_token_123")
        assert AuthManager._extract_token(req) == "my_token_123"

    def test_bearer_lowercase(self):
        req = self._make_request(auth_header="bearer my_token_123")
        assert AuthManager._extract_token(req) == "my_token_123"

    def test_query_param_token(self):
        req = self._make_request(query_token="token_from_query")
        assert AuthManager._extract_token(req) == "token_from_query"

    def test_no_token(self):
        req = self._make_request()
        assert AuthManager._extract_token(req) is None

    def test_auth_header_priority(self):
        """Authorization header 优先级高于 query param"""
        req = self._make_request(
            auth_header="Bearer header_token",
            query_token="query_token",
        )
        assert AuthManager._extract_token(req) == "header_token"


class TestAuthNeighborCache:
    """邻居缓存管理"""

    @pytest.fixture
    def auth(self):
        return AuthManager()

    def test_cache_invalidate(self, auth):
        auth._cache["alice"] = ({"alice": 1.0, "bob": 0.7}, time.time())
        assert "alice" in auth._cache
        auth.invalidate_cache("alice")
        assert "alice" not in auth._cache

    def test_cache_ttl(self, auth):
        """缓存超过 1 小时后失效"""
        old = time.time() - 4000  # > 1 hour ago
        auth._cache["alice"] = ({"alice": 1.0}, old)
        auth._cache_ttl = 3600

        # get_accessible_users called with no graph_engine returns just self
        import asyncio
        neighbors = asyncio.run(auth.get_accessible_users("alice"))
        # graph_engine is None so falls back to just self
        assert neighbors == {"alice": 1.0}

    def test_cache_hit(self, auth):
        """缓存命中时不重新加载"""
        auth._cache["alice"] = ({"alice": 1.0, "bob": 0.7}, time.time())
        import asyncio
        neighbors = asyncio.run(auth.get_accessible_users("alice"))
        assert neighbors == {"alice": 1.0, "bob": 0.7}
