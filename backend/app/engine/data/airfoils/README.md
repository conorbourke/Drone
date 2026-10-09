# Airfoil coordinates

Unit-chord section coordinates in Selig format (a name line, then `x y` pairs from the trailing
edge over the upper surface to the leading edge and back along the lower surface).

| File | Source |
|---|---|
| `sd7037.dat`, `sd7062.dat`, `e387.dat`, `mh32.dat`, `s3021.dat`, `ag35.dat`, `clarky.dat` | UIUC Airfoil Coordinates Database (Michael Selig, University of Illinois at Urbana-Champaign, https://m-selig.ae.illinois.edu/ads/coord_database.html), copied unchanged from the copy bundled with AeroSandbox 4.2.10 (`aerosandbox/geometry/airfoil/airfoil_database/`, MIT licence). |
| `naca2412.dat`, `naca4412.dat`, `naca0009.dat`, `naca0012.dat` | Generated from the NACA 4-digit equations (Abbott and von Doenhoff, *Theory of Wing Sections*) by `app.engine.airfoils.naca4`: 160 panels, cosine spacing, standard open trailing edge. Rewritten by `scripts/build_airfoil_tables.py`. |

Polars for every section are in `../airfoil_polars.json`, built with XFOIL by
`backend/scripts/build_airfoil_tables.py` (see that file for the settings).
