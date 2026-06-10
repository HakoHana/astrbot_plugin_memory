"""Flat layout: 仓库根目录 = memoria 包

pip install -e . 将根目录注册为 memoria 包，
core/ 等子目录映射为 memoria.core 等子包。
"""
from setuptools import setup

setup(
    package_dir={
        "memoria": ".",
        "memoria.core": "core",
        "memoria.storage": "storage",
        "memoria.models": "models",
        "memoria.retrieval": "retrieval",
        "memoria.api": "api",
    },
    packages=[
        "memoria",
        "memoria.core",
        "memoria.storage",
        "memoria.models",
        "memoria.retrieval",
        "memoria.api",
    ],
)
