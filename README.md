# NISAR Processor

**NISAR Processor** is a NISAR Level-2 GCOV product inspection, scientific raster extraction, visualization, and analysis-ready raster export toolbox for QGIS.

It is designed for **NISAR Level-2 GCOV products** and is intentionally **product-aware**: it inspects the product structure and dataset-level georeferencing before producing outputs. The plugin does not modify the source NISAR HDF5 product and does not manually assign a CRS when the source metadata is invalid or unavailable.

## Version

**1.0.0**

## Main capabilities

* Dedicated **NISAR Processor** toolbar and compact graphical interface
* NISAR product inspection
* GDAL version detection
* HDF5/NETCDF dataset discovery
* Dataset Explorer for navigating NISAR subdatasets
* Quick access to common Frequency A GCOV datasets:

  * HHHH
  * HVHV
  * Mask
  * Number of Looks
* Single-band and multi-band scientific raster extraction
* Full-scene or current-QGIS-map-extent extraction
* Float32 GeoTIFF output for scientific raster datasets
* Dataset-level CRS/georeferencing validation
* Progress reporting and cancellation
* SAR display visualization:

  * Automatic 2–98 percentile stretch
  * Pseudocolor stretch
  * Linear/full-range grayscale
* Dual-polarization RGB visualization:

  * Red = HHHH
  * Green = HVHV
  * Blue = derived HH/HV ratio
* RGB display domain:

  * dB: `10 × log10(power)`
  * Linear power
* Independent 2–98 percentile stretching for RGB channels
* Optional pre-RGB speckle filtering of the underlying HHHH/HVHV power layers (Median or Mean/Boxcar, 3×3/5×5/7×7)
* Optional loading of generated outputs into QGIS
* Basic scientific raster statistics
* Dataset-level Quality Control (QC)
* Reproducible scientific processing report generation
* Derived speckle smoothing using Median or Mean (Boxcar) filters
* Source data remain unchanged

## Scientific data vs visualization products

The plugin distinguishes between **scientific exports** and **display products**.

### Scientific exports

HHHH, HVHV, Mask, Number of Looks, and other selected NISAR datasets can be exported as separate GeoTIFF files. Scientific raster values are preserved as Float32 where applicable. These exports are intended as georeferenced, analysis-ready raster layers derived from the already processed NISAR GCOV product; the plugin does not recreate NISAR Level-2 scientific calibration or terrain correction.

### GCOV datasets, frequency and “bands”

NISAR GCOV is organized as product/frequency/dataset variables rather than conventional optical bands. The exact datasets available depend on the input product. The plugin exposes common GCOV datasets including **HHHH**, **HVHV**, **mask**, and **Number of Looks** when they are present. NISAR products can contain L-band and S-band observations; the plugin does not assume that every product contains the same frequency or dataset structure and discovers the available subdatasets from the input product.

For the current dual-polarization workflow, HHHH and HVHV are the primary scientific raster datasets. The **HH/HV ratio** is a derived product calculated from those scientific inputs and must be treated as derived data.

### RGB visualization

The RGB Composite tool is a **derived visualization product**, not a replacement for the original NISAR measurements. The RGB image combines HHHH, HVHV and a derived HH/HV ratio and applies a display transform and percentile stretch. RGB output is intended for visualization and interpretation.

An optional speckle filter can be applied **before RGB generation** to the underlying Float32 HHHH and HVHV power values. The derived HH/HV ratio is then calculated from those filtered values. This is different from filtering the final 8-bit RGB image and keeps the scientific source datasets unchanged.

The source HDF5 product is never overwritten.

### What “analysis-ready” means in this plugin

NISAR Level-2 GCOV is already a processed, geocoded SAR product. NISAR Processor does not claim to recreate the upstream NISAR scientific processing chain. Instead, it validates dataset-level georeferencing and exports the selected GCOV datasets as correctly georeferenced raster layers suitable for quantitative GIS, remote-sensing, and machine-learning workflows.

