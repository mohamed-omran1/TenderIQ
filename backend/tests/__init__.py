"""Marker so `tests` is an importable package.

Lets test modules do `from tests.conftest import <helper>` regardless of the
directory pytest was invoked from. No test code lives here.
"""
