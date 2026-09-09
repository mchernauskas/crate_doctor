# Shim so `pip install .` works on older setuptools too. Real metadata lives in
# pyproject.toml, with setup.cfg as the fallback for versions that can't read it.
from setuptools import setup
setup()