For classification or other quantitative analysis, use the **scientific Float32 exports** (for example HHHH and HVHV), together with appropriate quality/support layers such as `mask` and `numberOfLooks`. The 8-bit RGB composite is a visualization product and should not be used as a substitute for the scientific bands.

## Scientific Quality Control (QC)

The QC module validates the selected dataset without modifying it. Checks include readability, dimensions, CRS validity, geotransform validity, floating-point scientific data type, NoData information, and availability of supporting Mask/Number of Looks datasets. QC reports failures or warnings instead of inventing or manually assigning missing scientific metadata.

## Raster statistics

The Statistics module provides descriptive statistics for the selected scientific raster, including minimum, maximum, mean and standard deviation. Statistics are calculated for inspection and do not alter the source raster.

## Speckle filtering

SAR imagery contains speckle. NISAR Processor provides optional **derived** spatial smoothing filters for scientific floating-point raster exports:

* Median filter
* Mean (Boxcar) filter
* 3×3, 5×5 and 7×7 windows

Filtering changes pixel values, so filtered rasters are explicitly treated as derived products. The original NISAR HDF5 product and the unfiltered scientific export are never overwritten. Filtered outputs retain the source raster georeferencing and floating-point data type where supported.

These filters are provided as general preprocessing tools; users should validate the effect of filtering for their particular quantitative or classification workflow rather than assuming that filtering always improves an analysis. The same filters can optionally be applied to HHHH and HVHV **before RGB generation**. In that mode, the plugin filters the underlying SAR power layers first, calculates the derived HH/HV ratio from the filtered layers, and then performs the selected RGB display transform/stretch. It does not filter the final 8-bit RGB image.

## Scientific processing report

The Processing Report module creates a text record containing product identity, plugin/QGIS/GDAL versions, dataset, dimensions, data type, pixel size, CRS, processing extent, basic statistics, QC results and data-integrity statements. The report is intended to improve reproducibility and traceability when exported rasters are subsequently used for GIS, remote sensing or machine-learning workflows.

## Requirements

### QGIS

* QGIS **3.20 or newer**
* QGIS **4.x** is supported by the plugin metadata and should be tested with the current QGIS 4 release before production deployment.

The plugin uses QGIS's `qgis.PyQt` compatibility namespace rather than requiring a separately installed PyQt package.

### GDAL

**GDAL 3.13 or newer is strongly recommended and is the preferred environment for native NISAR HDF5 support.** GDAL 3.13 introduced NISAR-specific HDF5 support.

The plugin detects the GDAL version at runtime.

* **GDAL ≥ 3.13:** native NISAR HDF5 handling is available.
* **GDAL < 3.13:** the plugin can use the NETCDF access pattern where supported, but native NISAR HDF5 support is not available through the older HDF5 driver.

NISAR documentation explains that older GDAL releases should use the NETCDF driver because NISAR stores geospatial information according to netCDF/CF conventions.

### Windows recommendation

For Windows users, install QGIS through **OSGeo4W** so QGIS and its GDAL installation are managed together. The NISAR documentation also recommends using the OSGeo4W Shell for GDAL on Windows.

To check the GDAL version in an OSGeo4W Shell:

```text
gdalinfo --version
```

A recommended setup is currently:

```text
QGIS 3.44.x + GDAL 3.13.x or newer
```

The exact GDAL version installed by QGIS/OSGeo4W may change as packages are updated.

### Python dependencies

No additional Python packages are required by the plugin beyond the Python/PyQGIS/GDAL environment supplied by QGIS.

Do **not** install a separate `pip` version of GDAL just for this plugin. The plugin is designed to use the GDAL installation associated with QGIS.

## How to fulfil the requirements on Windows

### Recommended method: OSGeo4W

1. Install or update QGIS using OSGeo4W.
2. Open the **OSGeo4W Setup** program.
3. Select the existing QGIS/OSGeo4W installation.
4. Choose **Advanced Install** when package selection is available.
5. Search for `gdal` in the package selector.
6. Select the available GDAL runtime version **3.13 or newer**.
7. Keep the option to install required dependencies enabled.
8. Finish the installation.
9. Close and reopen the OSGeo4W Shell.
10. Verify:

