# Methodology

> Written during S23. This file is what makes the accuracy claims checkable — do not skip it.

Sections to cover:

1. Why shelf stations rather than free roam (webcam gaze is unusable with a moving camera)
2. Noise pipeline: calibration gate, confidence filter, median filter, I-DT fixation detection, session gate — with parameter values and the Day 7 freeze commit hash
3. Attention fusion formula and why gaze is weighted below interaction
4. Saliency model and its weights
5. Persona policies (LLM-generated) and persona agents (LLM step-by-step); how the simulator scales them
6. Pre-registration protocol: what is hashed, when, and how `scripts/eval.py` verifies ordering
7. Metric definitions: Spearman, KL, purchase-share MAE, decision agreement, Ad-to-Purchase Lift
8. Noise ceiling: split-half method, why it is the correct benchmark, and why "more accurate than humans" is not a coherent claim
9. Calibration and holdout protocol: fit on A only, report B and C separately
10. Known-effect check
11. Privacy: in-browser gaze, coordinates only, no frames, anonymous sessions, explicit consent
12. Limitations: sample bias (colleagues, not shoppers), webcam gaze error, persona policies authored by an LLM, single category
