import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AgaunewsCoverageTests(unittest.TestCase):
    def test_every_named_guide_entry_has_a_real_configured_collection_route(self):
        sources = json.loads((ROOT / "sources.json").read_text())
        by_id = {s["id"]: s for s in sources}
        self.assertEqual(len(by_id), len(sources))
        entries = json.loads((ROOT / "docs/agaunews-coverage.json").read_text())["entries"]
        self.assertEqual(len(entries), 50)
        self.assertEqual(len({e["guideName"] for e in entries}), 50)
        for entry in entries:
            source = by_id[entry["sourceId"]]
            self.assertEqual(source["guideName"], entry["guideName"])
            if entry["mode"] == "direct":
                self.assertNotEqual(source["type"], "directory")
                self.assertTrue(source["endpoint"].startswith("https://"))
            else:
                self.assertTrue(source.get("dealerFeeds"))
                for reference in source["dealerFeeds"]:
                    dealer = by_id[reference["sourceId"]]
                    self.assertNotEqual(dealer["type"], "directory")
                    if reference.get("feedId"):
                        feed = next(f for f in dealer["discoveryFeeds"] if f["id"] == reference["feedId"])
                        self.assertTrue(feed.get("maker"))
        self.assertNotEqual(by_id['mint-of-mk']['id'], by_id['mk-barz']['id'])
        self.assertNotEqual(by_id['bank-of-mexico']['id'], by_id['mexico-mint']['id'])


if __name__ == '__main__':
    unittest.main()
