# Changelog

## 1.0.0 — First Public Release

First public release of NISAR Processor. Includes GCOV inspection, dataset/band discovery, scientific extraction, QC, statistics, processing reports, visualization, RGB workflow, and derived speckle-smoothing tools.

### 1.0.0 additions

- Added vertical left-side module navigation.
- Added Scientific Data workspace.
- Added dataset-level Quality Control (QC).
- Added basic raster statistics.
- Added reproducible scientific processing reports.
- Added derived Median and Mean (Boxcar) speckle-smoothing filters with 3×3, 5×5 and 7×7 windows.
- Added optional pre-RGB speckle filtering of the underlying HHHH/HVHV power layers before calculating the derived RGB ratio and display stretch.
- Added explicit GCOV dataset/frequency/band terminology and L-band/S-band documentation.
- Clarified scientific-data versus derived visualization products.

Development history — 0.4.2 (pre-1.0.0) — 2026-09-15

- Compact main interface.
- Removed the large introductory header/banner.
- Reduced spacing and margins for a smaller working dialog.
- Added/retained scrollable Messages panel.
- Retained all 0.4.0 functionality.

### 0.4.0 (pre-1.0.0)

- Added multi-band scientific dataset export.
- Added dual-pol RGB Composite workflow.
- Added HHHH/HVHV/Mask/Number of Looks shortcuts.
- Added dB and linear RGB display domains.
- Added independent 2–98 percentile channel stretching.
- Added derived HH/HV ratio RGB channel.
- Added visualization controls.

### 0.3.0 (pre-1.0.0)

- Added SAR visualization tab.
- Added automatic 2–98 percentile stretch.
- Added pseudocolor visualization.
- Added linear grayscale visualization.
- Added automatic visualization after extraction.

### 0.2.0 (pre-1.0.0)

- Added dedicated NISAR Processor toolbar.
- Added product inspection and GDAL detection.
- Added HDF5/NETCDF dataset discovery.
- Added scientific raster extraction.
- Added full-scene/current-map-extent processing.
- Added Float32 GeoTIFF output.
- Added CRS/georeferencing validation.
- Added progress reporting and cancellation.
