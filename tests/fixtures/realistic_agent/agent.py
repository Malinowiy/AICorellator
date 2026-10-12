"""Synthetic project with REAL capabilities (not mock).

Used as a test fixture: the extractor must find executes_shell,
http_egress, and reads_filesystem.
"""
import subprocess

import requests


def create_agent():
    @mcp.tool
    def execute_script(script: str) -> str:
        return subprocess.run(script, shell=True, capture_output=True).stdout.decode()

    @mcp.tool
    def fetch_url(url: str) -> str:
        return requests.get(url).text

    @mcp.tool
    def read_file(path: str) -> str:
        with open(path, "r") as f:
            return f.read()

    return [execute_script, fetch_url, read_file]