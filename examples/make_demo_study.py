#!/usr/bin/env python3
"""
Build a synthetic metabolomics study package for testing lil-agent.

  python3 examples/make_demo_study.py [OUT_DIR]

Writes OUT_DIR/demo_study.zip (the package an agent sees, laid out like a
real one: instructions, a bundled skill, study design, data) and
OUT_DIR/demo_truth.json (the planted effects, for grading; not in the zip).
All data are simulated: a soil bacterium grown in control, low-phosphate
and low-nitrogen media, C18 positive-mode LC-MS exometabolomics.
"""

import json
import sys
import zipfile
from pathlib import Path

import numpy as np

rng = np.random.default_rng(20261030)

GROUPS = {"control": 4, "lowP": 4, "lowN": 4}
N_QC, N_BLANK, N_FEATURES = 3, 2, 120

# Annotated compounds: (name, formula, adduct, m/z, MSI level)
COMPOUNDS = [
    ("sn-Glycerol 3-phosphate", "C3H9O6P", "[M+H]+", 173.0210, 2),
    ("Adenosine monophosphate", "C10H14N5O7P", "[M+H]+", 348.0704, 2),
    ("O-Phosphoethanolamine", "C2H8NO4P", "[M+H]+", 142.0264, 2),
    ("Glucose 6-phosphate", "C6H13O9P", "[M+H]+", 261.0370, 3),
    ("Phosphocholine", "C5H15NO4P", "[M]+", 184.0733, 2),
    ("L-Glutamine", "C5H10N2O3", "[M+H]+", 147.0764, 1),
    ("L-Arginine", "C6H14N4O2", "[M+H]+", 175.1190, 1),
    ("L-Asparagine", "C4H8N2O3", "[M+H]+", 133.0608, 2),
    ("Spermidine", "C7H19N3", "[M+H]+", 146.1652, 2),
    ("Adenine", "C5H5N5", "[M+H]+", 136.0618, 1),
    ("Trehalose", "C12H22O11", "[M+Na]+", 365.1054, 2),
    ("Glycine betaine", "C5H11NO2", "[M+H]+", 118.0863, 2),
    ("Pyoverdine fragment (putative)", "", "[M+2H]2+", 668.2920, 4),
    ("4-Hydroxybenzoic acid", "C7H6O3", "[M+H]+", 139.0390, 3),
    ("Indole-3-acetic acid", "C10H9NO2", "[M+H]+", 176.0706, 2),
    ("Riboflavin", "C17H20N4O6", "[M+H]+", 377.1456, 1),
    ("Uracil", "C4H4N2O2", "[M+H]+", 113.0346, 2),
    ("Hypoxanthine", "C5H4N4O", "[M+H]+", 137.0458, 2),
    ("Nicotinic acid", "C6H5NO2", "[M+H]+", 124.0393, 2),
    ("Succinic acid", "C4H6O4", "[M+H]+", 119.0339, 3),
    ("Polyethylene glycol n=6 (contaminant)", "C12H26O7", "[M+H]+", 283.1751, 2),
    ("Erucamide (contaminant)", "C22H43NO", "[M+H]+", 338.3417, 2),
]


