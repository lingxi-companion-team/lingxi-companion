from common.perception_types import EmotionLabel, PerceptionResult


def majority_label(results: list[PerceptionResult]) -> EmotionLabel:
    """Minimal placeholder for the future fusion engine."""
    if not results:
        return EmotionLabel.UNKNOWN
    counts: dict[EmotionLabel, int] = {}
    for result in results:
        counts[result.label] = counts.get(result.label, 0) + 1
    return max(counts, key=counts.get)
