from __future__ import annotations

import numpy as np

from momo_camera_hub.opencv_capture import fit_frame


def test_fit_frame_preserves_aspect_ratio_with_pillarbox() -> None:
    source = np.full((1080, 608, 3), 255, dtype=np.uint8)

    fitted = fit_frame(source, 1920, 1080)

    assert fitted.shape == (1080, 1920, 3)
    assert fitted[:, :650].max() == 0
    assert fitted[:, 656:1264].min() == 255
    assert fitted[:, 1270:].max() == 0


def test_fit_frame_returns_exact_size_unchanged() -> None:
    source = np.full((1080, 1920, 3), 127, dtype=np.uint8)

    assert fit_frame(source, 1920, 1080) is source
