"""AstrBot WebUI 路由注册桥接 — 将 PageApi 挂到 AstrBot 的 Web 框架"""

from __future__ import annotations


def register_webui_routes(context, page_api):
    """通过 AstrBot 的 register_web_api 注册所有 PageApi 路由"""
    register = context.register_web_api

    def _register(path, handler, methods, description):
        register(path, handler, methods, description)

    page_api.register_routes(_register)
