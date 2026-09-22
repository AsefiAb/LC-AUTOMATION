import copy
import unittest
from decimal import Decimal

from allocate_tips import allocate


class AllocationTests(unittest.TestCase):
    def inputs(self, hours, tips="100.00"):
        period = {"start_date": "2026-09-01", "end_date": "2026-09-14"}
        gateway = {"period": period, "verified": True, "stores": [{"store": "3356-01", "total_tips": tips}]}
        payroll = {"period": period.copy(), "verified": True, "employees": [
            {"employee_id": str(i), "store": "3356-01", "roles": ["Crew"], "total_hours": h}
            for i, h in enumerate(hours)
        ]}
        return gateway, payroll

    def test_matches_all_fifteen_screenshot_amounts(self):
        # Anonymous numeric fixture transcribed from the user's example.
        hours = ["59.51", "25.97", "76.08", "14.05", "41.71", "75.35", "29.36", "55.97", "52.49", "5.65", "35.21", "26.82", "70.55", "0.67", "14.02"]
        expected = ["78.17", "34.11", "99.94", "18.46", "54.79", "98.98", "38.57", "73.52", "68.95", "7.42", "46.25", "35.23", "92.67", "0.88", "18.42"]
        result = allocate(*self.inputs(hours, "766.36"))
        actual = {int(x["employee_id"]): x["tips"] for x in result["employees"]}
        self.assertEqual([actual[i] for i in range(15)], expected)
        self.assertEqual(result["stores"][0]["eligible_crew_hours"], "583.41")
        self.assertEqual(sum(Decimal(x["tips"]) for x in result["employees"]), Decimal("766.36"))

    def test_all_management_roles_excluded_from_denominator(self):
        g, p = self.inputs(["10.00"] * 4)
        for row, role in zip(p["employees"][1:], ["Manager", "Co-Manager", "Assistant Manager"]):
            row["roles"] = [role]
        result = allocate(g, p)
        self.assertEqual(result["stores"][0]["eligible_crew_hours"], "10.00")
        self.assertEqual([x["tips"] for x in result["employees"]], ["100.00", "0.00", "0.00", "0.00"])

    def test_stores_allocate_separately_and_manager_cannot_receive_elsewhere(self):
        g, p = self.inputs(["10.00", "10.00"])
        g["stores"].append({"store": "3356-02", "total_tips": "50.00"})
        p["employees"] += [{"employee_id": "0", "store": "3356-02", "roles": ["Manager"], "total_hours": "40.00"},
                           {"employee_id": "2", "store": "3356-02", "roles": ["Crew Member"], "total_hours": "5.00"}]
        result = allocate(g, p)
        tips = {(x["store"], x["employee_id"]): x["tips"] for x in result["employees"]}
        self.assertEqual(tips[("3356-01", "0")], "0.00")
        self.assertEqual(tips[("3356-01", "1")], "100.00")
        self.assertEqual(tips[("3356-02", "2")], "50.00")

    def test_leftover_penny_is_preserved(self):
        result = allocate(*self.inputs(["1.00"] * 3, "1.00"))
        self.assertEqual([x["tips"] for x in result["employees"]], ["0.34", "0.33", "0.33"])

    def test_shared_crew_member_uses_only_hours_from_each_store(self):
        g, p = self.inputs(["10.00", "30.00"], "100.00")
        g['stores'].append({'store': '3356-02', 'total_tips': '200.00'})
        p['employees'] += [
            {'employee_id': '0', 'store': '3356-02', 'roles': ['Crew'], 'total_hours': '30.00'},
            {'employee_id': '2', 'store': '3356-02', 'roles': ['Crew Leader'], 'total_hours': '10.00'}]
        result = allocate(g, p)
        tips = {(r['store'], r['employee_id']): r['tips'] for r in result['employees']}
        self.assertEqual(tips, {('3356-01', '0'): '25.00', ('3356-01', '1'): '75.00',
                                ('3356-02', '0'): '150.00', ('3356-02', '2'): '50.00'})

    def test_mismatched_dates_and_unverified_data_block_export(self):
        g, p = self.inputs(["10.00"])
        bad = copy.deepcopy(p)
        bad["period"]["end_date"] = "2026-09-13"
        with self.assertRaises(ValueError):
            allocate(g, bad)
        p["verified"] = False
        with self.assertRaises(ValueError):
            allocate(g, p)
        p["verified"] = True
        p["issues"] = ["September 14 calculated hours missing"]
        with self.assertRaises(ValueError):
            allocate(g, p)

    def test_no_eligible_hours_cannot_distribute_positive_pool(self):
        g, p = self.inputs(["0.00"])
        with self.assertRaises(ValueError):
            allocate(g, p)

    def test_unknown_role_and_duplicate_rows_are_not_silently_accepted(self):
        g, p = self.inputs(["10.00"])
        p["employees"][0]["roles"] = ["Unclassified title"]
        with self.assertRaises(ValueError):
            allocate(g, p)
        p["employees"][0]["roles"] = ["Crew"]
        p["employees"].append(copy.deepcopy(p["employees"][0]))
        with self.assertRaises(ValueError):
            allocate(g, p)


if __name__ == "__main__":
    unittest.main()
