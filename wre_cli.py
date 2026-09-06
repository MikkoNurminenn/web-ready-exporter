# SPDX-License-Identifier: MIT
"""Headless entry point.

    blender -b scene.blend --python wre_cli.py -- --out out/model.glb --tris 300000 --strict

Exits 0 and prints `WRE_OK {...}` on success, exits 1 and prints `WRE_FAIL ...` on failure.
"""
import os
import runpy

runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_ready_exporter", "core.py"),
               run_name="__main__")
