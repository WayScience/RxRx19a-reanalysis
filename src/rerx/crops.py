"""
Per-cell crop Parquet generation.

Implements plan.md section 14: while CellProfiler masks and source images
are still available, crop each cell from all 5 channel source images,
mask out background pixels using the CellProfiler cell mask, encode each
channel as a grayscale JPEG, and write one row per cell.

Center coordinates and object numbers come from the joined CytoTable
profiles (``Cells_AreaShape_Center_X/Y``, ``Cells_Number_Object_Number``);
pixel data comes from the shard's staged source PNGs and its
``*_MaskCells.tiff`` per-object labeled mask (see :mod:`rerx.cellprofiler`).
"""

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from PIL import Image

from rerx.metadata import CHANNELS, ImageSetID

CROP_SCHEMA_COLUMNS = [
    "Metadata_cell_id",
    "Metadata_site_id",
    "Metadata_experiment",
    "Metadata_plate",
    "Metadata_well",
    "Metadata_site",
    "Metadata_object_number",
    "Metadata_center_x",
    "Metadata_center_y",
    "crop_width",
    "crop_height",
    "jpeg_quality",
    "crop_w1_jpeg",
    "crop_w2_jpeg",
    "crop_w3_jpeg",
    "crop_w4_jpeg",
    "crop_w5_jpeg",
]

# Pixel value used outside the cell mask (plan.md section 14: "Set pixels
# outside the cell mask to a fixed background value").
BACKGROUND_VALUE = 0

# Starting point per plan.md section 14 ("high-quality JPEG, such as
# quality 95 or higher"); validate against lossless before freezing.
DEFAULT_JPEG_QUALITY = 95

# Placeholder default; plan.md section 14 says use one fixed size after the
# pilot visual/embedding comparison, so this is deliberately provisional.
DEFAULT_CROP_SIZE = 96


def load_mask(mask_path: Path) -> np.ndarray:
    """
    Load a per-object labeled mask TIFF (see ``ConvertObjectsToImage``).

    Parameters
    ----------
    mask_path : Path
        ``*_MaskCells.tiff`` (or Nuclei/Cytoplasm) path.

    Returns
    -------
    np.ndarray
        2D integer array; pixel value is the object number, background 0.
    """
    return np.array(Image.open(mask_path))


