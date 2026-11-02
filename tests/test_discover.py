"""Offline tests of scripts/discover_records.py (no network)."""
import sys, unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import discover_records as d  # noqa: E402

AIRPORTS = """id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,iso_country,iso_region,municipality,scheduled_service,gps_code,iata_code,local_code
1,KSBN,medium_airport,South Bend International Airport,41.7087,-86.3173,799,NA,US,US-IN,South Bend,yes,KSBN,SBN,SBN
2,KORD,large_airport,Chicago O'Hare International Airport,41.9786,-87.9048,672,NA,US,US-IL,Chicago,yes,KORD,ORD,ORD
3,KXYZ,small_airport,Tiny Strip,40.0,-90.0,500,NA,US,US-IL,Nowhere,no,KXYZ,XYZ,XYZ
4,TJSJ,large_airport,San Juan,18.4,-66.0,9,NA,US,US-PR,San Juan,yes,TJSJ,SJU,SJU
5,CYYZ,large_airport,Toronto Pearson,43.67,-79.63,569,NA,CA,CA-ON,Toronto,yes,CYYZ,YYZ,YYZ
"""


class FakeResp:
    def __init__(self, p): self.p = p
    def raise_for_status(self): pass
    def json(self): return self.p


class FakeSession:
    def post(self, url, json=None, timeout=None):
        sid = json["sid"]
        if sid == "SBNthr 9":
            return FakeResp({"meta": {"name": "South Bend Area", "state": "IN"},
                             "data": [["2026-10-30", "61"], ["2026-10-31", "M"]]})
        if sid == "ORDthr 9":                      # exists but nothing recent: not kept
            return FakeResp({"meta": {"name": "Chicago Area"}, "data": [["2026-10-30", "M"]]})
        return FakeResp({"error": "no data available"})


class Test(unittest.TestCase):
    def test_only_us_state_airports_with_codes_and_service(self):
        ap = d.candidate_airports(AIRPORTS)
        self.assertEqual(sorted(ap), ["ORD", "SBN"])          # no tiny strip, Puerto Rico or Canada
        self.assertEqual(ap["SBN"]["state"], "IN")

    def test_probe_keeps_records_with_current_data_only(self):
        s, today = FakeSession(), date(2026, 11, 1)
        hit = d.probe(s, "SBN", today)
        self.assertEqual((hit["sid"], hit["acis_name"], hit["latest"]), ("SBNthr 9", "South Bend Area", "2026-10-30"))
        self.assertIsNone(d.probe(s, "ORD", today))
        self.assertIsNone(d.probe(s, "QQQ", today))


if __name__ == "__main__":
    unittest.main()
