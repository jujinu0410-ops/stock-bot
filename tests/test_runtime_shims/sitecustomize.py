"""Test-mode-only import shims for optional live-data SDKs.

The project declares these packages for operational environments.  The bundled
offline test interpreter deliberately omits them, so this module only provides
inert imports while ``STOCKBOT_TEST_MODE=1``.  It is loaded solely through the
isolation wrapper's temporary PYTHONPATH.
"""

import importlib.util
import os
import socket
import sys
import types
from html.parser import HTMLParser
from unittest.mock import MagicMock


def _missing_module(name: str) -> bool:
    return importlib.util.find_spec(name) is None


if os.environ.get("STOCKBOT_TEST_MODE") == "1":
    def _block_external_network(_socket, address):
        raise OSError(f"[TEST_MODE] External network blocked: {address}")

    # A number of legacy scanner tests exercise production collectors through
    # isolated SQLite files.  Keep that coverage offline as well: mocks still
    # work normally, while an unmocked HTTP client fails before any request.
    socket.socket.connect = _block_external_network

    if _missing_module("requests"):
        class _RequestError(Exception):
            pass

        class _ConnectionError(_RequestError):
            pass

        class _Timeout(_RequestError):
            pass

        requests = types.ModuleType("requests")
        requests.get = MagicMock()
        requests.post = MagicMock()
        requests.Session = MagicMock()
        requests.exceptions = types.SimpleNamespace(
            RequestException=_RequestError,
            ConnectionError=_ConnectionError,
            Timeout=_Timeout,
        )
        sys.modules["requests"] = requests
    if _missing_module("urllib3"):
        class _HTTPError(Exception):
            pass

        class _NameResolutionError(_HTTPError):
            pass

        urllib3 = types.ModuleType("urllib3")
        urllib3.exceptions = types.SimpleNamespace(
            HTTPError=_HTTPError,
            NameResolutionError=_NameResolutionError,
        )
        sys.modules["urllib3"] = urllib3
    if _missing_module("bs4"):
        class _FallbackTag:
            def __init__(self, name, attrs):
                self.name = name
                self.attrs = dict(attrs)

            def get(self, key, default=None):
                return self.attrs.get(key, default)

            def __getitem__(self, key):
                return self.attrs[key]

        class _FallbackBeautifulSoup(HTMLParser):
            def __init__(self, markup, _parser):
                super().__init__()
                self._tags = []
                self.feed(markup)

            def handle_starttag(self, tag, attrs):
                self._tags.append(_FallbackTag(tag, attrs))

            @staticmethod
            def _matches(tag, name, class_, attrs):
                if name is not None and tag.name != name:
                    return False
                if class_ is not None:
                    classes = tag.get("class", "").split()
                    if class_ not in classes:
                        return False
                return all(
                    key in tag.attrs and (value is True or tag.attrs[key] == value)
                    for key, value in (attrs or {}).items()
                )

            def find(self, name=None, class_=None, attrs=None):
                return next(
                    (
                        tag for tag in self._tags
                        if self._matches(tag, name, class_, attrs)
                    ),
                    None,
                )

            def find_all(self, name=None, class_=None, attrs=None):
                return [
                    tag for tag in self._tags
                    if self._matches(tag, name, class_, attrs)
                ]

        bs4 = types.ModuleType("bs4")
        bs4.BeautifulSoup = _FallbackBeautifulSoup
        sys.modules["bs4"] = bs4
    if _missing_module("yfinance"):
        yfinance = types.ModuleType("yfinance")
        yfinance.Ticker = MagicMock()
        sys.modules["yfinance"] = yfinance
    if _missing_module("dotenv"):
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *args, **kwargs: False
        sys.modules["dotenv"] = dotenv
    if _missing_module("FinanceDataReader"):
        finance_data_reader = types.ModuleType("FinanceDataReader")
        finance_data_reader.DataReader = MagicMock()
        sys.modules["FinanceDataReader"] = finance_data_reader
