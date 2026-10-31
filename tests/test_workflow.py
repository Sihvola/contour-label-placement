"""Small synthetic FileGDB round trips; skipped if the driver cannot write.

All datasets are created under TemporaryDirectory and are never repository data.
"""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, box

import contour_label as c


@unittest.skipUnless("w" in pyogrio.list_drivers().get("OpenFileGDB", ""), "OpenFileGDB write driver unavailable")
class SyntheticWorkflowTests(unittest.TestCase):
    def run_workflow(self, scope, with_dem=False):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source, output = base / "synthetic_input.gdb", base / "synthetic_output.gdb"
            contours = gpd.GeoDataFrame({"ELEV": [120., 150., 180.],
                                          "OBJE_CODE": ["SYNTHETIC_INDEX"] * 3},
                                         geometry=[LineString([(0, y), (2000, y)]) for y in (800, 1000, 1200)],
                                         crs="EPSG:3857")
            sheets = gpd.GeoDataFrame({"TFBH": ["SYNTHETIC_SHEET"]},
                                      geometry=[box(0, 0, 2000, 2000)], crs=contours.crs)
            pyogrio.write_dataframe(contours, source, layer="contours", driver="OpenFileGDB")
            pyogrio.write_dataframe(sheets, source, layer="sheets", driver="OpenFileGDB")
            dem_path = base / "synthetic_dem.tif"
            if with_dem:
                # A smooth north-rising surface; no real terrain information.
                values = np.repeat((50 + (2000 - (np.arange(200) + .5) * 10) / 10)[:, None], 200, axis=1).astype("float32")
                with rasterio.open(dem_path, "w", driver="GTiff", height=200, width=200,
                                   count=1, dtype="float32", crs=contours.crs,
                                   transform=from_origin(0, 2000, 10, 10), nodata=-9999) as dem:
                    dem.write(values, 1)
            with patch.multiple(c, CONTOUR_GDB=str(source), CONTOUR_LAYER="contours",
                                SHEET_GDB=str(source), SHEET_LAYER="sheets", OUTPUT_GDB=str(output),
                                INDEX_OBJE_CODES=["SYNTHETIC_INDEX"], LABEL_SCOPE=scope,
                                USE_DEM_FALLBACK=with_dem, DEM_PATH=str(dem_path),
                                OVERWRITE_OUTPUT_GDB=False, ONLY_TFBH=["SYNTHETIC_SHEET"]):
                c.preflight_config()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    c.main()
            master = pyogrio.read_dataframe(output, layer="contour_label_master")
            self.assertEqual(set(master["ELEV"]), {150} if scope == "index_only" else {120, 150, 180})
            self.assertTrue((master["ANGLE_GEO"] == 90).all())
            self.assertTrue((master["TOP_AZ"] == 0).all())
            self.assertTrue((master["HIGH_SIDE"] == "LEFT").all())
            self.assertEqual(set(master["OBJE_CODE"]), {"SYNTHETIC_INDEX"})
            self.assertEqual(set(master["TFBH"]), {"SYNTHETIC_SHEET"})
            if with_dem:
                self.assertTrue(master["SIDE_SRC"].str.contains("DEM").all())
            available = set(pyogrio.list_layers(output)[:, 0])
            self.assertIn("contour_label_reject", available)
            self.assertIn("contour_label_line_audit", available)
            if scope in {"both", "index_only"}:
                index = pyogrio.read_dataframe(output, layer="contour_label_index")
                self.assertEqual(set(index["ELEV"]), {150})
            else:
                self.assertNotIn("contour_label_index", available)
            coverage = pd.read_csv(base / "contour_label_elevation_coverage.csv")
            self.assertEqual(coverage["COVERED"].tolist(), [1])
            self.assertTrue((base / "OBJE_CODE_index_contour_diagnostic.csv").is_file())
            self.assertTrue((base / "contour_label_summary.csv").is_file())

    def test_index_only_without_dem(self):
        self.run_workflow("index_only")

    def test_all_without_dem(self):
        self.run_workflow("all")

    def test_both_without_dem(self):
        self.run_workflow("both")

    def test_both_with_synthetic_dem(self):
        self.run_workflow("both", with_dem=True)


if __name__ == "__main__":
    unittest.main()