def main(out):
    out.mkdir(parents=True, exist_ok=True)
    samples = []
    for group, n in GROUPS.items():
        samples += [(f"{group}_{i + 1}", "sample", group) for i in range(n)]
    samples += [(f"QC_{i + 1}", "pooled_QC", "") for i in range(N_QC)]
    samples += [(f"blank_{i + 1}", "extraction_blank", "") for i in range(N_BLANK)]
    order = rng.permutation(len(samples)) + 1

    feature_ids = [f"FT{i + 1:04d}" for i in range(N_FEATURES)]
    base = rng.lognormal(mean=12, sigma=1.5, size=N_FEATURES)
    # Feature roles: the first 22 are the annotated compounds above.
    up_lowP = [0, 1, 2, 3, 4, 30, 31, 32]           # 4x up in lowP
    down_lowN = [5, 6, 7, 8, 40, 41]                # 4x down in lowN
    noisy = [60, 61, 62, 63, 64, 65]                # QC RSD well above 30%
    blank_contam = [20, 21, 70, 71]                 # as high in blanks as samples
    sparse = [80, 81, 82, 83, 84]                   # missing in most samples

    groups = [s[2] for s in samples]
    types = [s[1] for s in samples]
    # The pooled QC averages the three groups equally.
    pooled = base.copy()
    pooled[up_lowP] *= (1 + 1 + 4) / 3
    pooled[down_lowN] *= (1 + 1 + 0.25) / 3
    X = np.zeros((N_FEATURES, len(samples)))
    for j, (sid, stype, group) in enumerate(samples):
        mean = base.copy()
        if group == "lowP":
            mean[up_lowP] *= 4
        if group == "lowN":
            mean[down_lowN] /= 4
        if stype == "pooled_QC":
            mean = pooled.copy()
        cv = np.where(np.isin(np.arange(N_FEATURES), noisy), 0.6, 0.12)
        if stype == "pooled_QC":
            cv = np.where(np.isin(np.arange(N_FEATURES), noisy), 0.6, 0.05)
        X[:, j] = mean * rng.lognormal(0, cv)
        if stype == "extraction_blank":
            X[:, j] = base * rng.uniform(0.001, 0.01, N_FEATURES)
            X[blank_contam, j] = base[blank_contam] * rng.uniform(0.8, 1.2, len(blank_contam))
    for f in sparse:
        miss = rng.choice(len(samples), size=len(samples) - 3, replace=False)
        X[f, miss] = 0
    # Mild signal drift with injection order.
    X *= (1 - 0.01 * (order - 1))[None, :]

    mz = np.round(rng.uniform(100, 900, N_FEATURES), 4)
    rt = np.round(rng.uniform(0.8, 14.5, N_FEATURES), 2)
    for i, c in enumerate(COMPOUNDS):
        mz[i] = c[3]

    files = {}
    files["README.md"] = """\
# JGI metabolomics report: SoilBac PhosN (DEMO, simulated data)

This package contains a simulated exometabolomics experiment. Start with
INSTRUCTIONS.md.

| File | Contents |
|---|---|
| INSTRUCTIONS.md | research question and what to deliver |
| skills/metabolomics-light/SKILL.md | analysis guidance to follow |
| study_design.md | experiment and sample descriptions |
| data/samples.csv | one row per injection: sample_id, sample_type, treatment, injection_order |
| data/feature_table.csv | feature_id, mz, rt_min, then peak areas per sample (0 = not detected) |
| data/annotations.csv | library matches / putative identifications for some features |
"""
    files["INSTRUCTIONS.md"] = """\
# Instructions

**Research question.** How do phosphate and nitrogen limitation change the
exometabolome of the soil bacterium *Pseudomonas* sp. SB-1?

Analyse the data in data/ following the guidance in
skills/metabolomics-light/SKILL.md, using study_design.md for the
experimental factors. Then write outputs/report.md: a short results section
suitable for a paper draft, with figures referenced, methods in brief, and
caveats.
"""
    files["skills/metabolomics-light/SKILL.md"] = """\
---
name: metabolomics-light
description: Light-weight untargeted LC-MS metabolomics analysis - QC, PCA, per-feature ANOVA with FDR, box plots, and identification reporting by MSI level. Use for feature tables with pooled QCs and blanks.
---

# Light-weight metabolomics analysis

1. **Quality control.** Use the pooled QC injections to compute each feature's
   relative standard deviation (RSD); flag features with QC RSD > 30%.
   Use the extraction blanks to flag features whose mean sample intensity is
   less than 3x the mean blank intensity. Report missingness, and look for
   drift with injection order. Remove flagged features before statistics and
   say how many were removed for each reason.
2. **Ordination.** log2-transform (handle zeros sensibly), then PCA of the
   biological samples (and separately with QCs included as a sanity check).
   Plot PC1 vs PC2 coloured by the main experimental factor.
3. **Statistics.** One-way ANOVA per feature across the groups,
   Benjamini-Hochberg FDR. Report features with FDR < 0.05 and, for those,
   which group differs (post-hoc test of your choice).
4. **Box plots** of the top significant features (at most 8).
5. **Identifications.** For significant features that have annotations,
   report the compound with its MSI level. Treat MSI level 3-4 as putative;
   never name a compound more confidently than its level allows.
"""
    files["study_design.md"] = """\
# Study design

*Pseudomonas* sp. SB-1 was grown in defined minimal medium (control), the
same medium with 1/50 phosphate (lowP), or with 1/50 ammonium (lowN);
4 biological replicates each, harvested in stationary phase. Spent media
were filtered (0.22 um), extracted with methanol, and analysed by LC-MS/MS
(C18 column, positive ESI). A pooled QC (equal volumes of all samples) was
injected 3 times across the run; 2 extraction blanks were prepared with
uninoculated medium. Injection order was randomized.
"""
    rows = ["sample_id,sample_type,treatment,injection_order"]
    rows += [f"{s[0]},{s[1]},{s[2]},{o}" for s, o in zip(samples, order)]
    files["data/samples.csv"] = "\n".join(rows) + "\n"
    header = "feature_id,mz,rt_min," + ",".join(s[0] for s in samples)
    lines = [header]
    for i, fid in enumerate(feature_ids):
        lines.append(f"{fid},{mz[i]},{rt[i]}," + ",".join(f"{v:.0f}" for v in X[i]))
    files["data/feature_table.csv"] = "\n".join(lines) + "\n"
    ann = ["feature_id,compound_name,formula,adduct,library,match_score,msi_level"]
    for i, (name, formula, adduct, _, level) in enumerate(COMPOUNDS):
        score = round(float(rng.uniform(0.7, 0.98)), 2)
        ann.append(f"{feature_ids[i]},\"{name}\",{formula},{adduct},GNPS,{score},{level}")
    files["data/annotations.csv"] = "\n".join(ann) + "\n"

    zpath = out / "demo_study.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(f"demo_study/{name}", text)
    truth = {
        "up_in_lowP": [feature_ids[i] for i in up_lowP],
        "down_in_lowN": [feature_ids[i] for i in down_lowN],
        "high_qc_rsd": [feature_ids[i] for i in noisy],
        "blank_contaminants": [feature_ids[i] for i in blank_contam],
        "sparse": [feature_ids[i] for i in sparse],
    }
    (out / "demo_truth.json").write_text(json.dumps(truth, indent=2))
    print(f"wrote {zpath} and {out / 'demo_truth.json'}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
