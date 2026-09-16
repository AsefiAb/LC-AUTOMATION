import os
import unittest
from unittest.mock import patch

from tcp_inspect import TCP, InspectionError, period, project


class InspectionTests(unittest.TestCase):
    def test_projection_excludes_unnecessary_personal_data(self):
        result = project("hours", {
            "employee": {"employeeId": 12, "employeeName": "Example", "birthDate": "private", "authentication": {"pin": "private"}},
            "segments": [{"hours": {"regular": "8.00"}, "rate": "private", "jobCode": {"description": "Crew"}}],
        })
        self.assertNotIn("birthDate", result["employee"])
        self.assertNotIn("authentication", result["employee"])
        self.assertNotIn("rate", result["segments"][0])
        self.assertEqual(result["segments"][0]["hours"]["regular"], "8.00")

    def test_short_pages_do_not_truncate_collection(self):
        client = TCP()
        with patch.object(client, "request", side_effect=[
            {"data": [{"id": "1", "name": "Store A"}]},
            {"data": [{"id": "2", "name": "Store B"}]},
            {"data": []},
        ]) as request:
            self.assertEqual(len(client.collect("locations")), 2)
            self.assertEqual(request.call_count, 3)

    def test_partial_errors_are_not_reported_as_complete(self):
        client = TCP()
        with patch.object(client, "request", return_value={"data": [{"id": "1"}], "errors": [{"message": "failure"}]}):
            with self.assertRaises(InspectionError):
                client.collect("locations")

    def test_repeated_page_fails(self):
        client = TCP()
        with patch.object(client, "request", return_value={"data": [{"id": "1"}]}):
            with self.assertRaises(InspectionError):
                client.collect("locations")

    def test_period_validation(self):
        with patch.dict(os.environ, {"TCP_PAY_PERIOD_START": "2026-09-01", "TCP_PAY_PERIOD_END": "2026-09-14"}):
            self.assertEqual(period(), {"startDate": "2026-09-01", "stopDate": "2026-09-14"})
        with patch.dict(os.environ, {"TCP_PAY_PERIOD_START": "2026-09-01", "TCP_PAY_PERIOD_END": ""}):
            with self.assertRaises(InspectionError):
                period()


if __name__ == "__main__":
    unittest.main()
