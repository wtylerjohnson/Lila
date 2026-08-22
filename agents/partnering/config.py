"""Named configuration for the partnering engine; judgment defaults live
here with their provenance, never as inline literals."""

# SCALE blocker: the pursuit's dollar signal (NAICS median award; MARKET
# PROXY per the grading convention) counts as beyond the client's reach when
# it exceeds the attested award-band ceiling by this multiple. Judgment
# default approved 2026-07-07; expected to be tuned. The blocker worksheet
# states the multiple used so a reviewer can see and challenge it.
SCALE_MULTIPLE = 3.0

# Subcontracting-plan dollar threshold (FAR 19.702(a)): contracts expected to
# exceed this figure (other than construction) require a subcontracting plan
# from other-than-small awardees; the demand signal keys on it. The figure
# adjusts under 41 USC 1908 inflation reviews, so it is a named config with
# provenance, never an inline literal:
#   VERIFIED 2026-07-07 against https://www.acquisition.gov/far/19.702
#   (live fetch): "expected to exceed $900,000 ($2 million for construction)".
# Set to None if unverifiable at a future review; the demand signal then
# degrades to N/A with a flag instead of guessing.
SUBCONTRACTING_PLAN_THRESHOLD_USD = 900_000  # float | None (None = unverified)
SUBCONTRACTING_THRESHOLD_PROVENANCE = (
    "FAR 19.702(a), acquisition.gov/far/19.702, verified 2026-07-07 "
    "(non-construction figure; construction is $2M)")

# Standing informational flag attached to every set-aside-involved teaming
# direction. LILA states that the limitation exists and cites the clause
# family; it NEVER computes or advises on compliance percentages.
LIMITATIONS_ON_SUBCONTRACTING_FLAG = (
    "Limitations on subcontracting apply to set-aside awards "
    "(FAR 52.219-14 clause family); human review required; compliance "
    "percentages are not computed here.")

# JV/teaming arrangement structure selection is a human decision; the system
# only flags that the shape may fit.
JV_STRUCTURE_FLAG = ("JV / teaming-arrangement structure selection is a "
                     "human decision; flagged only, not recommended.")
