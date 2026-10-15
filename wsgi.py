"""WSGI entry point (used by PythonAnywhere and other WSGI hosts)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app as application  # noqa: E402