```text
gdalinfo --version
```

The command should report GDAL 3.13 or newer.

### Important: QGIS version and GDAL version are separate

The QGIS major/minor version does not by itself guarantee a particular GDAL version. Always check the GDAL version actually used by the QGIS installation.

## NISAR HDF5 and georeferencing

NISAR Level 2 and Level 3 products are distributed in HDF5 and contain dataset-level geospatial information. NISAR's official documentation notes that older QGIS/GDAL combinations may not correctly read this geolocation information through the default HDF5 driver. GDAL 3.13 introduced NISAR HDF5 support.

NISAR Processor therefore validates georeferencing on the **actual dataset being extracted**, rather than assuming that the HDF5 product root contains a usable CRS.

The plugin does not invent or manually assign a CRS to a dataset whose source georeferencing is invalid.

## Installation

### From the QGIS Plugin Manager

After the plugin has been approved in the QGIS official plugin repository:

1. Open **QGIS → Plugins → Manage and Install Plugins**.
2. Search for **NISAR Processor**.
3. Select the plugin.
4. Click **Install Plugin**.
5. Enable the **NISAR Processor** toolbar if it is not visible.

### From a ZIP file for testing

1. Open **Plugins → Manage and Install Plugins → Install from ZIP**.
2. Select the NISAR Processor ZIP package.
3. Install it.
4. Restart QGIS if requested.

For repository submission, the ZIP must contain the plugin source and metadata but should not contain generated files, `.git`, `\\\_\\\_pycache\\\_\\\_`, or unrelated archives.

## Getting started

### 1\. Open a NISAR product

Select the NISAR HDF5 product in the **Input Product** field.

The plugin reports:

* Product type
* GDAL version
* Product/dataset information
* Available subdatasets
* Georeferencing information where available

### 2\. Inspect the product

Click **Inspect Product** to discover the internal NISAR datasets.

NISAR products are hierarchical HDF5 containers. A typical GCOV dataset can be located under a path such as:

```text
/science/LSAR/GCOV/grids/frequencyA/HHHH
```

NISAR's documentation describes the HDF5 hierarchy and dataset organization.

### 3\. Explore datasets

Use **Dataset Explorer** to inspect the available subdatasets and select the dataset required for extraction or visualization.

### 4\. Extract scientific data

Use **Extract** to export one or more datasets.

Choose either:

* **Full scene**, or
* **Current QGIS map extent**

For scientific bands, GeoTIFF output is written as Float32 where supported by the source dataset.

### 5\. Visualize a SAR band

The Visualization tab provides display-only stretching. The automatic 2–98 percentile stretch is intended to make the broad spatial pattern visible without changing the source scientific values.

NISAR's official QGIS guidance also describes percentile/cumulative-count stretching as a useful visualization approach.

### 6\. Create a dual-pol RGB composite

The RGB Composite tool uses:

|Channel|Input|
|-|-|
|Red|HHHH|
|Green|HVHV|
|Blue|Derived HH/HV ratio|

The user can select **dB** or **Linear power** display domain and the plugin applies an independent 2–98 percentile stretch to each channel.

The resulting RGB raster is a visualization product and should not be treated as a replacement for the original Float32 NISAR measurements.

## GCOV data handling

NISAR GCOV products are geocoded and projected for mapping. The HHHH and HVHV datasets represent covariance/backscatter terms, while Mask and Number of Looks provide supporting information for interpretation and quality control.

The plugin does not convert scientific measurements to 8-bit during normal scientific-band extraction.

## Processing extent

### Full scene

Use full-scene extraction when the complete NISAR dataset is required.

### Current map extent

Use the current QGIS map canvas extent when testing or extracting a spatial subset. This can significantly reduce processing time and output size for large NISAR scenes.

NISAR products can be very large, and the official NISAR GDAL documentation describes extracting selected datasets and spatial subsets rather than unnecessarily processing the entire HDF5 product.

## Workflow

