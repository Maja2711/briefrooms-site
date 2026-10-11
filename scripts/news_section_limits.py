#!/usr/bin/env python3
"""Single source of truth for PL/EN section-card limits.

Minimum is an editorial coverage target (warn on missing quality content).
Maximum is a hard publication cap. Homepage limits are independent.
"""

NEWS_SECTION_MINIMUM = 6
NEWS_SECTION_MAXIMUM = 15
NEWS_SECTION_LIMITS_VERSION = "news-section-limits-6-15-v1"

assert 0 < NEWS_SECTION_MINIMUM <= NEWS_SECTION_MAXIMUM
