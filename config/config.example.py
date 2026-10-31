# -*- coding: utf-8 -*-
"""Copy to config.local.py, then edit. Paths are relative to the working directory.

All algorithm thresholds retain the V3.4 defaults. Output overwrite is disabled
for the release example. Never point OUTPUT_GDB at an input or shared database.
"""

# =====================================================================
# 1. USER SETTINGS - normally edit only this section
# =====================================================================

# -----------------------------
# Input contour FileGDB + layer
# -----------------------------
CONTOUR_GDB = "data/REPLACE_WITH_CONTOURS.gdb"
CONTOUR_LAYER = "REPLACE_WITH_CONTOUR_LAYER"
ELEV_FIELD = "ELEV"
OBJE_CODE_FIELD = "OBJE_CODE"

# -----------------------------
# 1:10,000 standard map sheets
# -----------------------------
SHEET_GDB = "data/REPLACE_WITH_SHEETS.gdb"
SHEET_LAYER = "REPLACE_WITH_SHEET_LAYER"
TFBH_FIELD = "TFBH"

# -----------------------------
# Optional DEM fallback
# -----------------------------
# The program tries nearby-contour evidence first.
# DEM is used only if that evidence is not decisive.
USE_DEM_FALLBACK = True
DEM_PATH = "data/REPLACE_WITH_DEM.tif"

# -----------------------------
# Output FileGDB
# IMPORTANT: use a dedicated result GDB when OVERWRITE_OUTPUT_GDB=True.
# -----------------------------
OUTPUT_GDB = "outputs/contour_labels.gdb"
OUTPUT_MASTER_LAYER = "contour_label_master"
OUTPUT_INDEX_LAYER = "contour_label_index"
OUTPUT_REJECT_LAYER = "contour_label_reject"
OUTPUT_AUDIT_LAYER = "contour_label_line_audit"
OVERWRITE_OUTPUT_GDB = False
WRITE_REJECT_LAYER = True

# -----------------------------
# Processing scope
# -----------------------------
# "index_only" = only index contours (fastest; current production demand)
# "all"        = all contours
# "both"       = process all once, also write an index-contour subset
#
# Current production demand is index contours, so default is index_only for speed.
# Change to "all" or "both" later without changing the core algorithm.
LABEL_SCOPE = "index_only"

# Optional test mode.  None = all sheets.
# Example: ["YOUR_SHEET_ID"]
ONLY_TFBH = None

# -----------------------------
# Index contour identification
# -----------------------------
# Preferred production setting after confirmation, e.g. ["xxxx"] or [210101].
# Keep None for the first diagnostic run if the exact code is not yet known.
INDEX_OBJE_CODES = ["REPLACE_WITH_INDEX_CODE"]
AUTO_DETECT_INDEX_OBJE_CODE = False
# Production safeguard: code match AND 150 m elevation sequence must both agree.
INDEX_REQUIRE_ELEV_PATTERN = True

# Current contour interval = 30 m; index contour expected every 5 contours = 150 m.
# This is used ONLY to help identify which OBJE_CODE represents index contours.
CONTOUR_INTERVAL_M = 30.0
INDEX_CONTOUR_MULTIPLIER = 5
INDEX_ELEV_ORIGIN_M = 0.0
AUTO_CODE_MIN_PURITY = 0.95
AUTO_CODE_MIN_FEATURES = 5
AUTO_CODE_MIN_COMBINED_COVERAGE = 0.70
ALLOW_ELEVATION_ONLY_FALLBACK = False

# -----------------------------
# Scale / frame safety
# -----------------------------
SCALE_DENOMINATOR = 10000.0

# Default map annotation footprint: 5.669 mm; verify for your map.
# At 1:10,000 this corresponds to 56.69 m on the ground.
# If 5.669 mm is later found to be FONT HEIGHT rather than along-line text footprint,
# change LABEL_MAP_LENGTH_MM to the measured along-line footprint.
LABEL_MAP_LENGTH_MM = 5.669
LABEL_MAP_LENGTH_M = LABEL_MAP_LENGTH_MM / 1000.0
LABEL_GROUND_LENGTH_M = LABEL_MAP_LENGTH_M * SCALE_DENOMINATOR
LABEL_HALF_GROUND_M = 0.5 * LABEL_GROUND_LENGTH_M

# Map-frame safety is intentionally relaxed in V3.3.
# Old: 100 + 28.345 = 128.345 m, which rejected many visually usable positions.
# New: 15 + 28.345 = 43.345 m: half text footprint plus a small 1.5 mm map margin.
FRAME_BASE_INSET_M = 15.0
ADD_HALF_LABEL_TO_FRAME_MARGIN = True

# -----------------------------
# Natural contour connectivity
# -----------------------------
# Only true endpoint contacts are merged. River/reservoir gaps are never bridged.
MERGE_ENDPOINT_TOL_M = 0.01
UID_GRID_SIZE_M = 0.01

# -----------------------------
# Dense candidate search - V3 core
# -----------------------------
# Candidate centres are sampled every 25 m. This is less than half of the 56.69 m
# label footprint, so a usable straight reach is difficult to miss by sampling phase.
CANDIDATE_STEP_M = 25.0

