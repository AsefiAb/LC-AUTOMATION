import copy
import unittest

from summarize_inspection import minutes, store_label, summarize


class SummaryTests(unittest.TestCase):
    def fixture(self):
        record = {"recordId": 1, "timeIn": "2026-09-14T20:00:00", "timeOut": "2026-09-15T01:00:00",
                  "jobCode": {"jobCodeId": 10}, "hours": {"regular": "4:00", "overtime1": "1:00", "overtime2": "0:00"}}
        records = {
            "locations": [{"name": "03356-00001"}],
            "jobcodes": [{"jobCodeId": 10, "description": "Assistant Manager - 3356-01", "locationName": "03356-00001"}],
            "employees": [],
            "hours": [{"employee": {"employeeId": 20, "employeeName": "Example"}, "segments": [record]}],
            "worksegments": [{"id": "1", "timeIn": record["timeIn"]}],
        }
        return {"period": {"startDate": "2026-09-01", "stopDate": "2026-09-14"}, "generated_at": "2026-09-16",
                "datasets": {name: {"complete": True, "records": values} for name, values in records.items()}}

    def test_minutes_are_not_decimal_hours(self):
        self.assertEqual(minutes("7:30"), 450)
        self.assertEqual(minutes("7.50"), 450)
        self.assertEqual(store_label("03356-00001"), "3356-01")

    def test_shift_start_includes_overnight_but_not_next_day(self):
        report = self.fixture()
        extra = copy.deepcopy(report["datasets"]["hours"]["records"][0]["segments"][0])
        extra.update(recordId=2, timeIn="2026-09-15T10:00:00", timeOut="2026-09-15T15:00:00")
        report["datasets"]["hours"]["records"][0]["segments"].append(extra)
        result = summarize(report)
        self.assertEqual(result["stores"][0]["regular_minutes"], 240)
        self.assertEqual(result["stores"][0]["ot1_minutes"], 60)
        self.assertEqual(result["employees"][0]["roles"], "Assistant Manager")
        self.assertIsNone(result["stores"][0]["tips"])

    def test_open_punch_is_not_counted_as_completed_work(self):
        report = self.fixture()
        report["datasets"]["hours"]["records"][0]["segments"][0]["timeOut"] = None
        result = summarize(report)
        self.assertEqual(result["stores"][0]["regular_minutes"], 0)
        self.assertEqual(result["stores"][0]["open_punches"], 1)

    def test_duplicate_segments_fail(self):
        report = self.fixture()
        rows = report["datasets"]["hours"]["records"][0]["segments"]
        rows.append(copy.deepcopy(rows[0]))
        with self.assertRaises(ValueError):
            summarize(report)

    def test_elapsed_shift_with_zero_hours_is_flagged(self):
        report = self.fixture()
        report["datasets"]["hours"]["records"][0]["segments"][0]["hours"] = {"regular": "0:00", "overtime1": "0:00", "overtime2": "0:00"}
        result = summarize(report)
        self.assertEqual(result["checks"]["zero_hour_closed_shifts"], 1)
        self.assertEqual(result["segments"][0]["category"], "Zero-hour shift")


if __name__ == "__main__":
    unittest.main()
