"""Read-time difficulty gates; AI scores remain estimates, never official ratings."""
import math

from .difficulty import selection_range


def metadata_matches(evidence, minimum, maximum):
    """Wide cross-platform uncertainty alone cannot turn an easy task into 1800."""
    low, high = evidence.get('minimum'), evidence.get('maximum')
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (low, high)):
        return False
    lower, upper = selection_range(minimum, maximum)
    center = evidence.get('estimated_rating', (low + high) / 2)
    return type(center) in (int, float) and math.isfinite(center) and lower <= center <= upper


def decision(assessment, minimum, maximum):
    rating = assessment.get('rating')
    if assessment.get('confidence') == 'unknown' or type(rating) not in (int, float) or not math.isfinite(rating):
        return 'undetermined'
    # A point target 1800 requires AI >=1800, not the 1700 prefilter fringe.
    if rating < minimum:
        return 'below_minimum'
    if rating > selection_range(minimum, maximum)[1]:
        return 'above_maximum'
    return 'accepted'
