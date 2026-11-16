import datetime as dt
import unittest

from check_crate_age import index_path, registry_packages, too_new

LOCK = """
version = 4

[[package]]
name = "axum"
version = "0.8.9"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "memories-server"
version = "6.0.0"

[[package]]
name = "ab"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""

NOW = dt.datetime(2026, 11, 16, tzinfo=dt.timezone.utc)


class CrateAgeTest(unittest.TestCase):
    def test_registry_packages_skip_local_crates(self):
        self.assertEqual(registry_packages(LOCK), [("axum", "0.8.9"), ("ab", "1.0.0")])

    def test_index_path(self):
        self.assertEqual(index_path("a"), "1/a")
        self.assertEqual(index_path("ab"), "2/ab")
        self.assertEqual(index_path("abc"), "3/a/abc")
        self.assertEqual(index_path("Axum"), "ax/um/axum")

    def test_too_new(self):
        self.assertTrue(too_new("2026-11-15T00:00:00Z", NOW, days=2))
        self.assertFalse(too_new("2026-11-14T00:00:00Z", NOW, days=2))
        self.assertFalse(too_new(None, NOW, days=2))


if __name__ == "__main__":
    unittest.main()
