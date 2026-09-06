from collections import deque

from common.perception_types import EmotionLabel


class TemporalSmoother:
    """Minimal three-sample majority smoother placeholder."""

    def __init__(self, window_size: int = 3) -> None:
        if window_size < 1:
            raise ValueError("window_size must be positive")
        self._window: deque[EmotionLabel] = deque(maxlen=window_size)

    def update(self, label: EmotionLabel) -> EmotionLabel | None:
        self._window.append(label)
        if len(self._window) < self._window.maxlen:
            return None
        counts = {candidate: self._window.count(candidate) for candidate in set(self._window)}
        winner, count = max(counts.items(), key=lambda item: item[1])
        return winner if count > len(self._window) / 2 else EmotionLabel.UNKNOWN
