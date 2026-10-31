"""Synthetic geometry only: no real GDB, DEM or production records."""
import math
import unittest

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, box

import contour_label as c


def lines(geometries, elevations=None):
    n = len(geometries)
    return gpd.GeoDataFrame({
        c.ELEV_FIELD: elevations if elevations is not None else [150.0] * n,
        c.OBJE_CODE_FIELD: ["SYNTHETIC_INDEX"] * n,
        "SRC_FID": list(range(n)),
    }, geometry=geometries, crs="EPSG:3857")


class AngleTests(unittest.TestCase):
    def test_cardinal_azimuths(self):
        for xy, expected in [((0, 1), 0), ((1, 0), 90), ((0, -1), 180), ((-1, 0), 270)]:
            with self.subTest(xy=xy):
                self.assertAlmostEqual(c.geographic_azimuth(Point(0, 0), Point(*xy)), expected)

    def test_diagonal_and_coincident_points(self):
        self.assertAlmostEqual(c.geographic_azimuth(Point(0, 0), Point(1, 1)), 45)
        self.assertEqual(c.geographic_azimuth(Point(0, 0), Point(0, 0)), 0)

    def test_wraparound_difference(self):
        self.assertEqual(c.angular_diff(359, 1), 2)
        self.assertEqual(c.angular_diff(0, 180), 180)
        self.assertEqual(c.angular_diff(-10, 350), 0)

    def test_left_and_right_high_side_to_top(self):
        for tangent, side, top in [(90, "LEFT", 0), (270, "RIGHT", 0),
                                   (45, "LEFT", 315), (315, "RIGHT", 45)]:
            with self.subTest(tangent=tangent, side=side):
                ok, reject = c.orient_record({"TANG_AZ": tangent, c.ELEV_FIELD: 150}, side, "SYNTHETIC", 1, 3)
                self.assertIsNone(reject)
                self.assertEqual(ok["TOP_AZ"], top)
                self.assertEqual(ok["LABEL_TXT"], "150")
                self.assertEqual(ok["HIGH_SIDE"], side)

    def test_southward_top_is_rejected_without_flip(self):
        ok, reject = c.orient_record({"TANG_AZ": 90, c.ELEV_FIELD: 150}, "RIGHT", "SYNTHETIC", 1, 3)
        self.assertIsNone(ok)
        self.assertEqual(reject["TOP_AZ"], 180)
        self.assertEqual(reject["REJECT_REASON"], "TEXT_TOP_OUTSIDE_NORTH_CONE")

    def test_north_cone_boundaries(self):
        for az, expected in [(0, 2), (45, 2), (315, 2), (45.001, 1), (75, 1),
                             (285, 1), (75.001, 0), (90, 0), (180, 0), (270, 0), (360, 2)]:
            with self.subTest(az=az):
                self.assertEqual(c.text_top_direction_class(az), expected)

    def test_direction_preference(self):
        self.assertAlmostEqual(c.northward_preference_score(0), 1)
        self.assertAlmostEqual(c.northward_preference_score(90), .5)
        self.assertAlmostEqual(c.northward_preference_score(180), 0)


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.line = LineString([(0, 0), (1000, 0)])
        self.ring = LineString([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])

    def test_open_line_candidate_clearance(self):
        stations = c.candidate_stations(self.line, 0)
        self.assertIn(500, stations)
        # Original code rounds stations to millimetres.
        self.assertTrue(all(min(s, self.line.length - s) >= c.MIN_END_CLEARANCE_M - .0005 for s in stations))

    def test_short_open_line_has_no_candidates(self):
        length = 2 * c.MIN_END_CLEARANCE_M
        self.assertEqual(c.candidate_stations(LineString([(0, 0), (length, 0)]), 0), [])

    def test_endpoint_geometry_exclusion(self):
        self.assertIsNone(c.local_geometry_metrics(self.line, c.MIN_END_CLEARANCE_M - .01, 0))
        self.assertIsNone(c.local_geometry_metrics(self.line, 1000 - c.MIN_END_CLEARANCE_M + .01, 0))
        self.assertIsNotNone(c.local_geometry_metrics(self.line, c.MIN_END_CLEARANCE_M, 0))

    def test_closed_line_stations_and_wrap(self):
        stations = c.candidate_stations(self.ring, 1)
        self.assertTrue(self.ring.is_ring)
        self.assertTrue(any(s < c.MIN_END_CLEARANCE_M for s in stations))
        self.assertTrue(c._interp_station(self.ring, -10, 1).equals(c._interp_station(self.ring, 390, 1)))
        self.assertTrue(c._interp_station(self.ring, 410, 1).equals(c._interp_station(self.ring, 10, 1)))

    def test_closed_line_no_endpoint_clearance(self):
        circle = Point(0, 0).buffer(500, quad_segs=128).exterior
        metrics = c.local_geometry_metrics(LineString(circle.coords), 0, 1)
        self.assertIsNotNone(metrics)
        self.assertTrue(metrics["hard_ok"])
        self.assertTrue(math.isnan(metrics["end_clear_m"]))

    def test_short_closed_ring_has_no_candidates(self):
        small = LineString([(0, 0), (1, 0), (1, 1), (0, 0)])
        self.assertEqual(c.candidate_stations(small, 1), [])

    def test_straight_line_metrics(self):
        metrics = c.local_geometry_metrics(self.line, 500, 0)
        self.assertTrue(metrics["hard_ok"])
        self.assertEqual(metrics["flat_class"], 2)
        self.assertAlmostEqual(metrics["tangent_az"], 90)
        self.assertAlmostEqual(metrics["chord_ratio"], 1)
        self.assertAlmostEqual(metrics["bend_deg"], 0)

    def test_right_angle_corner_rejected(self):
        corner = LineString([(0, 0), (500, 0), (500, 500)])
        metrics = c.local_geometry_metrics(corner, 500, 0)
        self.assertFalse(metrics["hard_ok"])
        self.assertEqual(c._geometry_reject_reason(metrics), "LOCAL_BEND_TOO_HIGH")

    def test_exact_endpoint_merge_and_natural_gap(self):
        connected = lines([LineString([(0, 0), (100, 0)]), LineString([(100, 0), (200, 0)])])
        merged = c.merge_connected_group(connected)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged.iloc[0]["SRC_CNT"], 2)
        self.assertAlmostEqual(merged.geometry.iloc[0].length, 200)
        gap = lines([LineString([(0, 0), (100, 0)]), LineString([(101, 0), (200, 0)])])
        self.assertEqual(len(c.merge_connected_group(gap)), 2)

    def test_branch_endpoint_does_not_merge(self):
        branch = lines([LineString([(0, 0), (100, 0)]), LineString([(100, 0), (200, 0)]),
                        LineString([(100, 0), (100, 100)])])
        self.assertEqual(len(c.merge_connected_group(branch)), 3)

    def test_closed_line_metadata(self):
        result = c.merge_connected_group(lines([self.ring]))
        self.assertEqual(result.iloc[0]["IS_CLOSED"], 1)
        self.assertEqual(len(result.iloc[0]["LINE_UID"]), 18)

    def test_uid_reversed_geometry(self):
        reverse = LineString(list(self.line.coords)[::-1])
        self.assertEqual(c.stable_geom_uid(self.line, 150, "SYNTHETIC"), c.stable_geom_uid(reverse, 150, "SYNTHETIC"))