def crop_box(
    center_x: float,
    center_y: float,
    crop_size: int,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    """
    Compute an integer crop box centered on a point, clamped to the image.

    Parameters
    ----------
    center_x, center_y : float
        Cell center in image pixel coordinates (from CellProfiler
        ``AreaShape_Center_X/Y``).
    crop_size : int
        Crop side length in pixels (square crop).
    image_width, image_height : int
        Source image dimensions, for boundary clamping.

    Returns
    -------
    tuple[int, int, int, int]
        ``(left, top, right, bottom)`` in image coordinates; ``right -
        left`` and ``bottom - top`` may be less than ``crop_size`` at image
        edges (the caller pads the difference; plan.md section 14: "Pad
        deterministically when a crop reaches an image boundary").
    """
    half = crop_size // 2
    left = round(center_x) - half
    top = round(center_y) - half
    right = left + crop_size
    bottom = top + crop_size
    clamped_left = max(0, left)
    clamped_top = max(0, top)
    clamped_right = min(image_width, right)
    clamped_bottom = min(image_height, bottom)
    return clamped_left, clamped_top, clamped_right, clamped_bottom


def crop_and_mask_channel(  # noqa: PLR0913
    channel_image: np.ndarray,
    cell_mask: np.ndarray,
    object_number: int,
    center_x: float,
    center_y: float,
    crop_size: int = DEFAULT_CROP_SIZE,
    background_value: int = BACKGROUND_VALUE,
) -> np.ndarray:
    """
    Crop one channel around a cell, zeroing pixels outside its mask.

    Parameters
    ----------
    channel_image : np.ndarray
        Full-resolution single-channel source image.
    cell_mask : np.ndarray
        Per-object labeled cell mask, same dimensions as ``channel_image``.
    object_number : int
        This cell's label in ``cell_mask``.
    center_x, center_y : float
        Cell center (CellProfiler ``AreaShape_Center_X/Y``).
    crop_size : int
        Output crop side length in pixels (square). Deterministic padding
        (zeros) is applied at image boundaries so every crop is exactly
        ``crop_size x crop_size``.
    background_value : int
        Fill value for both padding and pixels outside the cell's mask.

    Returns
    -------
    np.ndarray
        ``(crop_size, crop_size)`` array, dtype matching ``channel_image``.

    Raises
    ------
    ValueError
        If ``channel_image`` and ``cell_mask`` shapes differ.
    """
    if channel_image.shape != cell_mask.shape:
        raise ValueError(
            f"channel_image shape {channel_image.shape} != "
            f"cell_mask shape {cell_mask.shape}"
        )
    height, width = channel_image.shape
    left, top, right, bottom = crop_box(center_x, center_y, crop_size, width, height)

    out = np.full((crop_size, crop_size), background_value, dtype=channel_image.dtype)
    if right <= left or bottom <= top:
        return out

    region = channel_image[top:bottom, left:right].copy()
    region_mask = cell_mask[top:bottom, left:right] == object_number
    region[~region_mask] = background_value

    # Where this crop lands in the (possibly smaller, boundary-clamped)
    # output array: same offset the clamp introduced, so the cell stays
    # centered and only the padding differs.
    half = crop_size // 2
    ideal_left = round(center_x) - half
    ideal_top = round(center_y) - half
    dest_left = left - ideal_left
    dest_top = top - ideal_top
    dest_right = dest_left + (right - left)
    dest_bottom = dest_top + (bottom - top)
    out[dest_top:dest_bottom, dest_left:dest_right] = region
    return out


def encode_jpeg(channel_crop: np.ndarray, quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """
    Encode a single-channel crop as a grayscale JPEG.

    Parameters
    ----------
    channel_crop : np.ndarray
        2D array; cast to 8-bit if not already (RxRx19a source PNGs are
        8-bit, so this is a no-op for real crops).
    quality : int
        JPEG quality (plan.md section 14 default: 95).

    Returns
    -------
    bytes
        JPEG-encoded image bytes, suitable for a Parquet ``BINARY`` column.
    """
    if channel_crop.dtype != np.uint8:
        channel_crop = channel_crop.astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(channel_crop, mode="L").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


@dataclass(frozen=True)
class SiteCropInputs:
    """
    Everything needed to crop every cell in one site.

    Attributes
    ----------
    ids : ImageSetID
        Site identity.
    channel_images : dict[int, np.ndarray]
        Channel number -> full-resolution source image array.
    cell_mask : np.ndarray
        Per-object labeled ``MaskCells`` array for this site.
    """

    ids: ImageSetID
    channel_images: dict[int, np.ndarray]
    cell_mask: np.ndarray


def load_site_crop_inputs(
    ids: ImageSetID,
    images_dir: Path,
    mask_path: Path,
    channels: Iterable[int] = CHANNELS,
) -> SiteCropInputs:
    """
    Load a site's channel images and cell mask for cropping.

    Parameters
    ----------
    ids : ImageSetID
        Site identity.
    images_dir : Path
        Directory holding the site's staged channel PNGs (see
        :func:`rerx.cellprofiler.stage_shard_images`).
    mask_path : Path
        This site's ``*_MaskCells.tiff`` path.
    channels : Iterable[int]
        Channels to load.

    Returns
    -------
    SiteCropInputs
        Loaded arrays, ready for :func:`crop_site_cells`.
    """
    channel_images = {}
    for channel in channels:
        path = Path(images_dir) / ids.image_path(channel)
        channel_images[channel] = np.array(Image.open(path))
    cell_mask = load_mask(mask_path)
    return SiteCropInputs(ids=ids, channel_images=channel_images, cell_mask=cell_mask)


def crop_site_cells(
    inputs: SiteCropInputs,
    cell_rows: pd.DataFrame,
    crop_size: int = DEFAULT_CROP_SIZE,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> pd.DataFrame:
    """
    Crop every cell in a site, producing one crop row per cell.

    Parameters
    ----------
    inputs : SiteCropInputs
        This site's loaded channel images and cell mask.
    cell_rows : pd.DataFrame
        This site's rows from the joined CytoTable profiles. Must carry
        ``Metadata_cell_id``, ``Cells_Number_Object_Number``,
        ``Cells_AreaShape_Center_X``, ``Cells_AreaShape_Center_Y``.
    crop_size : int
        Square crop side length in pixels.
    jpeg_quality : int
        JPEG quality for every channel.

    Returns
    -------
    pd.DataFrame
        One row per cell, columns matching :data:`CROP_SCHEMA_COLUMNS`.

    Raises
    ------
    ValueError
        If ``cell_rows`` is missing required columns.
    """
    required = (
        "Metadata_cell_id",
        "Cells_Number_Object_Number",
        "Cells_AreaShape_Center_X",
        "Cells_AreaShape_Center_Y",
    )
    missing = [c for c in required if c not in cell_rows.columns]
    if missing:
        raise ValueError(f"cell_rows missing required columns: {missing}")

    ids = inputs.ids
    rows = []
    for _, cell in cell_rows.iterrows():
        object_number = int(cell["Cells_Number_Object_Number"])
        center_x = float(cell["Cells_AreaShape_Center_X"])
        center_y = float(cell["Cells_AreaShape_Center_Y"])
        row = {
            "Metadata_cell_id": cell["Metadata_cell_id"],
            "Metadata_site_id": ids.site_id,
            "Metadata_experiment": ids.experiment,
            "Metadata_plate": ids.plate,
            "Metadata_well": ids.well,
            "Metadata_site": ids.site,
            "Metadata_object_number": object_number,
            "Metadata_center_x": center_x,
            "Metadata_center_y": center_y,
            "crop_width": crop_size,
            "crop_height": crop_size,
            "jpeg_quality": jpeg_quality,
        }
        for channel in CHANNELS:
            crop = crop_and_mask_channel(
                inputs.channel_images[channel],
                inputs.cell_mask,
                object_number,
                center_x,
                center_y,
                crop_size=crop_size,
            )
            row[f"crop_w{channel}_jpeg"] = encode_jpeg(crop, quality=jpeg_quality)
        rows.append(row)
    return pd.DataFrame(rows, columns=pd.Index(CROP_SCHEMA_COLUMNS))


def write_crops_parquet(crops: pd.DataFrame, dest_path: Path) -> Path:
    """
    Write a crop Parquet atomically (plan.md section 23).

    Parameters
    ----------
    crops : pd.DataFrame
        Crop rows, columns matching :data:`CROP_SCHEMA_COLUMNS`.
    dest_path : Path
        Final Parquet path.

    Returns
    -------
    Path
        ``dest_path``.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".parquet.tmp")
    crops.to_parquet(tmp_path, index=False, compression="zstd")
    tmp_path.rename(dest_path)
    return dest_path


def validate_crops_join_one_to_one(
    crops: pd.DataFrame,
    profiles: pd.DataFrame,
) -> None:
    """
    Check crop rows join one-to-one with CellProfiler cells (plan.md section 27).

    Parameters
    ----------
    crops : pd.DataFrame
        Crop rows with ``Metadata_cell_id``.
    profiles : pd.DataFrame
        CellProfiler cell profiles with ``Metadata_cell_id``.

    Raises
    ------
    ValueError
        If either side has duplicate IDs, or the ID sets differ.
    """
    crop_ids = crops["Metadata_cell_id"]
    profile_ids = profiles["Metadata_cell_id"]
    if crop_ids.duplicated().any():
        raise ValueError("crops has duplicate Metadata_cell_id values")
    if profile_ids.duplicated().any():
        raise ValueError("profiles has duplicate Metadata_cell_id values")
    crop_set, profile_set = set(crop_ids), set(profile_ids)
    if crop_set != profile_set:
        missing_crops = profile_set - crop_set
        extra_crops = crop_set - profile_set
        raise ValueError(
            f"crops and profiles do not join one-to-one: "
            f"{len(missing_crops)} cells missing crops, "
            f"{len(extra_crops)} crops without a matching cell"
        )


def decode_jpeg(data: bytes) -> np.ndarray:
    """Decode a crop JPEG column value back to a 2D uint8 array (for QC)."""
    return np.array(Image.open(io.BytesIO(data)))
