"""Dependency-free checks for the initial package layout."""

import importlib
import unittest


class PackageLayoutTests(unittest.TestCase):
    def test_packages_import(self):
        modules = (
            "solo_leveling",
            "solo_leveling.interfaces.mcp",
            "solo_leveling.interfaces.http",
            "solo_leveling.application",
            "solo_leveling.domain",
            "solo_leveling.infrastructure",
            "solo_leveling.workers",
        )
        for module_name in modules:
            with self.subTest(module=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))


if __name__ == "__main__":
    unittest.main()