**Product → Dataset Explorer → Scientific Data → QC → Extract → Visualize / RGB → Statistics → SAR Processing → Processing Report**

The workflow is modular: users can inspect and validate a dataset before exporting or deriving additional rasters.

## Data integrity and safety

NISAR Processor follows these principles:

* Source HDF5 products are read-only.
* The plugin does not overwrite the input product.
* Scientific source values are not converted to 8-bit merely for extraction.
* CRS information is not fabricated.
* Visualization operations are separated from scientific exports.
* Derived RGB products are clearly treated as visualization outputs.

## Performance considerations

NISAR products can contain tens of thousands of pixels in each dimension and multiple internal datasets.

For large products:

1. Inspect the product first.
2. Test extraction using the current map extent.
3. Export only required datasets.
4. Use full-scene extraction only when necessary.
5. Allow sufficient disk space for GeoTIFF outputs.
6. Avoid creating unnecessary duplicate copies of the original HDF5 product.

## Troubleshooting

### "GDAL < 3.13" is shown

Update the GDAL installation associated with QGIS.

On Windows, use OSGeo4W Setup rather than installing a separate `pip` GDAL package.

Verify with:

```text
gdalinfo --version
```

### Dataset has no valid CRS/georeferencing

The plugin intentionally does not assign a CRS manually. Check the GDAL version and verify that the selected dataset contains valid georeferencing.

For NISAR HDF5 products, GDAL 3.13+ is recommended because native NISAR HDF5 support was introduced in that release.

### RGB output looks noisy

SAR imagery naturally contains speckle. The RGB Composite tool is a visualization product and does not automatically apply a scientific speckle filter. The source scientific datasets remain available for independent processing.

### Full-scene processing is slow

Use **Current QGIS map extent** first and export only the required datasets.

### QGIS cannot load the output

Check that:

* The output file exists and is not being written by another application.
* There is sufficient disk space.
* The source dataset had valid georeferencing.
* The GDAL version used by QGIS is supported.

## Supported product philosophy

NISAR Processor is intended as a **generic NISAR preprocessing toolbox**, not a forest-specific classification or deforestation tool.

The architecture is designed so that future modules can be added according to NISAR product type, while avoiding inappropriate operations on datasets for which they are not scientifically meaningful.

## Current scope in 1.0.0

Implemented:

* Product inspection
* Dataset discovery
* Dataset extraction
* Multi-band export
* Spatial subsetting by current map extent
* Scientific Float32 GeoTIFF output
* CRS/georeferencing validation
* SAR visualization
* Dual-pol RGB visualization with optional pre-RGB speckle filtering

Future releases may include additional product-aware preprocessing, quality-control tools, clipping/subsetting options, reprojection, resampling, mosaicking, statistics, and batch workflows.

## Limitations

* Processing time depends strongly on scene size, disk speed, CPU, available memory, and selected extent.
* The RGB composite is a visualization product and is not a substitute for scientific source bands.
* Native NISAR HDF5 support depends on the GDAL version available to QGIS.
* QGIS 4 compatibility should be validated against the current QGIS 4 release before claiming a tested production configuration.

## License

NISAR Processor is released under the **GNU General Public License v2.0 or later (GPL-2.0-or-later)**.

See `LICENSE` for the full license text.

## Documentation and references

* NISAR Data User Guide — Using NISAR Data in QGIS: https://nisar-docs.asf.alaska.edu/using-qgis/
* NISAR Data User Guide — GDAL: https://nisar-docs.asf.alaska.edu/gdal/
* NISAR Data User Guide — HDF5/Data Format: https://nisar-docs.asf.alaska.edu/data-format/
* QGIS Plugin Publishing Guidelines: https://plugins.qgis.org/docs/publish/
* QGIS Plugin QGIS 4 migration guidance: https://plugins.qgis.org/docs/migrate-qgis4

## Author

**Deepak Kumar, Ashutosh Singh, Rahul Jayprakash
Copyright © 2026 Deepak Kumar,** **Ashutosh Singh, Rahul Jayprakash Sharma**


