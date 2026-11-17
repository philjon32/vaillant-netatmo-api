"""Make respx mock httpx2.

respx only knows httpx. httpx2 can make `import httpx` resolve to itself, as
Home Assistant does, which has to happen before respx imports httpx; so its
plugin is loaded here rather than by its entry point (see pyproject.toml).
"""

import httpx2

httpx2.alias_httpx()

pytest_plugins = ["respx.plugin"]
