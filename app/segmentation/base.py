"""Segmenter interface. Any segmenter (classical, ilastik-style pixel classifier, a future
deep model, ...) only has to turn one well region into a boolean "cell pixels" mask.
Candidate grouping, review, measurement and export do not depend on how that mask is made.

Nothing the threshold selected is dropped silently: every pixel that passed the cell
threshold but was removed by a clean-up step is returned in `removed[reason]`, and pixels
the clean-up ADDED without passing the threshold (gap closing, hole filling) are returned
in `added`. Contract (checked by tests/test_review_integrity.py, starting from `raw`):
    raw                       subset of  cells | excluded | union(removed)
    cells | excluded | union(removed)  subset of  raw | added
    added                     = (cells | excluded | union(removed)) & ~raw
Reasons used by the built-in segmenters:
    wall_band   inside the wall margin next to the well boundary
    thin        removed by morphological opening (thin lines, cluster fringes)
    small       component smaller than the speck limit
    wall_shape  long-thin / L-shaped / edge-hugging component (the old 'excluded')
    split_line  the cut between two pieces of a watershed-split component (optional
                profile). All line pixels are kept, including pixels that closing/filling
                had added (those stay in `added`), so re-assigning the line restores the
                unsplit mask exactly.
"""
from dataclasses import dataclass, field

import numpy as np

REMOVAL_REASONS = ('wall_shape', 'wall_band', 'thin', 'small', 'split_line')


@dataclass
class SegmentationResult:
    cells: np.ndarray                 # bool (H, W): kept cell pixels, inside the region
    excluded: np.ndarray              # bool (H, W): wall/shadow-shaped components (reason 'wall_shape')
    info: dict = field(default_factory=dict)
    removed: dict = field(default_factory=dict)   # reason -> bool (H, W), other removal steps
    added: np.ndarray = None          # bool (H, W): pixels added by closing / hole filling
    raw: np.ndarray = None            # bool (H, W): every region pixel that passed the cell threshold


class Segmenter:
    name = 'base'

    def segment(self, rgb: np.ndarray, region: np.ndarray, params: dict) -> SegmentationResult:
        """rgb: uint8 (H, W, 3) crop around one well. region: bool (H, W), True inside the well."""
        raise NotImplementedError
