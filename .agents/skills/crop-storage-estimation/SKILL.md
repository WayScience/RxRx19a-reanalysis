______________________________________________________________________

## name: crop-storage-estimation description: 'Use when estimating crop-table storage from pilot file size, crop resolution, channel count, and cell count.'

# Crop Storage Estimation

Estimate the size of Parquet tables that contain per-cell JPEG crops.
Use measured pilot storage instead of a generic JPEG compression ratio.

## Scope

This estimate includes JPEG bytes and metadata inside the crop tables.
It excludes source images, masks, feature tables, temporary files, backups, and filesystem overhead.
It estimates logical file bytes, not allocated disk blocks.

## Inputs

- `pilot_bytes`: sum of the pilot crop-table file sizes, in bytes.
- `pilot_cells`: sum of the row counts in those same files.
- `pilot_width`, `pilot_height`: pilot crop dimensions, in pixels per channel.
- `pilot_channels`: number of JPEG channels per cell in the pilot.
- `total_cells`: estimated number of cells in the target dataset.
- `width`, `height`: target crop dimensions, in pixels per channel.
- `channels`: number of JPEG channels per cell in the target dataset.
- `headroom`: extra storage fraction, with `0.25` as the planning default.

## Procedure

1. Select representative pilot crop tables with the target JPEG quality, bit depth, masking, and Parquet settings.
1. Sum their file sizes and row counts across the same set of files.
1. Read crop dimensions and JPEG quality from `crop_width`, `crop_height`, and `jpeg_quality`.
1. Count the JPEG columns, such as `crop_w1_jpeg` through `crop_w5_jpeg`.
1. Verify that pilot counts and dimensions are positive, target cells are nonnegative, and headroom is nonnegative.
1. If pilot files use different settings, calculate separate estimates for each group.
1. Calculate the expected size and planning budget with the formulas below.
1. Report the input values, assumptions, expected GB, and budget GB.

```text
bytes_per_cell = pilot_bytes / pilot_cells

resolution_scale = (width * height) / (pilot_width * pilot_height)
channel_scale = channels / pilot_channels

expected_bytes = bytes_per_cell * total_cells * resolution_scale * channel_scale
expected_GB = expected_bytes / 1_000_000_000
budget_GB = expected_GB * (1 + headroom)
```

GB means decimal gigabytes.
For GiB, divide bytes by `1_073_741_824` instead.

## Measured ReRx pilot

The `pilot-dev` run contains seven crop tables under `runs/pilot-dev/crops/cells/` within the durable ReRx root.
The measured totals are:

| Input             | Value                            |
| ----------------- | -------------------------------- |
| Total file bytes  | 134,546,214                      |
| Total cells       | 33,330                           |
| Crop dimensions   | 96 × 96 pixels                   |
| Channels per cell | 5                                |
| Pixel format      | 8-bit grayscale                  |
| JPEG quality      | 95                               |
| Mask background   | Black outside the cell           |
| File format       | Parquet with JPEG binary columns |

These totals give about 4,037 bytes per cell, including all five channels and table metadata.
Use this shortcut for similar data:

```text
expected_GB = (134_546_214 / 33_330) * total_cells / 1_000_000_000
              * (width * height) / (96 * 96)
              * channels / 5

budget_GB = expected_GB * 1.25
```

At unchanged settings, use about 4 GB per million cells, or 5 GB per million cells with headroom.
Ten million cells give an expected size of 40.4 GB and a planning budget of 50.5 GB.

## Limits and recalibration

Cell-count scaling is the strongest assumption when crop settings and image content remain similar.
Resolution and channel scaling are rough approximations, not exact JPEG size predictions.
JPEG headers and table metadata do not scale directly with pixel area.
Larger masked crops can add mostly black pixels, rather than proportionally more image content.

The 25% headroom is a planning allowance, not a statistical confidence bound.
It does not guarantee enough storage for a different cell population or crop configuration.

If JPEG quality, bit depth, masking, channels, or image content changes, measure a new representative sample.
Do not adjust JPEG quality or bit depth with an assumed linear factor.
For more reliable resolution estimates, encode sample crops at the target dimensions before extrapolation.

The implementation in `src/rerx/crops.py` defines the crop settings and JPEG encoding.
Verify those settings before reusing this calibration.
