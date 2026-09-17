import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtGui import QIcon, QColor
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QComboBox, QCheckBox,
    QGroupBox, QTableWidget, QTableWidgetItem, QMessageBox,
    QProgressBar, QTextEdit, QTabWidget, QSpinBox, QRadioButton,
    QListWidget, QListWidgetItem, QStackedWidget, QAbstractItemView
)
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsRectangle, Qgis,
    QgsContrastEnhancement, QgsSingleBandGrayRenderer,
    QgsSingleBandPseudoColorRenderer, QgsRasterShader, QgsColorRampShader
)
from qgis.utils import iface

try:
    from osgeo import gdal, osr
except Exception:
    gdal = None
    osr = None

try:
    import numpy as np
except Exception:
    np = None


PRODUCT_PATTERNS = {
    "GCOV": re.compile(r"NISAR_L2_.*GCOV", re.I),
    "GSLC": re.compile(r"NISAR_L2_.*GSLC", re.I),
    "ROFF": re.compile(r"NISAR_L2_.*ROFF", re.I),
    "SLC": re.compile(r"NISAR_L1_.*SLC", re.I),
}

DATASET_HINTS = {
    "HHHH": "frequencyA/HHHH",
    "HVHV": "frequencyA/HVHV",
    "Mask": "frequencyA/mask",
    "Number of Looks": "frequencyA/numberOfLooks",
}


def gdal_version_tuple():
    if gdal is None:
        return (0, 0, 0)
    try:
        v = gdal.VersionInfo("VERSION_NUM")
        n = int(v)
        return (n // 1000000, (n // 10000) % 100, (n // 100) % 100)
    except Exception:
        try:
            m = re.search(r"(\d+)\.(\d+)\.(\d+)", gdal.VersionInfo("--version"))
            return tuple(map(int, m.groups())) if m else (0, 0, 0)
        except Exception:
            return (0, 0, 0)


def gdal_version_text():
    if gdal is None:
        return "GDAL unavailable"
    try:
        return gdal.VersionInfo("--version")
    except Exception:
        return "GDAL"


def product_type(path):
    name = os.path.basename(path)
    for p, rx in PRODUCT_PATTERNS.items():
        if rx.search(name):
            return p
    return "NISAR"


def flatten_subdatasets(ds):
    result = []
    if not ds:
        return result
    try:
        for name, desc in ds.GetSubDatasets() or []:
            result.append((name, desc))
    except Exception:
        pass
    return result


def find_dataset(subdatasets, token):
    token = token.lower()
    for name, desc in subdatasets:
        s = (name + " " + desc).lower()
        if token in s:
            return name
    return None


def netcdf_dataset(path, internal_path):
    # NISAR documentation supports NETCDF:<file>:<internal dataset>
    return 'NETCDF:"{}":{}'.format(path.replace('"', '""'), internal_path)


def choose_dataset_source(path, subdatasets, label):
    token = DATASET_HINTS.get(label, label).lower()

    # Prefer GDAL's native subdataset name when available.
    native = find_dataset(subdatasets, token.split("/")[-1])
    if native:
        return native

    # NISAR GCOV paths. This avoids copying the HDF5 file.
    if "frequencyA" in DATASET_HINTS.get(label, ""):
        internal = "/science/LSAR/GCOV/grids/" + DATASET_HINTS[label]
        return netcdf_dataset(path, internal)

    return None


class ExtractWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, src, dst, extent=None, compress=True, parent=None):
        super().__init__(parent)
        self.src = src
        self.dst = dst
        self.extent = extent
        self.compress = compress
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            if gdal is None:
                raise RuntimeError("GDAL Python bindings are not available.")

            self.progress.emit(2, "Opening NISAR dataset…")
            ds = gdal.Open(self.src, gdal.GA_ReadOnly)
            if ds is None:
                raise RuntimeError("GDAL could not open the selected NISAR dataset.")

            options = [
                "TILED=YES",
                "BIGTIFF=IF_SAFER",
                "PREDICTOR=3",
            ]
            if self.compress:
                options.append("COMPRESS=DEFLATE")
            else:
                options.append("COMPRESS=NONE")

            creation = ["GTiff"] + options

            translate_kwargs = {
                "format": "GTiff",
                "creationOptions": options,
                "callback": self._callback,
            }
            if self.extent is not None:
                translate_kwargs["projWin"] = list(self.extent)

            self.progress.emit(5, "Extracting raster…")
            out = gdal.Translate(self.dst, ds, **translate_kwargs)
            if out is None:
                raise RuntimeError("GDAL failed to create the GeoTIFF.")

            out.FlushCache()
            out = None
            self.progress.emit(100, "Extraction complete.")
            self.finished_ok.emit(self.dst)

        except Exception as e:
            self.failed.emit(str(e))

    def _callback(self, complete, message, data):
        if self._cancel:
            return 0
        pct = max(0, min(100, int(complete * 100)))
        self.progress.emit(pct, "Extracting… {}".format(pct))
        return 1



class BatchExtractWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, jobs, extent=None, compress=True, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.extent = extent
        self.compress = compress
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        outputs = []
        try:
            if gdal is None:
                raise RuntimeError("GDAL Python bindings are not available.")
            total = len(self.jobs)
            for i, (label, source, dst) in enumerate(self.jobs):
                if self._cancel:
                    raise RuntimeError("Batch export cancelled by user.")
                base = int(i * 100 / total)
                self.progress.emit(base, "Exporting {} ({}/{})…".format(label, i + 1, total))
                ds = gdal.Open(source, gdal.GA_ReadOnly)
                if ds is None:
                    raise RuntimeError("GDAL could not open {}.".format(label))
                options = ["TILED=YES", "BIGTIFF=IF_SAFER", "PREDICTOR=3",
                           "COMPRESS=DEFLATE" if self.compress else "COMPRESS=NONE"]
                def cb(complete, message, data):
                    if self._cancel:
                        return 0
                    pct = base + int((complete * 100 / total))
                    self.progress.emit(min(99, pct),
                                       "Exporting {}… {}%".format(label, int(complete * 100)))
                    return 1
                kwargs = {"format": "GTiff", "creationOptions": options, "callback": cb}
                if self.extent is not None:
                    kwargs["projWin"] = list(self.extent)
                out = gdal.Translate(dst, ds, **kwargs)
                ds = None
                if out is None:
                    raise RuntimeError("GDAL failed to create {}.".format(dst))
                out.FlushCache()
                out = None
                outputs.append(dst)
            self.progress.emit(100, "Batch export complete.")
            self.finished_ok.emit(outputs)
        except Exception as e:
            self.failed.emit(str(e))


class RGBCompositeWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, hh_source, hv_source, out_path, extent=None,
                 mode="dB", filter_method="None", filter_window=5, parent=None):
        super().__init__(parent)
        self.hh_source = hh_source
        self.hv_source = hv_source
        self.out_path = out_path
        self.extent = extent
        self.mode = mode
        self.filter_method = filter_method
        self.filter_window = int(filter_window)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    @staticmethod
    def _window(ds, extent):
        gt = ds.GetGeoTransform()
        if abs(gt[2]) > 1e-12 or abs(gt[4]) > 1e-12:
            raise RuntimeError("Rotated geotransforms are not supported for RGB export yet.")
        x0, y0, x1, y1 = 0, 0, ds.RasterXSize, ds.RasterYSize
        if extent is not None:
            xmin, ymax, xmax, ymin = extent
            px0 = int((xmin - gt[0]) / gt[1])
            px1 = int((xmax - gt[0]) / gt[1])
            py0 = int((ymax - gt[3]) / gt[5])
            py1 = int((ymin - gt[3]) / gt[5])
            x0 = max(0, min(px0, px1))
            x1 = min(ds.RasterXSize, max(px0, px1) + 1)
            y0 = max(0, min(py0, py1))
            y1 = min(ds.RasterYSize, max(py0, py1) + 1)
        if x1 <= x0 or y1 <= y0:
            raise RuntimeError("RGB extent does not overlap the selected NISAR dataset.")
        return x0, y0, x1 - x0, y1 - y0

    @staticmethod
    def _sample_limits(band, window, mode):
        xoff, yoff, xsize, ysize = window
        # Sample to a manageable grid rather than reading a billion-pixel scene.
        sx = min(1600, xsize)
        sy = min(1600, ysize)
        arr = band.ReadAsArray(xoff, yoff, xsize, ysize, sx, sy)
        if arr is None:
            raise RuntimeError("Could not sample raster values for RGB stretch.")
        import numpy as np
        a = np.asarray(arr, dtype=np.float32)
        if mode == "dB":
            a = np.where(a > 0, 10.0 * np.log10(a), np.nan)
        else:
            a = np.where(a > 0, a, np.nan)
        vals = a[np.isfinite(a)]
        if vals.size == 0:
            raise RuntimeError("No positive finite values available for RGB stretch.")
        lo, hi = np.percentile(vals, [2, 98])
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo = float(np.nanmin(vals))
            hi = float(np.nanmax(vals))
        if hi <= lo:
            raise RuntimeError("RGB channel has no usable dynamic range.")
        return float(lo), float(hi)

    @staticmethod
    def _to_byte(arr, lo, hi, mode):
        import numpy as np
        a = np.asarray(arr, dtype=np.float32)
        if mode == "dB":
            a = np.where(a > 0, 10.0 * np.log10(a), lo)
        else:
            a = np.where(a > 0, a, lo)
        a = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
        return (a * 255.0 + 0.5).astype(np.uint8)

    def _filter_array(self, arr):
        """Filter an in-memory sample in SAR power space."""
        w = self.filter_window
        if w < 3 or w % 2 == 0:
            raise RuntimeError("RGB filter window must be an odd number of at least 3.")
        a = np.asarray(arr, dtype=np.float32)
        a = np.where((a > 0) & np.isfinite(a), a, np.nan)
        radius = w // 2
        pad = np.pad(a, ((radius, radius), (radius, radius)), mode="reflect")
        windows = np.lib.stride_tricks.sliding_window_view(pad, (w, w))
        if self.filter_method == "Median":
            filtered = np.nanmedian(windows, axis=(-2, -1))
        else:
            filtered = np.nanmean(windows, axis=(-2, -1))
        return filtered.astype(np.float32, copy=False)

    def _filter_block(self, band, xoff, yoff, xsize, ysize):
        """Read a block with halo and apply an optional spatial filter in power space."""
        if self.filter_method == "None":
            arr = band.ReadAsArray(xoff, yoff, xsize, ysize)
            if arr is None:
                raise RuntimeError("Failed reading RGB source block.")
            return np.asarray(arr, dtype=np.float32)

        w = self.filter_window
        if w < 3 or w % 2 == 0:
            raise RuntimeError("RGB filter window must be an odd number of at least 3.")
        radius = w // 2
        full_x = band.XSize
        full_y = band.YSize
        rx0 = max(0, xoff - radius)
        ry0 = max(0, yoff - radius)
        rx1 = min(full_x, xoff + xsize + radius)
        ry1 = min(full_y, yoff + ysize + radius)
        arr = band.ReadAsArray(rx0, ry0, rx1 - rx0, ry1 - ry0)
        if arr is None:
            raise RuntimeError("Failed reading RGB filter block.")
        arr = np.asarray(arr, dtype=np.float32)
        # GCOV power values must be positive for the display workflow. Treat
        # non-positive/non-finite cells as missing during the filter operation.
        arr = np.where((arr > 0) & np.isfinite(arr), arr, np.nan)
        top = max(0, radius - (yoff - ry0))
        left = max(0, radius - (xoff - rx0))
        bottom = max(0, radius - (ry1 - (yoff + ysize)))
        right = max(0, radius - (rx1 - (xoff + xsize)))
        if top or bottom or left or right:
            arr = np.pad(arr, ((top, bottom), (left, right)), mode="reflect")
        windows = np.lib.stride_tricks.sliding_window_view(arr, (w, w))
        if self.filter_method == "Median":
            filtered = np.nanmedian(windows, axis=(-2, -1))
        else:
            filtered = np.nanmean(windows, axis=(-2, -1))
        return filtered.astype(np.float32, copy=False)

    def run(self):
        try:
            if gdal is None:
                raise RuntimeError("GDAL Python bindings are not available.")
            try:
                import numpy as np  # noqa: F401
            except Exception:
                raise RuntimeError("NumPy is required for RGB composite creation in this QGIS environment.")

            self.progress.emit(2, "Opening HHHH and HVHV…")
            hh = gdal.Open(self.hh_source, gdal.GA_ReadOnly)
            hv = gdal.Open(self.hv_source, gdal.GA_ReadOnly)
            if hh is None or hv is None:
                raise RuntimeError("Could not open HHHH/HVHV datasets.")
            if hh.RasterXSize != hv.RasterXSize or hh.RasterYSize != hv.RasterYSize:
                raise RuntimeError("HHHH and HVHV grids do not have matching dimensions.")
            if hh.GetProjectionRef() != hv.GetProjectionRef():
                raise RuntimeError("HHHH and HVHV CRS definitions do not match.")

            window = self._window(hh, self.extent)
            xoff, yoff, xsize, ysize = window
            self.progress.emit(5, "Preparing RGB source data…")
            hh_band = hh.GetRasterBand(1)
            hv_band = hv.GetRasterBand(1)

            # If requested, filter the underlying Float32 SAR power values BEFORE
            # calculating the RGB channels. The source datasets are read-only.
            if self.filter_method != "None":
                self.progress.emit(6, "Applying {} {}×{} filter before RGB…".format(
                    self.filter_method, self.filter_window, self.filter_window))
                # For percentile estimation, read a manageable resampled view of
                # the selected extent and filter that view. The final RGB output
                # is filtered at full source resolution below.
                sx = min(1600, xsize)
                sy = min(1600, ysize)
                hh_sample = hh_band.ReadAsArray(xoff, yoff, xsize, ysize, sx, sy)
                hv_sample = hv_band.ReadAsArray(xoff, yoff, xsize, ysize, sx, sy)
                if hh_sample is None or hv_sample is None:
                    raise RuntimeError("Could not sample HHHH/HVHV for RGB calculation.")
                hh_sample = np.asarray(hh_sample, dtype=np.float32)
                hv_sample = np.asarray(hv_sample, dtype=np.float32)
                hh_sample = self._filter_array(hh_sample)
                hv_sample = self._filter_array(hv_sample)
            else:
                sx = min(1600, xsize)
                sy = min(1600, ysize)
                hh_sample = hh_band.ReadAsArray(xoff, yoff, xsize, ysize, sx, sy)
                hv_sample = hv_band.ReadAsArray(xoff, yoff, xsize, ysize, sx, sy)
            if hh_sample is None or hv_sample is None:
                raise RuntimeError("Could not sample HHHH/HVHV for RGB calculation.")
            hh_sample = np.asarray(hh_sample, dtype=np.float32)
            hv_sample = np.asarray(hv_sample, dtype=np.float32)

            def sample_limits_from_array(a):
                if self.mode == "dB":
                    a = np.where(a > 0, 10.0 * np.log10(a), np.nan)
                else:
                    a = np.where(a > 0, a, np.nan)
                vals = a[np.isfinite(a)]
                if vals.size == 0:
                    raise RuntimeError("No positive finite values available for RGB stretch.")
                lo, hi = np.percentile(vals, [2, 98])
                if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                    lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
                if hi <= lo:
                    raise RuntimeError("RGB channel has no usable dynamic range.")
                return float(lo), float(hi)

            hh_lo, hh_hi = sample_limits_from_array(hh_sample)
            hv_lo, hv_hi = sample_limits_from_array(hv_sample)

            # Blue is the HH/HV ratio, calculated from the same filtered/unfiltered
            # power samples used for the red and green channels.
            ratio = np.where((hh_sample > 0) & (hv_sample > 0),
                             hh_sample / hv_sample, np.nan)
            if self.mode == "dB":
                ratio = np.where(np.isfinite(ratio), 10.0 * np.log10(ratio), np.nan)
            vals = ratio[np.isfinite(ratio)]
            if vals.size == 0:
                raise RuntimeError("Could not calculate a usable HH/HV ratio channel.")
            b_lo, b_hi = np.percentile(vals, [2, 98])
            if b_hi <= b_lo:
                b_lo, b_hi = float(np.nanmin(vals)), float(np.nanmax(vals))

            # Create an 8-bit display RGB. Scientific Float32 source bands remain untouched.
            driver = gdal.GetDriverByName("GTiff")
            if driver is None:
                raise RuntimeError("GTiff driver is unavailable.")
            options = ["TILED=YES", "BIGTIFF=IF_SAFER", "COMPRESS=DEFLATE"]
            out = driver.Create(self.out_path, xsize, ysize, 3, gdal.GDT_Byte, options=options)
            if out is None:
                raise RuntimeError("Could not create RGB GeoTIFF.")
            gt = hh.GetGeoTransform()
            out.SetGeoTransform((gt[0] + xoff * gt[1], gt[1], gt[2],
                                 gt[3] + yoff * gt[5], gt[4], gt[5]))
            out.SetProjection(hh.GetProjectionRef())
            descriptions = ["HHHH", "HVHV", "HH/HV ratio"]
            for idx, desc in enumerate(descriptions, 1):
                out.GetRasterBand(idx).SetDescription(desc)

            block_y = 256
            block_x = 1024
            for row in range(0, ysize, block_y):
                if self._cancel:
                    out = None
                    raise RuntimeError("RGB composite cancelled by user.")
                rows = min(block_y, ysize - row)
                for col in range(0, xsize, block_x):
                    cols = min(block_x, xsize - col)
                    if self.filter_method != "None":
                        ha = self._filter_block(hh_band, xoff + col, yoff + row, cols, rows)
                        va = self._filter_block(hv_band, xoff + col, yoff + row, cols, rows)
                    else:
                        ha = hh_band.ReadAsArray(xoff + col, yoff + row, cols, rows)
                        va = hv_band.ReadAsArray(xoff + col, yoff + row, cols, rows)
                    if ha is None or va is None:
                        raise RuntimeError("Failed reading RGB source block.")
                    ha = np.asarray(ha, dtype=np.float32)
                    va = np.asarray(va, dtype=np.float32)
                    r = self._to_byte(ha, hh_lo, hh_hi, self.mode)
                    g = self._to_byte(va, hv_lo, hv_hi, self.mode)
                    rr = np.where((ha > 0) & (va > 0), ha / va, np.nan)
                    if self.mode == "dB":
                        rr = np.where(np.isfinite(rr), 10.0 * np.log10(rr), b_lo)
                    else:
                        rr = np.where(np.isfinite(rr), rr, b_lo)
                    b = np.clip((rr - b_lo) / (b_hi - b_lo), 0.0, 1.0)
                    b = (b * 255.0 + 0.5).astype(np.uint8)
                    out.GetRasterBand(1).WriteArray(r, col, row)
                    out.GetRasterBand(2).WriteArray(g, col, row)
                    out.GetRasterBand(3).WriteArray(b, col, row)
                pct = 10 + int(88 * ((row + rows) / float(ysize)))
                self.progress.emit(pct, "Building RGB composite… {}%".format(pct))
            out.FlushCache()
            out = None
            hh = None
            hv = None
            self.progress.emit(100, "RGB composite complete.")
            self.finished_ok.emit(self.out_path)
        except Exception as e:
            self.failed.emit(str(e))




class SpeckleFilterWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, src, dst, method="Median", window=5, parent=None):
        super().__init__(parent)
        self.src = src
        self.dst = dst
        self.method = method
        self.window = int(window)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            if gdal is None or np is None:
                raise RuntimeError("GDAL and NumPy are required for speckle filtering.")
            if self.window % 2 == 0 or self.window < 3:
                raise RuntimeError("Window size must be an odd number of at least 3.")
            src = gdal.Open(self.src, gdal.GA_ReadOnly)
            if src is None:
                raise RuntimeError("Could not open the input raster.")
            if src.RasterCount < 1:
                raise RuntimeError("Input raster has no bands.")
            band = src.GetRasterBand(1)
            dtype = band.DataType
            if dtype not in (gdal.GDT_Float32, gdal.GDT_Float64):
                raise RuntimeError("Speckle filtering requires a floating-point scientific raster.")
            xsize, ysize = src.RasterXSize, src.RasterYSize
            gt = src.GetGeoTransform()
            proj = src.GetProjectionRef()
            nodata = band.GetNoDataValue()
            driver = gdal.GetDriverByName("GTiff")
            opts = ["TILED=YES", "BIGTIFF=IF_SAFER", "COMPRESS=DEFLATE", "PREDICTOR=3"]
            out = driver.Create(self.dst, xsize, ysize, 1, dtype, options=opts)
            if out is None:
                raise RuntimeError("Could not create the output GeoTIFF.")
            out.SetGeoTransform(gt)
            if proj:
                out.SetProjection(proj)
            ob = out.GetRasterBand(1)
            if nodata is not None:
                ob.SetNoDataValue(nodata)
            radius = self.window // 2
            block = 512
            total_rows = max(1, (ysize + block - 1) // block)
            row_no = 0
            for y in range(0, ysize, block):
                if self._cancel:
                    raise RuntimeError("Speckle filtering cancelled by user.")
                h = min(block, ysize - y)
                y0 = max(0, y - radius)
                y1 = min(ysize, y + h + radius)
                arr = band.ReadAsArray(0, y0, xsize, y1 - y0).astype(np.float32, copy=False)
                if arr is None:
                    raise RuntimeError("Failed reading raster block.")
                pad = np.pad(arr, ((radius, radius), (radius, radius)), mode="reflect")
                shape = (arr.shape[0], arr.shape[1], self.window, self.window)
                strides = pad.strides + pad.strides
                win = np.lib.stride_tricks.as_strided(pad, shape=shape, strides=strides)
                if self.method == "Median":
                    filtered = np.nanmedian(win, axis=(2, 3)).astype(np.float32)
                else:
                    filtered = np.nanmean(win, axis=(2, 3)).astype(np.float32)
                start = y - y0
                filtered = filtered[start:start + h, :]
                if nodata is not None:
                    invalid = ~np.isfinite(arr[start:start + h, :]) | (arr[start:start + h, :] == nodata)
                    filtered[invalid] = nodata
                ob.WriteArray(filtered, 0, y)
                row_no += 1
                self.progress.emit(min(99, int(row_no * 100 / total_rows)),
                                   "Filtering… {}%".format(int(row_no * 100 / total_rows)))
            ob.FlushCache(); out.FlushCache()
            out = None; src = None
            self.progress.emit(100, "Speckle filtering complete.")
            self.finished_ok.emit(self.dst)
        except Exception as e:
            self.failed.emit(str(e))


class NISARDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("NISAR Processor")
        self.resize(820, 560)
        self.setMinimumSize(720, 500)
        self.worker = None
        self.subdatasets = []
        self.source_path = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(5)

        file_box = QGroupBox("NISAR Product")
        fg = QGridLayout(file_box)
        fg.addWidget(QLabel("Input product:"), 0, 0)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select a .h5, .hdf5 or .nc NISAR product…")
        fg.addWidget(self.path_edit, 0, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        fg.addWidget(browse, 0, 2)
        self.inspect_btn = QPushButton("Inspect Product")
        self.inspect_btn.clicked.connect(self.inspect)
        fg.addWidget(self.inspect_btn, 1, 2)
        self.product_label = QLabel("Product: —")
        self.gdal_label = QLabel("GDAL: —")
        self.crs_label = QLabel("CRS: —")
        fg.addWidget(self.product_label, 1, 0)
        fg.addWidget(self.gdal_label, 1, 1)
        fg.addWidget(self.crs_label, 2, 0, 1, 2)
        root.addWidget(file_box)

        # Vertical navigation keeps the growing toolbox compact and extensible.
        nav = QHBoxLayout()
        nav.setSpacing(6)
        self.nav = QListWidget()
        self.nav.setFixedWidth(155)
        self.nav.setSelectionMode(QAbstractItemView.SingleSelection)
        modules = [
            ("📦 Product", "Product"),
            ("🔍 Dataset Explorer", "Dataset Explorer"),
            ("🔬 Scientific Data", "Scientific Data"),
            ("✅ Quality Control", "Quality Control"),
            ("🎨 Visualization", "Visualization"),
            ("🛰 Speckle Filtering", "Speckle Filtering"),
            ("📤 Extract", "Extract"),
            ("🌈 RGB Composite", "RGB Composite"),
            ("📊 Statistics", "Statistics"),
            ("📋 Processing Report", "Processing Report"),
        ]
        self.stack = QStackedWidget()
        self.nav_modules = []
        for label, key in modules:
            self.nav.addItem(QListWidgetItem(label))
            self.nav_modules.append(key)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        nav.addWidget(self.nav)
        nav.addWidget(self.stack, 1)
        root.addLayout(nav, 1)

        # Product page
        ptab = QVBoxLayout()
        pinfo = QLabel("Product inspection, environment detection and NISAR product-level information.")
        pinfo.setWordWrap(True); ptab.addWidget(pinfo)
        self.product_details = QTextEdit(); self.product_details.setReadOnly(True); ptab.addWidget(self.product_details, 1)
        self.stack.addWidget(QWidgetContainer(ptab))

        # Dataset Explorer page
        dtab = QVBoxLayout()
        self.dataset_table = QTableWidget(0, 2)
        self.dataset_table.setHorizontalHeaderLabels(["Dataset / Name", "GDAL description"])
        self.dataset_table.horizontalHeader().setStretchLastSection(True)
        dtab.addWidget(self.dataset_table)
        dtabw = QGroupBox("Discovered datasets")
        dtabw.setLayout(dtab)
        dpage = QVBoxLayout(); dpage.addWidget(dtabw); self.stack.addWidget(QWidgetContainer(dpage))

        # Scientific Data page
        stab = QVBoxLayout()
        sinfo = QLabel(
            "<b>Scientific data</b><br>Extracted scientific datasets retain their source numeric values and floating-point type. "
            "These are the preferred outputs for quantitative GIS, remote-sensing and classification workflows."
        ); sinfo.setWordWrap(True); stab.addWidget(sinfo)
        self.scientific_info = QTextEdit(); self.scientific_info.setReadOnly(True); stab.addWidget(self.scientific_info, 1)
        stab.addWidget(QLabel("Primary GCOV datasets: HHHH and HVHV. Supporting datasets: mask and Number of Looks. Derived products are clearly identified."))
        self.stack.addWidget(QWidgetContainer(stab))

        # QC page
        qtab = QVBoxLayout()
        qinfo = QLabel("Run non-destructive validation on the selected NISAR dataset. QC reports problems rather than inventing scientific metadata.")
        qinfo.setWordWrap(True); qtab.addWidget(qinfo)
        self.qc_result = QTextEdit(); self.qc_result.setReadOnly(True); qtab.addWidget(self.qc_result, 1)
        self.qc_btn = QPushButton("Run QC on Selected Dataset"); self.qc_btn.clicked.connect(self.run_qc); qtab.addWidget(self.qc_btn)
        self.stack.addWidget(QWidgetContainer(qtab))

        # Extract page
        etab = QVBoxLayout()
        row = QHBoxLayout(); row.addWidget(QLabel("Dataset:"))
        self.dataset_combo = QComboBox(); self.dataset_combo.addItems(list(DATASET_HINTS.keys())); row.addWidget(self.dataset_combo, 1); etab.addLayout(row)
        batch_box = QGroupBox("Multi-dataset export")
        bg = QVBoxLayout(batch_box); self.band_checks = {}
        for band_label in ["HHHH", "HVHV", "Mask", "Number of Looks"]:
            cb = QCheckBox(band_label); cb.setChecked(band_label in ("HHHH", "HVHV")); self.band_checks[band_label] = cb; bg.addWidget(cb)
        bbuttons = QHBoxLayout(); select_all = QPushButton("Select All"); clear_all = QPushButton("Clear All")
        select_all.clicked.connect(lambda: [cb.setChecked(True) for cb in self.band_checks.values()]); clear_all.clicked.connect(lambda: [cb.setChecked(False) for cb in self.band_checks.values()])
        bbuttons.addWidget(select_all); bbuttons.addWidget(clear_all); bbuttons.addStretch(1); bg.addLayout(bbuttons)
        self.batch_btn = QPushButton("Export Selected Datasets"); self.batch_btn.clicked.connect(self.export_selected_bands); bg.addWidget(self.batch_btn); etab.addWidget(batch_box)
        extent_box = QGroupBox("Processing extent"); eg = QVBoxLayout(extent_box)
        self.full_radio = QCheckBox("Full scene"); self.full_radio.setChecked(True); self.current_radio = QCheckBox("Current QGIS map extent")
        self.full_radio.toggled.connect(lambda v: self.current_radio.setChecked(not v)); self.current_radio.toggled.connect(lambda v: self.full_radio.setChecked(not v)); eg.addWidget(self.full_radio); eg.addWidget(self.current_radio); etab.addWidget(extent_box)
        outrow = QHBoxLayout(); outrow.addWidget(QLabel("Output folder:")); self.out_edit = QLineEdit(); outrow.addWidget(self.out_edit, 1); outb = QPushButton("Browse…"); outb.clicked.connect(self.browse_output); outrow.addWidget(outb); etab.addLayout(outrow)
        self.compress = QCheckBox("DEFLATE compression (smaller output, slower write)"); self.compress.setChecked(True); etab.addWidget(self.compress)
        self.load_output = QCheckBox("Load extracted raster into QGIS"); self.load_output.setChecked(True); etab.addWidget(self.load_output)
        self.extract_btn = QPushButton("Extract & Load"); self.extract_btn.clicked.connect(self.extract); etab.addWidget(self.extract_btn)
        self.progress = QProgressBar(); etab.addWidget(self.progress); self.status = QLabel("Ready."); etab.addWidget(self.status)
        self.stack.addWidget(QWidgetContainer(etab))

        # Visualization page
        vtab = QVBoxLayout()
        vinfo = QLabel("<b>Display-only visualization</b><br>NISAR GCOV floating-point values are not modified by these settings."); vinfo.setWordWrap(True); vtab.addWidget(vinfo)
        vrow = QHBoxLayout(); vrow.addWidget(QLabel("Visualization:")); self.visual_combo = QComboBox(); self.visual_combo.addItems(["Auto SAR stretch (2–98%, grayscale)", "Pseudocolor SAR stretch (2–98%)", "Linear grayscale (full range)"]); vrow.addWidget(self.visual_combo, 1); vtab.addLayout(vrow)
        self.auto_visual = QCheckBox("Apply automatically after extraction"); self.auto_visual.setChecked(True); vtab.addWidget(self.auto_visual)
        self.apply_visual_btn = QPushButton("Apply to Selected Raster Layer"); self.apply_visual_btn.clicked.connect(self.apply_visualization_to_selected); vtab.addWidget(self.apply_visual_btn)
        vnote = QLabel("Visualization is display-only. Keep the scientific Float32 exports for analysis and classification."); vnote.setWordWrap(True); vtab.addWidget(vnote); vtab.addStretch(1)
        self.stack.addWidget(QWidgetContainer(vtab))

        # RGB page
        rtab = QVBoxLayout(); rinfo = QLabel("<b>Dual-pol GCOV RGB visualization</b><br>R=HHHH, G=HVHV, B=HH/HV ratio. This is a derived 8-bit visualization, not a replacement for scientific Float32 data."); rinfo.setWordWrap(True); rtab.addWidget(rinfo)
        preset = QGroupBox("RGB channels"); pg = QGridLayout(preset); pg.addWidget(QLabel("Red:"),0,0); pg.addWidget(QLabel("HHHH"),0,1); pg.addWidget(QLabel("Green:"),1,0); pg.addWidget(QLabel("HVHV"),1,1); pg.addWidget(QLabel("Blue:"),2,0); pg.addWidget(QLabel("HH/HV ratio (derived)"),2,1); rtab.addWidget(preset)
        mode_box = QGroupBox("RGB value display domain"); mg = QVBoxLayout(mode_box); self.rgb_db = QRadioButton("dB (10 × log10(power)) — recommended"); self.rgb_linear = QRadioButton("Linear power"); self.rgb_db.setChecked(True); mg.addWidget(self.rgb_db); mg.addWidget(self.rgb_linear); rtab.addWidget(mode_box)
        rgb_filter = QGroupBox("Optional speckle filtering before RGB generation"); rfg = QGridLayout(rgb_filter)
        self.rgb_filter_enable = QCheckBox("Apply filter to HHHH and HVHV before creating RGB"); self.rgb_filter_enable.setChecked(False); rfg.addWidget(self.rgb_filter_enable, 0, 0, 1, 3)
        rfg.addWidget(QLabel("Filter:"), 1, 0); self.rgb_filter_method = QComboBox(); self.rgb_filter_method.addItems(["Median", "Mean (Boxcar)"]); rfg.addWidget(self.rgb_filter_method, 1, 1)
        rfg.addWidget(QLabel("Window:"), 2, 0); self.rgb_filter_window = QComboBox(); self.rgb_filter_window.addItems(["3 × 3", "5 × 5", "7 × 7"]); self.rgb_filter_window.setCurrentIndex(1); rfg.addWidget(self.rgb_filter_window, 2, 1)
        rfg.addWidget(QLabel("Filtering is applied to the underlying Float32 SAR power before the derived RGB ratio/stretch. Original data are unchanged."), 3, 0, 1, 3); rtab.addWidget(rgb_filter)
        rgbrow = QHBoxLayout(); rgbrow.addWidget(QLabel("Output folder:")); self.rgb_out_edit = QLineEdit(); rgbrow.addWidget(self.rgb_out_edit,1); rgbb=QPushButton("Browse…"); rgbb.clicked.connect(self.browse_rgb_output); rgbrow.addWidget(rgbb); rtab.addLayout(rgbrow)
        self.rgb_btn=QPushButton("Create RGB Composite"); self.rgb_btn.clicked.connect(self.create_rgb_composite); rtab.addWidget(self.rgb_btn); self.rgb_progress=QProgressBar(); rtab.addWidget(self.rgb_progress); self.rgb_status=QLabel("Ready."); rtab.addWidget(self.rgb_status); rtab.addStretch(1)
        self.stack.addWidget(QWidgetContainer(rtab))

        # Statistics page
        stattab = QVBoxLayout(); stinfo=QLabel("Calculate basic statistics for a selected scientific dataset. Statistics are descriptive and do not alter the raster."); stinfo.setWordWrap(True); stattab.addWidget(stinfo)
        self.stats_text=QTextEdit(); self.stats_text.setReadOnly(True); stattab.addWidget(self.stats_text,1); self.stats_btn=QPushButton("Calculate Statistics"); self.stats_btn.clicked.connect(self.calculate_statistics); stattab.addWidget(self.stats_btn)
        self.stack.addWidget(QWidgetContainer(stattab))

        # Speckle Filtering page
        sar_tab=QVBoxLayout(); sarinfo=QLabel("<b>Speckle filtering (derived product)</b><br>Filtering modifies pixel values. The source NISAR product is never modified. Use filtered outputs as derived datasets."); sarinfo.setWordWrap(True); sar_tab.addWidget(sarinfo)
        sr=QHBoxLayout(); sr.addWidget(QLabel("Input:")); self.filter_dataset=QComboBox(); self.filter_dataset.addItems(["HHHH","HVHV"]); sr.addWidget(self.filter_dataset,1); sar_tab.addLayout(sr)
        fr=QHBoxLayout(); fr.addWidget(QLabel("Filter:")); self.filter_method=QComboBox(); self.filter_method.addItems(["Median","Mean (Boxcar)"]); fr.addWidget(self.filter_method,1); fr.addWidget(QLabel("Window:")); self.filter_window=QComboBox(); self.filter_window.addItems(["3 × 3","5 × 5","7 × 7"]); self.filter_window.setCurrentIndex(1); fr.addWidget(self.filter_window); sar_tab.addLayout(fr)
        frow=QHBoxLayout(); frow.addWidget(QLabel("Output folder:")); self.filter_out_edit=QLineEdit(); frow.addWidget(self.filter_out_edit,1); fb=QPushButton("Browse…"); fb.clicked.connect(self.browse_filter_output); frow.addWidget(fb); sar_tab.addLayout(frow)
        self.filter_btn=QPushButton("Create Filtered Raster"); self.filter_btn.clicked.connect(self.apply_speckle_filter); sar_tab.addWidget(self.filter_btn); self.filter_progress=QProgressBar(); sar_tab.addWidget(self.filter_progress); self.filter_status=QLabel("Ready."); sar_tab.addWidget(self.filter_status); sar_tab.addStretch(1)
        self.stack.addWidget(QWidgetContainer(sar_tab))

        # Report page
        rpt=QVBoxLayout(); rpi=QLabel("Generate a reproducible text report for the inspected product and selected dataset, including environment, spatial information and QC/statistics when available."); rpi.setWordWrap(True); rpt.addWidget(rpi)
        rr=QHBoxLayout(); rr.addWidget(QLabel("Report folder:")); self.report_out_edit=QLineEdit(); rr.addWidget(self.report_out_edit,1); rb=QPushButton("Browse…"); rb.clicked.connect(self.browse_report_output); rr.addWidget(rb); rpt.addLayout(rr)
        self.report_btn=QPushButton("Generate Processing Report"); self.report_btn.clicked.connect(self.generate_report); rpt.addWidget(self.report_btn); self.report_status=QLabel("Ready."); rpt.addWidget(self.report_status); rpt.addStretch(1)
        self.stack.addWidget(QWidgetContainer(rpt))

        # Reorder pages to match the left navigation exactly.
        # Original page construction is retained above; this only changes presentation order.
        pages = [self.stack.widget(i) for i in range(self.stack.count())]
        desired_order = [0, 1, 2, 3, 5, 8, 4, 6, 7, 9]
        for w in pages:
            self.stack.removeWidget(w)
        for i in desired_order:
            self.stack.addWidget(pages[i])

        self.nav.setCurrentRow(1)

        log_box = QGroupBox("Messages"); lg=QVBoxLayout(log_box); self.log=QTextEdit(); self.log.setReadOnly(True); self.log.setMinimumHeight(60); self.log.setMaximumHeight(95); self.log.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded); self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); lg.setContentsMargins(6,4,6,4); lg.addWidget(self.log); root.addWidget(log_box,0)
        close=QPushButton("Close"); close.clicked.connect(self.close); root.addWidget(close)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select NISAR product", "", "NISAR (*.h5 *.hdf5 *.nc);;All files (*.*)"
        )
        if path:
            self.path_edit.setText(path)
            self.inspect()

    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.out_edit.setText(folder)

    def inspect(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "NISAR Processor", "Please select an existing NISAR product.")
            return
        if gdal is None:
            QMessageBox.critical(self, "GDAL unavailable",
                                 "The QGIS Python environment cannot import GDAL.")
            return

        self.source_path = path
        self.product_label.setText("Product: {}".format(product_type(path)))
        self.gdal_label.setText("GDAL: {}".format(gdal_version_text()))

        try:
            ds = gdal.Open(path, gdal.GA_ReadOnly)
            if ds is None:
                raise RuntimeError("GDAL could not open the file.")

            self.subdatasets = flatten_subdatasets(ds)
            self.dataset_table.setRowCount(0)
            for name, desc in self.subdatasets:
                r = self.dataset_table.rowCount()
                self.dataset_table.insertRow(r)
                self.dataset_table.setItem(r, 0, QTableWidgetItem(name))
                self.dataset_table.setItem(r, 1, QTableWidgetItem(desc))

            # Report direct dataset CRS when available.
            wkt = ds.GetProjectionRef() or ""
            if wkt and osr:
                sref = osr.SpatialReference(wkt=wkt)
                try:
                    auth = sref.GetAuthorityCode(None)
                    crs_text = "EPSG:{}".format(auth) if auth else sref.GetName()
                except Exception:
                    crs_text = "Projection present"
            else:
                crs_text = "Not available at product root (dataset-level georeferencing may apply)"
            self.crs_label.setText("CRS: {}".format(crs_text))

            self.log.append("Opened: {}".format(path))
            self.log.append("GDAL: {}".format(gdal_version_text()))
            self.log.append("Discovered {} subdataset(s).".format(len(self.subdatasets)))
            self.product_details.setPlainText(
                "Product type: {}\nInput: {}\nGDAL: {}\nSubdatasets discovered: {}\nCRS at product root: {}\n\nDataset-level georeferencing is validated when an individual dataset is opened.".format(
                    product_type(path), os.path.basename(path), gdal_version_text(), len(self.subdatasets), crs_text))
            self.scientific_info.setPlainText(
                "GCOV scientific datasets\n\nPrimary dual-pol datasets: HHHH, HVHV\nSupporting datasets: mask, Number of Looks\n\nAvailable frequency/product datasets depend on the input NISAR product.\nScientific exports preserve source numeric values and are intended for quantitative analysis.\nRGB output is a derived visualization and should not replace scientific exports.")

            if gdal_version_tuple() >= (3, 13, 0):
                self.log.append("GDAL >= 3.13 detected: native NISAR HDF5 support is available.")
            else:
                self.log.append("GDAL < 3.13: NETCDF access pattern will be used where possible.")

        except Exception as e:
            QMessageBox.critical(self, "Inspection failed", str(e))
            self.log.append("ERROR: {}".format(e))

    def _current_extent_in_source_crs(self, source):
        ds = gdal.Open(source, gdal.GA_ReadOnly)
        if ds is None:
            raise RuntimeError("Cannot open dataset to determine its extent.")

        gt = ds.GetGeoTransform()
        if gt == (0, 1, 0, 0, 0, 1):
            raise RuntimeError("The selected dataset has no usable geotransform.")

        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        map_crs = canvas.mapSettings().destinationCrs()

        wkt = ds.GetProjectionRef()
        if not wkt:
            raise RuntimeError("The selected dataset has no valid CRS.")

        src_crs = QgsCoordinateReferenceSystem.fromWkt(wkt)
        if not src_crs.isValid():
            raise RuntimeError("The selected dataset CRS is invalid.")

        if map_crs != src_crs:
            transform = QgsCoordinateTransform(map_crs, src_crs, QgsProject.instance())
            extent = transform.transformBoundingBox(extent)

        return extent.xMinimum(), extent.yMaximum(), extent.xMaximum(), extent.yMinimum()

    def extract(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "NISAR Processor", "Select a NISAR product first.")
            return

        outdir = self.out_edit.text().strip()
        if not outdir:
            outdir = os.path.dirname(path)
        os.makedirs(outdir, exist_ok=True)

        label = self.dataset_combo.currentText()
        source = choose_dataset_source(path, self.subdatasets, label)
        if not source:
            QMessageBox.warning(
                self, "Dataset not found",
                "Could not identify {} in this product. Inspect the product first.".format(label)
            )
            return

        # Validate source before launching the long operation.
        test = gdal.Open(source, gdal.GA_ReadOnly)
        if test is None:
            QMessageBox.critical(
                self, "Dataset open failed",
                "GDAL could not open the selected dataset:\n\n{}".format(source)
            )
            return

        projection = test.GetProjectionRef() or ""
        gt = test.GetGeoTransform()
        xsize, ysize = test.RasterXSize, test.RasterYSize
        test = None

        if not projection:
            QMessageBox.critical(
                self, "Invalid georeferencing",
                "The selected dataset has no valid CRS/georeferencing. "
                "The plugin will not assign a CRS manually."
            )
            return

        extent = None
        if self.current_radio.isChecked():
            try:
                extent = self._current_extent_in_source_crs(source)
            except Exception as e:
                QMessageBox.critical(self, "Extent error", str(e))
                return

        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", label.lower())
        stem = Path(path).stem
        dst = os.path.join(outdir, "{}_{}.tif".format(stem, safe))

        if os.path.exists(dst):
            answer = QMessageBox.question(
                self, "Output exists",
                "{} already exists. Overwrite it?".format(dst),
                QMessageBox.Yes | QMessageBox.No
            )
            if answer != QMessageBox.Yes:
                return

        self.progress.setValue(0)
        self.status.setText("Starting…")
        self.extract_btn.setEnabled(False)

        self.worker = ExtractWorker(
            source, dst, extent=extent,
            compress=self.compress.isChecked(), parent=self
        )
        self.worker.progress.connect(self._worker_progress)
        self.worker.finished_ok.connect(self._worker_done)
        self.worker.failed.connect(self._worker_failed)
        self.worker.start()


    def _selected_band_labels(self):
        return [label for label, cb in self.band_checks.items() if cb.isChecked()]

    def _source_and_extent(self, path, label):
        source = choose_dataset_source(path, self.subdatasets, label)
        if not source:
            raise RuntimeError("Could not identify {} in this product. Inspect the product first.".format(label))
        test = gdal.Open(source, gdal.GA_ReadOnly)
        if test is None:
            raise RuntimeError("GDAL could not open the selected {} dataset.".format(label))
        projection = test.GetProjectionRef() or ""
        test = None
        if not projection:
            raise RuntimeError("The {} dataset has no valid CRS/georeferencing. The plugin will not assign a CRS manually.".format(label))
        extent = None
        if self.current_radio.isChecked():
            extent = self._current_extent_in_source_crs(source)
        return source, extent

    def export_selected_bands(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "NISAR Processor", "Select and inspect a NISAR product first.")
            return
        labels = self._selected_band_labels()
        if not labels:
            QMessageBox.warning(self, "Multi-band export", "Select at least one band.")
            return
        outdir = self.out_edit.text().strip() or os.path.dirname(path)
        os.makedirs(outdir, exist_ok=True)
        jobs = []
        try:
            common_extent = None
            for label in labels:
                source, extent = self._source_and_extent(path, label)
                if common_extent is None:
                    common_extent = extent
                safe = re.sub(r"[^A-Za-z0-9_-]+", "_", label.lower())
                dst = os.path.join(outdir, "{}_{}.tif".format(Path(path).stem, safe))
                jobs.append((label, source, dst))
        except Exception as e:
            QMessageBox.critical(self, "Batch export", str(e))
            return

        existing = [dst for _, _, dst in jobs if os.path.exists(dst)]
        if existing:
            answer = QMessageBox.question(
                self, "Output exists",
                "One or more selected outputs already exist. Overwrite them?\n\n{}".format("\n".join(existing)),
                QMessageBox.Yes | QMessageBox.No
            )
            if answer != QMessageBox.Yes:
                return

        self.batch_btn.setEnabled(False)
        self.extract_btn.setEnabled(False)
        self.progress.setValue(0)
        self.status.setText("Starting multi-band export…")
        self.batch_worker = BatchExtractWorker(
            jobs, extent=common_extent, compress=self.compress.isChecked(), parent=self
        )
        self.batch_worker.progress.connect(self._worker_progress)
        self.batch_worker.finished_ok.connect(self._batch_done)
        self.batch_worker.failed.connect(self._batch_failed)
        self.batch_worker.start()

    def _batch_done(self, paths):
        self.batch_btn.setEnabled(True)
        self.extract_btn.setEnabled(True)
        self.status.setText("Batch export complete.")
        for path in paths:
            self.log.append("Created: {}".format(path))
            if self.load_output.isChecked():
                layer = QgsRasterLayer(path, os.path.basename(path), "gdal")
                if layer.isValid() and layer.crs().isValid():
                    QgsProject.instance().addMapLayer(layer)
                    if self.auto_visual.isChecked():
                        try:
                            self._apply_visualization(layer)
                        except Exception as e:
                            self.log.append("Visualization warning for {}: {}".format(path, e))
                else:
                    self.log.append("WARNING: output failed QGIS/CRS validation: {}".format(path))
        QMessageBox.information(self, "Multi-band export", "Exported {} band(s) successfully.".format(len(paths)))

    def _batch_failed(self, msg):
        self.batch_btn.setEnabled(True)
        self.extract_btn.setEnabled(True)
        self.status.setText("Batch export failed.")
        self.log.append("ERROR: {}".format(msg))
        QMessageBox.critical(self, "Multi-band export failed", msg)

    def browse_rgb_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select RGB output folder")
        if folder:
            self.rgb_out_edit.setText(folder)

    def create_rgb_composite(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "RGB Composite", "Select and inspect a NISAR product first.")
            return
        try:
            hh_source, extent = self._source_and_extent(path, "HHHH")
            hv_source, extent2 = self._source_and_extent(path, "HVHV")
            if extent is None:
                extent = extent2
        except Exception as e:
            QMessageBox.critical(self, "RGB Composite", str(e))
            return

        outdir = self.rgb_out_edit.text().strip() or self.out_edit.text().strip() or os.path.dirname(path)
        os.makedirs(outdir, exist_ok=True)
        mode = "dB" if self.rgb_db.isChecked() else "linear"
        if self.rgb_filter_enable.isChecked():
            filter_method = self.rgb_filter_method.currentText().split(" ")[0]
            filter_window = int(self.rgb_filter_window.currentText().split()[0])
            filter_tag = "_speckle_{}_{}x{}".format(filter_method.lower(), filter_window, filter_window)
        else:
            filter_method = "None"
            filter_window = 5
            filter_tag = ""
        suffix = "RGB_HH_HV_ratio_dB{}.tif".format(filter_tag) if mode == "dB" else "RGB_HH_HV_ratio_linear{}.tif".format(filter_tag)
        dst = os.path.join(outdir, "{}_{}".format(Path(path).stem, suffix))
        if os.path.exists(dst):
            answer = QMessageBox.question(self, "Output exists", "{} already exists. Overwrite it?".format(dst),
                                          QMessageBox.Yes | QMessageBox.No)
            if answer != QMessageBox.Yes:
                return

        self.rgb_btn.setEnabled(False)
        self.rgb_progress.setValue(0)
        self.rgb_status.setText("Starting RGB composite…")
        self.rgb_worker = RGBCompositeWorker(
            hh_source, hv_source, dst, extent=extent, mode=mode,
            filter_method=filter_method, filter_window=filter_window, parent=self)
        self.rgb_worker.progress.connect(self._rgb_progress)
        self.rgb_worker.finished_ok.connect(self._rgb_done)
        self.rgb_worker.failed.connect(self._rgb_failed)
        self.rgb_worker.start()

    def _rgb_progress(self, pct, msg):
        self.rgb_progress.setValue(pct)
        self.rgb_status.setText(msg)

    def _rgb_done(self, path):
        self.rgb_btn.setEnabled(True)
        self.rgb_status.setText("Complete: {}".format(path))
        self.log.append("Created RGB visualization: {}".format(path))
        if self.rgb_filter_enable.isChecked():
            self.log.append("RGB speckle filter: {} {}x{} applied before RGB generation.".format(
                self.rgb_filter_method.currentText(), self.rgb_filter_window.currentText().split()[0],
                self.rgb_filter_window.currentText().split()[0]))
        layer = QgsRasterLayer(path, os.path.basename(path), "gdal")
        if layer.isValid() and layer.crs().isValid():
            QgsProject.instance().addMapLayer(layer)
            self.log.append("Loaded RGB composite into QGIS. CRS: {}".format(layer.crs().authid()))
        else:
            self.log.append("WARNING: RGB output could not be loaded/validated in QGIS.")
        QMessageBox.information(self, "RGB Composite", "RGB composite created successfully.\n\n{}".format(path))

    def _rgb_failed(self, msg):
        self.rgb_btn.setEnabled(True)
        self.rgb_status.setText("Failed.")
        self.log.append("RGB ERROR: {}".format(msg))
        QMessageBox.critical(self, "RGB Composite failed", msg)

    def _worker_progress(self, pct, msg):
        self.progress.setValue(pct)
        self.status.setText(msg)

    def _worker_done(self, path):
        self.extract_btn.setEnabled(True)
        self.status.setText("Complete: {}".format(path))
        self.log.append("Created: {}".format(path))

        if self.load_output.isChecked():
            layer = QgsRasterLayer(path, os.path.basename(path), "gdal")
            if not layer.isValid():
                QMessageBox.warning(
                    self, "Output validation failed",
                    "GeoTIFF was created, but QGIS could not load it."
                )
                return

            if not layer.crs().isValid():
                QMessageBox.critical(
                    self, "CRS validation failed",
                    "Output raster has an invalid CRS. It was not added to QGIS."
                )
                return

            QgsProject.instance().addMapLayer(layer)
            self.log.append("Loaded into QGIS. CRS: {}".format(layer.crs().authid()))

            if self.auto_visual.isChecked():
                try:
                    self._apply_visualization(layer)
                    self.log.append("Applied display-only visualization: {}".format(
                        self.visual_combo.currentText()))
                except Exception as e:
                    self.log.append("Visualization warning: {}".format(e))

        QMessageBox.information(self, "NISAR Processor",
                                "Extraction completed successfully.")

    def _apply_visualization(self, layer):
        """Apply a display renderer only; never alters the source raster values."""
        if not layer or not layer.isValid() or layer.bandCount() < 1:
            raise RuntimeError("Selected layer is not a valid single-band raster.")

        provider = layer.dataProvider()
        band = 1
        # Estimate percentile limits from the provider. sampleSize keeps this fast for
        # billion-pixel NISAR scenes while still giving a robust display stretch.
        lower = 0.0
        upper = 0.0
        try:
            # PyQGIS exposes cumulativeCut as a tuple (lower, upper).
            cut = provider.cumulativeCut(band, 0.02, 0.98, layer.extent(), 250000)
            if cut is None or len(cut) < 2:
                raise RuntimeError("No percentile limits returned by the raster provider.")
            lower, upper = float(cut[0]), float(cut[1])
        except Exception as e:
            raise RuntimeError("Could not calculate percentile display limits: {}".format(e))

        if not (upper > lower):
            stats = provider.bandStatistics(band)
            lower = stats.minimumValue
            upper = stats.maximumValue

        if not (upper > lower):
            raise RuntimeError("Raster does not contain a usable value range.")

        mode = self.visual_combo.currentIndex()
        if mode == 2:
            # Full-range grayscale.
            ce = QgsContrastEnhancement(provider.dataType(band))
            ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum)
            ce.setMinimumValue(lower)
            ce.setMaximumValue(upper)
            renderer = QgsSingleBandGrayRenderer(provider, band)
            renderer.setContrastEnhancement(ce)
        elif mode == 1:
            # Display-only pseudocolor using a compact SAR-friendly ramp.
            shader = QgsRasterShader()
            ramp = QgsColorRampShader()
            ramp.setColorRampType(QgsColorRampShader.Interpolated)
            items = [
                QgsColorRampShader.ColorRampItem(lower, QColor(49, 54, 149)),
                QgsColorRampShader.ColorRampItem(lower + 0.20 * (upper-lower), QColor(69, 117, 180)),
                QgsColorRampShader.ColorRampItem(lower + 0.40 * (upper-lower), QColor(116, 173, 209)),
                QgsColorRampShader.ColorRampItem(lower + 0.60 * (upper-lower), QColor(171, 221, 164)),
                QgsColorRampShader.ColorRampItem(lower + 0.80 * (upper-lower), QColor(253, 174, 97)),
                QgsColorRampShader.ColorRampItem(upper, QColor(215, 25, 28)),
            ]
            ramp.setColorRampItemList(items)
            shader.setRasterShaderFunction(ramp)
            renderer = QgsSingleBandPseudoColorRenderer(provider, band, shader)
        else:
            # Auto SAR stretch: robust 2–98 percentile grayscale.
            ce = QgsContrastEnhancement(provider.dataType(band))
            ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchAndClipToMinimumMaximum)
            ce.setMinimumValue(lower)
            ce.setMaximumValue(upper)
            renderer = QgsSingleBandGrayRenderer(provider, band)
            renderer.setContrastEnhancement(ce)

        layer.setRenderer(renderer)
        layer.triggerRepaint()

    def apply_visualization_to_selected(self):
        layer = self.iface.activeLayer()
        if not layer or not isinstance(layer, QgsRasterLayer):
            QMessageBox.warning(self, "Visualization", "Select a raster layer in the Layers panel first.")
            return
        try:
            self._apply_visualization(layer)
            self.log.append("Applied display-only visualization to: {}".format(layer.name()))
            self.status.setText("Visualization applied: {}".format(self.visual_combo.currentText()))
        except Exception as e:
            QMessageBox.critical(self, "Visualization failed", str(e))
            self.log.append("Visualization ERROR: {}".format(e))

    def _selected_source(self, label):
        path = self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            raise RuntimeError("Select and inspect a NISAR product first.")
        source = choose_dataset_source(path, self.subdatasets, label)
        if not source:
            raise RuntimeError("Could not identify {} in this product.".format(label))
        return source

    def run_qc(self):
        label = self.dataset_combo.currentText()
        try:
            source = self._selected_source(label)
            ds = gdal.Open(source, gdal.GA_ReadOnly)
            if ds is None:
                raise RuntimeError("GDAL could not open the dataset.")
            gt = ds.GetGeoTransform()
            proj = ds.GetProjectionRef() or ""
            band = ds.GetRasterBand(1)
            dtype = gdal.GetDataTypeName(band.DataType)
            nodata = band.GetNoDataValue()
            x, y = ds.RasterXSize, ds.RasterYSize
            px = (gt[1], abs(gt[5])) if gt else (None, None)
            checks = []
            checks.append(("Dataset readable", True))
            checks.append(("Dimensions valid", x > 0 and y > 0))
            checks.append(("CRS valid", bool(proj) and QgsCoordinateReferenceSystem.fromWkt(proj).isValid()))
            checks.append(("Geotransform valid", gt != (0,1,0,0,0,1)))
            checks.append(("Floating-point scientific type", dtype in ("Float32", "Float64")))
            text = ["NISAR QUALITY CONTROL", "=" * 30]
            for name, ok in checks:
                text.append(("✓ " if ok else "✗ ") + name)
            text += ["", "Dataset: {}".format(label), "Dimensions: {} × {}".format(x,y), "Data type: {}".format(dtype), "Pixel size: {} × {}".format(px[0],px[1]), "NoData: {}".format(nodata if nodata is not None else "not defined")]
            self.qc_result.setPlainText("\n".join(text))
            self.log.append("QC completed for {}.".format(label))
        except Exception as e:
            self.qc_result.setPlainText("QC FAILED\n\n{}".format(e)); self.log.append("QC ERROR: {}".format(e))

    def calculate_statistics(self):
        label = self.dataset_combo.currentText()
        try:
            source = self._selected_source(label); ds=gdal.Open(source, gdal.GA_ReadOnly); band=ds.GetRasterBand(1)
            stats = band.GetStatistics(False, True)
            nodata=band.GetNoDataValue(); text=["RASTER STATISTICS", "="*30, "Dataset: {}".format(label), "Minimum: {}".format(stats[0]), "Maximum: {}".format(stats[1]), "Mean: {}".format(stats[2]), "Standard deviation: {}".format(stats[3]), "NoData: {}".format(nodata if nodata is not None else "not defined")]
            self.stats_text.setPlainText("\n".join(text)); self.log.append("Statistics calculated for {}.".format(label))
        except Exception as e:
            self.stats_text.setPlainText("Statistics failed\n\n{}".format(e)); self.log.append("Statistics ERROR: {}".format(e))

    def browse_filter_output(self):
        folder=QFileDialog.getExistingDirectory(self,"Select filter output folder")
        if folder:self.filter_out_edit.setText(folder)

    def apply_speckle_filter(self):
        label=self.filter_dataset.currentText()
        try:
            source=self._selected_source(label)
            ds=gdal.Open(source,gdal.GA_ReadOnly)
            if ds is None or not (ds.GetProjectionRef() or ""):
                raise RuntimeError("Selected dataset has no valid CRS/georeferencing.")
            outdir=self.filter_out_edit.text().strip() or self.out_edit.text().strip() or os.path.dirname(self.path_edit.text().strip())
            os.makedirs(outdir,exist_ok=True)
            method=self.filter_method.currentText().split(" ")[0]
            w=int(self.filter_window.currentText().split()[0])
            suffix="{}_{}_{}x{}.tif".format(Path(self.path_edit.text().strip()).stem,label.lower(),method.lower(),w)
            dst=os.path.join(outdir,suffix)
            if os.path.exists(dst):
                if QMessageBox.question(self,"Output exists","{} already exists. Overwrite it?".format(dst),QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes:return
            self.filter_btn.setEnabled(False); self.filter_progress.setValue(0); self.filter_status.setText("Starting…")
            self.filter_worker=SpeckleFilterWorker(source,dst,method=method,window=w,parent=self)
            self.filter_worker.progress.connect(lambda p,m:(self.filter_progress.setValue(p),self.filter_status.setText(m)))
            self.filter_worker.finished_ok.connect(self._filter_done); self.filter_worker.failed.connect(self._filter_failed); self.filter_worker.start()
        except Exception as e: QMessageBox.critical(self,"Speckle filtering",str(e))

    def _filter_done(self,path):
        self.filter_btn.setEnabled(True); self.filter_status.setText("Complete: {}".format(path)); self.log.append("Created derived filtered raster: {}".format(path))
        layer=QgsRasterLayer(path,os.path.basename(path),"gdal")
        if layer.isValid() and layer.crs().isValid(): QgsProject.instance().addMapLayer(layer)
        QMessageBox.information(self,"Speckle filtering","Derived filtered raster created successfully.\n\n{}".format(path))

    def _filter_failed(self,msg):
        self.filter_btn.setEnabled(True); self.filter_status.setText("Failed."); self.log.append("Speckle filter ERROR: {}".format(msg)); QMessageBox.critical(self,"Speckle filtering failed",msg)

    def browse_report_output(self):
        folder=QFileDialog.getExistingDirectory(self,"Select report folder")
        if folder:self.report_out_edit.setText(folder)

    def generate_report(self):
        path=self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self,"Processing Report","Select and inspect a NISAR product first."); return
        label=self.dataset_combo.currentText()
        try:
            source=self._selected_source(label)
            ds=gdal.Open(source,gdal.GA_ReadOnly)
            band=ds.GetRasterBand(1)
            gt=ds.GetGeoTransform(); proj=ds.GetProjectionRef() or ""
            crs=QgsCoordinateReferenceSystem.fromWkt(proj) if proj else QgsCoordinateReferenceSystem()
            stats=band.GetStatistics(False, True)
            nodata=band.GetNoDataValue()
            qc_ok = bool(proj) and crs.isValid() and gt != (0,1,0,0,0,1) and ds.RasterXSize > 0 and ds.RasterYSize > 0
            try:
                mask_available = bool(self._selected_source("Mask"))
            except Exception:
                mask_available = False
            try:
                looks_available = bool(self._selected_source("Number of Looks"))
            except Exception:
                looks_available = False
            outdir=self.report_out_edit.text().strip() or self.out_edit.text().strip() or os.path.dirname(path)
            os.makedirs(outdir,exist_ok=True)
            report=os.path.join(outdir,"{}_{}_processing_report.txt".format(Path(path).stem,label.lower()))
            extent_text="Full scene"
            if self.current_radio.isChecked():
                try:
                    ex=self._current_extent_in_source_crs(source)
                    extent_text="Current QGIS map extent: {}, {}, {}, {}".format(*ex)
                except Exception as ex:
                    extent_text="Current map extent (validation unavailable: {})".format(ex)
            lines=[
                "NISAR PROCESSOR — SCIENTIFIC PROCESSING REPORT",
                "="*60,
                "Generated: {}".format(datetime.now().isoformat(timespec="seconds")),
                "Plugin version: 1.0.0",
                "QGIS version: {}".format(Qgis.QGIS_VERSION),
                "GDAL: {}".format(gdal_version_text()),
                "",
                "INPUT PRODUCT",
                "Product type: {}".format(product_type(path)),
                "Input product: {}".format(os.path.basename(path)),
                "",
                "DATASET",
                "Dataset: {}".format(label),
                "Dimensions: {} × {}".format(ds.RasterXSize,ds.RasterYSize),
                "Data type: {}".format(gdal.GetDataTypeName(band.DataType)),
                "Pixel size: {} × {}".format(gt[1],abs(gt[5])) if gt else "Pixel size: unavailable",
                "CRS: {}".format(crs.authid() if crs.isValid() else "Invalid/unavailable"),
                "Extent used: {}".format(extent_text),
                "NoData: {}".format(nodata if nodata is not None else "not defined"),
                "",
                "QUALITY CONTROL",
                "Overall QC: {}".format("PASSED" if qc_ok else "FAILED / WARNING"),
                "Readable: YES",
                "Dimensions valid: {}".format("YES" if ds.RasterXSize > 0 and ds.RasterYSize > 0 else "NO"),
                "CRS valid: {}".format("YES" if crs.isValid() else "NO"),
                "Geotransform valid: {}".format("YES" if gt != (0,1,0,0,0,1) else "NO"),
                "Mask dataset available: {}".format("YES" if mask_available else "NO"),
                "Number of Looks available: {}".format("YES" if looks_available else "NO"),
                "",
                "STATISTICS",
                "Minimum: {}".format(stats[0]),
                "Maximum: {}".format(stats[1]),
                "Mean: {}".format(stats[2]),
                "Standard deviation: {}".format(stats[3]),
                "",
                "SCIENTIFIC DATA INTEGRITY",
                "GCOV scientific values preserved during extraction: YES",
                "Original source product modified: NO",
                "CRS manually assigned by plugin: NO",
                "RGB/visualization outputs: DERIVED DISPLAY PRODUCTS",
                "",
                "NOTE",
                "This report records plugin-side inspection, validation and derivation information. It does not replace the original NISAR product metadata or independent scientific validation."
            ]
            Path(report).write_text("\n".join(lines),encoding="utf-8")
            self.report_status.setText("Created: {}".format(report)); self.log.append("Created processing report: {}".format(report)); QMessageBox.information(self,"Processing Report","Report created successfully.\n\n{}".format(report))
        except Exception as e: QMessageBox.critical(self,"Processing Report",str(e))

    def _worker_failed(self, msg):
        self.extract_btn.setEnabled(True)
        self.status.setText("Failed.")
        self.log.append("ERROR: {}".format(msg))
        QMessageBox.critical(self, "Extraction failed", msg)

    def closeEvent(self, event):
        for worker_name in ("worker", "batch_worker", "rgb_worker", "filter_worker"):
            worker = getattr(self, worker_name, None)
            if worker and worker.isRunning():
                worker.cancel()
                worker.wait(2000)
        event.accept()


class QWidgetContainer(QGroupBox):
    def __init__(self, layout):
        super().__init__()
        self.setLayout(layout)


class NISARProcessor:
    def __init__(self, iface):
        self.iface = iface
        self.toolbar = None
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.toolbar = self.iface.addToolBar("NISAR Processor")
        self.toolbar.setObjectName("NISARProcessorToolbar")

        self.action = QAction(QIcon(icon_path), "NISAR Processor", self.iface.mainWindow())
        self.action.setToolTip("Open NISAR Processor")
        self.action.triggered.connect(self.run)
        self.toolbar.addAction(self.action)

    def unload(self):
        if self.action:
            try:
                self.action.triggered.disconnect(self.run)
            except Exception:
                pass
            self.action.deleteLater()
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
        self.action = None
        self.toolbar = None

    def run(self):
        if self.dialog is None:
            self.dialog = NISARDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