class EvidenceTests(unittest.TestCase):
    def test_first_adjacent_contour_wins_over_farther_contour(self):
        evidence = lines([LineString([(-1000, y), (1000, y)]) for y in [0, 50, -50, 100]],
                         [150, 180, 120, 90])
        result = c.ContourSideIndex(evidence).judge(Point(0, 0), 90, 150)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["higher_side"], "LEFT")
        self.assertEqual(result["near_l_z"], 180)
        self.assertEqual(result["near_r_z"], 120)
        self.assertEqual(result["two_sided_sections"], 3)

    def test_one_sided_lower_contour_infers_opposite_high_side(self):
        side, weight, reason = c.ContourSideIndex._vote_from_hits(150, (20, 120, 0), None)
        self.assertEqual(side, "RIGHT")
        self.assertEqual(reason, "LEFT_ONLY")
        self.assertGreater(weight, 0)

    def test_both_sides_above_are_ambiguous(self):
        side, weight, reason = c.ContourSideIndex._vote_from_hits(150, (20, 180, 0), (20, 180, 1))
        self.assertIsNone(side)
        self.assertEqual(weight, 0)
        self.assertEqual(reason, "AMBIGUOUS_BOTH_SIDES")

    def test_frame_margin(self):
        sheets = gpd.GeoDataFrame({c.TFBH_FIELD: ["SYNTHETIC_SHEET"]}, geometry=[box(0, 0, 1000, 1000)], crs="EPSG:3857")
        assigner = c.SheetAssigner(sheets)
        self.assertAlmostEqual(c.frame_safe_margin_m(), 43.345)
        self.assertEqual(assigner.assign(Point(500, 500))["status"], "OK")
        self.assertEqual(assigner.assign(Point(10, 500))["status"], "TOO_CLOSE_TO_FRAME")
        self.assertEqual(assigner.assign(Point(-1, 500))["status"], "OUTSIDE_STANDARD_SHEETS")

    def test_index_code_and_elevation_both_required(self):
        contours = lines([LineString([(0, i), (100, i)]) for i in range(3)], [150, 180, 300])
        contours.loc[2, c.OBJE_CODE_FIELD] = "SYNTHETIC_OTHER"
        result = c.mark_index_contours(contours, {"SYNTHETIC_INDEX"})
        self.assertEqual(result["IS_INDEX"].tolist(), [1, 0, 0])
        self.assertEqual(result["INDEX_CONFLICT"].tolist(), [0, 1, 0])

    def test_elevation_mask_and_normalization(self):
        np.testing.assert_array_equal(c.expected_index_elevation_mask(pd.Series([0, 150, 180, 300, np.nan])),
                                      [True, True, False, True, False])
        self.assertEqual(c.normalize_code(42.0), "42")
        self.assertIsNone(c.normalize_code(np.nan))
        self.assertEqual(c.format_elev(150.125), "150.125")

    def test_same_line_and_elevation_thinning_protects_coverage(self):
        def record(uid, station, x, score, elev=150):
            return {"LINE_UID": uid, c.TFBH_FIELD: "SYNTHETIC_SHEET", c.ELEV_FIELD: elev,
                    "STATION_M": station, "LINE_LEN": 1000, "UP_SCORE": score,
                    "TOP_AZ": 0, "geometry": Point(x, 0)}
        records = [record("a", 100, 100, 1), record("a", 200, 200, .9),
                   record("b", 100, 300, .8), record("c", 100, 2000, .7)]
        rejects = []
        kept, missing = c.select_labels_with_elevation_coverage(records, rejects, [150, 300])
        self.assertEqual({r["LINE_UID"] for r in kept}, {"a", "c"})
        self.assertEqual(missing, [300])
        self.assertEqual(sum(r["COVERAGE_SEED"] for r in kept), 1)
        self.assertEqual({r["REJECT_REASON"] for r in rejects},
                         {"NOT_SELECTED_SAME_LINE_SHEET", "NOT_SELECTED_LOCAL_SAME_ELEV"})


if __name__ == "__main__":
    unittest.main()
