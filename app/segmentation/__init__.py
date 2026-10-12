"""Registry of segmenters. To add one (e.g. a trained pixel classifier), implement
base.Segmenter and register it here; profiles in config/profiles.json select it by name."""
from .adaptive import AdaptiveSegmenter
from .classical import ClassicalSegmenter
from .cellpose_seg import CellposeSegmenter

SEGMENTERS = {s.name: s for s in (ClassicalSegmenter(), AdaptiveSegmenter(), CellposeSegmenter())}


def get_segmenter(name):
    if name not in SEGMENTERS:
        raise KeyError(f'unknown segmenter {name!r}; available: {sorted(SEGMENTERS)}')
    return SEGMENTERS[name]