# Open contours: require the full 56.69 m label footprint to fit, plus 5 m extra.
# Old 113.38 m head/tail exclusion was too strict. Closed contours have no endpoint rule.
MIN_END_CLEARANCE_M = LABEL_HALF_GROUND_M + 5.0
MIN_LOGICAL_LINE_LENGTH_M = 2.0 * MIN_END_CLEARANCE_M

# Rotation tangent and straightness are evaluated from resampled geometry, not raw
# vertex spacing, so dense/noisy source vertices do not dominate the decision.
TANGENT_HALF_WINDOW_M = max(20.0, 0.35 * LABEL_GROUND_LENGTH_M)
STRAIGHTNESS_CONTEXT_HALF_M = LABEL_HALF_GROUND_M  # hard geometry only over the actual 56.69 m label footprint
STRAIGHTNESS_SAMPLE_STEP_M = 10.0

# Hard limits: obvious corners are rejected. Preferred limits define the best class;
# if a line has no preferred site, an acceptable (but still non-corner) site can win.
HARD_MAX_BEND_DEG = 35.0
HARD_MIN_CHORD_RATIO = 0.90
HARD_MAX_TOTAL_TURN_DEG = 50.0
HARD_MAX_STEP_TURN_DEG = 30.0

PREFERRED_MAX_BEND_DEG = 10.0
PREFERRED_MIN_CHORD_RATIO = 0.985
PREFERRED_MAX_TOTAL_TURN_DEG = 25.0
PREFERRED_MAX_STEP_TURN_DEG = 12.0

# Final density rules.
# Default: at most one label for the same logical contour within one 1:10,000 sheet.
MAX_LABELS_PER_LINE_PER_SHEET = 1
# Even if the same contour folds back close to itself, or a natural gap creates two
# nearby pieces of the same elevation, do not keep two equal-elevation labels locally.
MIN_SAME_ELEV_LABEL_DISTANCE_M = max(800.0, 12.0 * LABEL_GROUND_LENGTH_M)

# Mandatory production coverage: every index-contour elevation that actually exists
# in the source data must retain at least this many final labels citywide.
MIN_LABELS_PER_INDEX_ELEV = 1
REQUIRE_INDEX_ELEVATION_COVERAGE = True


# -----------------------------
# Text-top direction
# -----------------------------
# TOP_AZ is the normal to the text baseline and must point to higher terrain.
# Only the northward cone within 75 degrees of north is allowed: this excludes exact
# east/west, southeast, southwest and south. <=45 degrees from north ranks highest.
PREFER_NORTHWARD_TEXT_TOP = True
PREFERRED_TEXT_TOP_AZ_DEG = 0.0
REQUIRE_TEXT_TOP_NORTH_CONE = True
MAX_TEXT_TOP_DEVIATION_FROM_NORTH_DEG = 75.0
BEST_TEXT_TOP_DEVIATION_FROM_NORTH_DEG = 45.0

# Prefer points farther inside the map sheet after the hard frame margin is passed.
PREFER_LARGER_FRAME_CLEARANCE = True

# -----------------------------
# Adjacent-contour higher-side judgement (PRIMARY)
# -----------------------------
# For each candidate we cast short rays along the LOCAL left/right normals and use
# only the FIRST different-elevation contour reached on each side.  This mimics
# manual contour reading and prevents a farther fold/ridge/valley from out-voting
# the immediately adjacent 930/870-type relationship.
NORMAL_RAY_MAX_M = 350.0
NORMAL_RAY_MIN_HIT_M = 3.0
NORMAL_RAY_QUERY_BUFFER_M = 0.25
NORMAL_SECTION_ALONG_OFFSETS_M = [-0.5 * LABEL_GROUND_LENGTH_M, 0.0, 0.5 * LABEL_GROUND_LENGTH_M]
MIN_ADJACENT_SECTION_VOTES = 2
MIN_ADJACENT_SIDE_CONSISTENCY = 0.67

# One-sided evidence is allowed.  Example: for a closed 1050 contour, if the first
# contour outside is 1020 and no different-elevation contour is reached inward, the
# opposite/inward side is inferred to be higher.  Such a one-sided inference gets
# lower confidence and is preferably checked by the DEM.
ALLOW_ONE_SIDED_ADJACENT_EVIDENCE = True
ONE_SIDED_SECTION_WEIGHT = 0.75
TWO_SIDED_SECTION_WEIGHT = 1.00

# Same-elevation nearby pieces never determine higher/lower side.
ELEV_EQUAL_TOL = 1.0e-6

# Validate adjacent-contour decisions with the DEM when possible.  If DEM evidence
# is unavailable, a consistent adjacent-contour decision is retained.  If both are
# decisive but opposite, the candidate is rejected and another position is sought.
VALIDATE_ADJACENT_SIDE_WITH_DEM = True
REJECT_CONTOUR_DEM_CONFLICT = True

# -----------------------------
# DEM fallback higher-side judgement
# -----------------------------
SIDE_DISTANCES_M = [20.0, 30.0, 50.0, 80.0]
ALONG_OFFSETS_M = [-0.5 * LABEL_GROUND_LENGTH_M, 0.0, 0.5 * LABEL_GROUND_LENGTH_M]
MIN_SIDE_DZ_M = 0.5
MIN_VALID_SIDE_VOTES = 2
MIN_DEM_SIDE_CONSISTENCY = 0.60

# -----------------------------
# QA / performance
# -----------------------------
VERBOSE_EVERY_LINES = 500
