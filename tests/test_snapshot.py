import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect import read_snapshot, write_snapshot


class SnapshotStorageTests(unittest.TestCase):
    def test_legacy_migration_preserves_unicode_dates_ids_and_history(self):
        sample = {"products": [{"id": "mint:1", "title": "Gdańsk", "firstSeenAt": "2026-11-13T00:00:00Z",
                                "history": [{"price": 101.25, "status": "available"}]}], "sources": []}
        with tempfile.TemporaryDirectory() as folder:
            legacy = Path(folder) / "releases.json"
            compressed = Path(folder) / "releases.json.gz"
            legacy.write_text(json.dumps(sample), encoding="utf-8")
            self.assertEqual(read_snapshot(compressed), sample)
            write_snapshot(compressed, read_snapshot(compressed))
            legacy.unlink()
            self.assertEqual(read_snapshot(compressed), sample)
            self.assertEqual(json.loads(gzip.decompress(compressed.read_bytes())), sample)

    def test_plain_diagnostics_and_deterministic_compression(self):
        with tempfile.TemporaryDirectory() as folder:
            plain, zipped = Path(folder) / "diagnostic.json", Path(folder) / "snapshot.json.gz"
            self.assertEqual(read_snapshot(zipped), {})
            write_snapshot(plain, {"products": []})
            write_snapshot(zipped, read_snapshot(plain))
            original = zipped.read_bytes()
            write_snapshot(zipped, read_snapshot(plain))
            self.assertEqual(zipped.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
