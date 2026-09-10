"""Constants for the independent Route Optimizer module."""

COL_PLANS = "independent_route_plans"
COL_STOPS = "independent_route_stops"
COL_VERSIONS = "independent_route_versions"
SETTINGS_KEY = "independent_route"

PERM_VIEW = "route_optimizer.view"
PERM_CREATE = "route_optimizer.create"
PERM_EDIT_OWN = "route_optimizer.edit_own"
PERM_OPTIMIZE = "route_optimizer.optimize"
PERM_MANAGE = "route_optimizer.manage"
PERM_SETTINGS_VIEW = "settings.route_optimizer.view"
PERM_SETTINGS_MANAGE = "settings.route_optimizer.manage"
PERM_NAV = "nav.route_optimizer"

ROUTE_PERMS = (
    PERM_VIEW,
    PERM_CREATE,
    PERM_EDIT_OWN,
    PERM_OPTIMIZE,
    PERM_MANAGE,
    PERM_SETTINGS_VIEW,
    PERM_SETTINGS_MANAGE,
    PERM_NAV,
)

START_CURRENT = "CURRENT_LOCATION"
START_WAREHOUSE = "WAREHOUSE"
START_MANUAL = "MANUAL"
START_MODES = (START_CURRENT, START_WAREHOUSE, START_MANUAL)

END_LAST = "LAST_STOP"
END_RETURN_START = "RETURN_START"
END_WAREHOUSE = "WAREHOUSE"
END_MANUAL = "MANUAL"
END_MODES = (END_LAST, END_RETURN_START, END_WAREHOUSE, END_MANUAL)

STRATEGY_NEAREST = "NEAREST"
STRATEGY_FARTHEST = "FARTHEST"
STRATEGY_SHORTEST = "SHORTEST"
STRATEGIES = (STRATEGY_NEAREST, STRATEGY_FARTHEST, STRATEGY_SHORTEST)

STATUS_DRAFT = "DRAFT"
STATUS_OPTIMIZED = "OPTIMIZED"
STATUS_FINAL = "FINAL"
STATUSES = (STATUS_DRAFT, STATUS_OPTIMIZED, STATUS_FINAL)

LOC_MAP_PIN = "MAP_PIN"
LOC_MANUAL = "MANUAL_COORDINATE"
LOC_CURRENT = "CURRENT_LOCATION"
LOC_GEOCODER = "GEOCODER"
LOC_GOOGLE = "GOOGLE_PLACES"
LOCATION_SOURCES = (LOC_MAP_PIN, LOC_MANUAL, LOC_CURRENT, LOC_GEOCODER, LOC_GOOGLE)

REF_SALES_OUTLET = "SALES_OUTLET"
REF_DELIVERY_OUTLET = "DELIVERY_OUTLET"
REF_ROUTE_ONLY = "ROUTE_ONLY"
REF_KINDS = (REF_SALES_OUTLET, REF_DELIVERY_OUTLET, REF_ROUTE_ONLY)

SEQ_MANUAL = "MANUAL"
SEQ_OPTIMIZED = "OPTIMIZED"

SOURCE_MANUAL = "manual"
SOURCE_DELIVERY_SCHEDULE = "delivery_schedule"
SOURCE_TYPES = (SOURCE_MANUAL, SOURCE_DELIVERY_SCHEDULE)

OPT_STATUS_NONE = "belum_dioptimasi"
OPT_STATUS_OPTIMIZED = "sudah_dioptimasi"
OPT_STATUS_STALE = "perlu_optimasi_ulang"
OPT_STATUS_LABELS = {
    OPT_STATUS_NONE: "Belum Dioptimasi",
    OPT_STATUS_OPTIMIZED: "Sudah Dioptimasi",
    OPT_STATUS_STALE: "Perlu Optimasi Ulang",
}

EARTH_RADIUS_M = 6371000.0

DEFAULT_PRIORITIES = (
    {"id": "p1", "rank": 1, "label": "Urgent", "color": "#e11d48", "enabled": True},
    {"id": "p2", "rank": 2, "label": "High", "color": "#f59e0b", "enabled": True},
    {"id": "p3", "rank": 3, "label": "Normal", "color": "#0ea5e9", "enabled": True},
    {"id": "p4", "rank": 4, "label": "Low", "color": "#10b981", "enabled": True},
)
