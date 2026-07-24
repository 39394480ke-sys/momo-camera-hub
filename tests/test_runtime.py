from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from momo_camera_hub.config import AppConfig
from momo_camera_hub.runtime import CommandError, StreamSupervisor, count_path_readers


@pytest.mark.asyncio
async def test_stream_probe_retries_while_publisher_is_starting(tmp_path: Path) -> None:
    supervisor = StreamSupervisor(AppConfig.default(platform_name="Darwin", home=tmp_path))
    supervisor._probe_stream_once = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            CommandError("404 Not Found"),
            {"width": 1920, "height": 1080, "fps": "30/1"},
        ]
    )

    result = await supervisor._probe_stream(timeout=1)

    assert result["width"] == 1920
    assert supervisor._probe_stream_once.await_count == 2


def test_count_path_readers_only_counts_selected_stream() -> None:
    payload = {
        "items": [
            {"name": "armcam", "readers": [{"id": "one"}, {"id": "two"}]},
            {"name": "other", "readers": [{"id": "three"}]},
        ]
    }

    assert count_path_readers(payload, "armcam") == 2
    assert count_path_readers(payload, "missing") == 0
