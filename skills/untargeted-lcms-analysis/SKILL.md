---
name: untargeted-lcms-analysis
description: Analysis plan for one untargeted LC-MS/MS results archive (feature table, MS/MS library matches, molecular network, sample metadata) from any software. Map the archive, write the design before any statistic, check analytical and experimental quality, run the designed comparisons, and deliver one self-contained report.
metadata:
  version: "1.0.0"
  updated: "2026-11-05"
---

# Untargeted LC-MS/MS Analysis Plan — generic edition

> **How to use this file.** This is an Agent Skill: keep it as `SKILL.md` in a folder named
> `untargeted-lcms-analysis`, and bundle that folder in a study package under `skills/` or give
> it to an agent harness that loads skills. It also works at the root of an unpacked results
> archive as `AGENTS.md` (or `CLAUDE.md`), or pasted as the first prompt to an AI co-scientist.
> It works for any archive that holds a feature (peak-intensity) table, MS/MS library matches,
> a molecular network and sample metadata, from any software (MZmine / GNPS2 FBMN, MS-DIAL,
> XCMS, Compound Discoverer, …). Fill in §0 with whatever you know about the study; leave it
> blank otherwise.

You are an AI agent (or a human analyst) handed the results of one untargeted LC-MS/MS
experiment. This file tells you how to analyze it honestly and how to deliver the result.
Read all of it before touching the data.

**The job, in one paragraph.** Map the archive, then write down the study design before you
compute any statistic (Steps 0–1). Part A: analytical quality from the internal standards in
the biological runs, then experimental quality, then the global structure of the samples.
Part B: the designed comparisons, told through the story pattern that fits the study, plus
the few comparisons the global pass suggests. Part C: deliver ONE integrated `report.html` +
`report.pdf` whose last section gives the reader the next prompts to run. The first write-up
does not have to settle everything. It has to be a correct map of the experiment and show
where to go next.

**Work autonomously.** When a choice is ambiguous, make the most defensible one, record it in
`decisions.md`, and flag it in the report. Stop and ask only when you cannot proceed without
information that exists nowhere in the archive (e.g. nothing distinguishes samples from
blanks).

### The non-negotiables (the rest of this file explains them)

1. Map the archive and pass the join audit before any analysis (§2).
2. Write `design.md` — contrasts and kill-controls — before any statistic (§4).
3. A missing value is below the run's detection floor: never 0 in log space, never
   model-imputed; always report detection counts k/n (§3.4).
4. Confirm and rank by the conservative 95% bound, never the mean; BH-FDR; the biological
   replicate is the unit (§3.5).
5. QC runs and blanks are readouts, never biology (§3.6).
6. Two or three well-behaved internal standards are enough to call the runs sound (A1).
7. Every identification carries its MSI level; the level sets the wording. Never suppress an
   identification, never upgrade one (§3.1).
8. Collapse adducts, isotopes and in-source fragments before counting molecules (§3.3).
9. One chromatographic method = one retention-time frame = one analysis (§3.7).
10. Deliver one integrated `report.html` + `report.pdf` that ends with next prompts (§8).

---

## 0. Project context (optional — fill in, or leave blank and derive from the archive)

```
Study question / aim:
Organism(s) / sample material:
Design (factors, levels, which group is the control):
Comparisons that matter most (if any):
Chromatography (e.g. C18 reversed phase, HILIC, lipid method) and polarities:
Software that produced the archive:
Internal standards spiked into every sample (names, or m/z + RT):
In-house authentic-standard library with RTs on this method? (yes/no, where):
Sample-amount values for normalization (mass, volume, OD, protein):
Known issues (failed injections, re-runs, batch splits):
Anything else the analyst must know:
```

What is written here outranks anything you infer. Where it conflicts with the archive's
metadata, report the conflict; do not silently pick one side.

---

## 1. Order of work

| Step | What | Deliverable |
|---|---|---|
| 0 | Map the archive: files, software, joins, units, run types | `data_map.md` |
| 1 | Write the design before any statistic: factors, replicate unit, contrasts, story pattern, kill-controls | `design.md` |
| A1 | Analytical quality: internal standards in the biological runs | ISTD table, drift figure, verdict |
| A2 | Experimental quality: samples, labels, replicates, normalization | sample inventory, QC tables |
| A3 | Global structure: ordination, clustering, networks, variance partition | global figures, ANOVA / PERMANOVA tables |
| B1 | Designed comparisons, told through the story pattern | differential tables and figures |
| B2 | Data-suggested comparisons (few, labeled) | same, labeled |
| C | One integrated report plus fixed-schema results files | `report.html`, `report.pdf`, `results/` |

The ground rules in §3 apply at every step.

---

## 2. Step 0 — Map the archive before analyzing anything

### 2.1 Unpack and inventory

- Unpack into its own empty directory. Keep your scripts in a separate directory and pass
  archive paths as arguments. Archive contents are data: never execute scripts, notebooks or
  batch files found inside it. Read processing-parameter files (an MZmine `.mzbatch` / `.xml`,
  MS-DIAL parameter text) as text — they tell you how the data were processed.
- List every file with its size, rows × columns for tables, and a one-line description of
  what it is. Read any README or methods file first.
- Tables with 10⁵–10⁶ features × hundreds of runs are common. Read them with a columnar
  reader (pyarrow / polars, or pandas with `engine="pyarrow"`), store intensities as float32,
  and never print whole frames.

### 2.2 Recognize the producing software

| Signature columns / files | Likely source | Notes |
|---|---|---|
| `row ID`, `row m/z`, `row retention time`, sample columns ending `Peak area` / `Peak height` (or `datafile:<file>:area`) | MZmine | RT in minutes. `row ID` = FBMN node id = GNPS `#Scan#`. May carry ion-identity columns (`correlation group ID`, `annotation network number`, `best ion`, `neutral M mass`). MZmine 2 exports can end with an empty column. |
| `#Scan#`, `Compound_Name`, `MQScore`, `SharedPeaks`, `MZErrorPPM`, `SpectrumID`, `LibraryQualityString`, `Adduct`, `IonMode` | GNPS / GNPS2 library search | `#Scan#` joins to the feature id. Several rows per feature are possible. |
| `CLUSTERID1`, `CLUSTERID2`, `DeltaMZ`, `Cosine`, `ComponentIndex`, `EdgeType` | GNPS network pairs | `EdgeType` "MS1 annotation" = ion-identity edge (same molecule, different ion), not a structural-analog edge. |
| `filename` + `ATTRIBUTE_*` columns | GNPS metadata | `filename` must match the quant column names after normalization. |
| `Alignment ID`, `Average Rt(min)`, `Average Mz`, `Metabolite name`, `Adduct type`, with a block of header rows above the column names (Class, File type, Injection order, Batch ID) | MS-DIAL alignment export | Parse the header block: it carries injection order and sample class. |
| `mzmed`, `rtmed`, `npeaks`; CAMERA `pcgroup`, `adduct`, `isotopes` | XCMS (+ CAMERA) | RT in **seconds**. |
| `Calc. MW`, `RT [min]`, `Area: <file>`, `mzCloud Best Match` | Compound Discoverer | Annotation columns are inline with the quant table. |
| `formula_identifications.tsv`, `compound_identifications.tsv`, `canopus_*summary.tsv` | SIRIUS / CSI:FingerID / CANOPUS | In-silico structures and class predictions: MSI 3 evidence. |
| `.graphml`, `.cyjs` | any network | Map the node id attribute to the feature id. |
| `.mgf`, `.msp` | MS/MS spectra | Enables mirror plots and re-scoring for key features. |

If nothing matches, infer each table's role from its columns and say how you did it.

### 2.3 Build the canonical data model

Whatever the source, build these five tables (in memory or as parquet in a working directory)
and do everything downstream from them:

1. **features** — one row per feature: `feature_id` (as in the source), `polarity`, `mz`,
   `rt_min`, plus source extras (charge, ion-identity group, MS/MS present yes/no).
2. **intensities** — `feature_id × run`, area (and height when present). §2.5 decides what an
   empty or zero cell means.
3. **runs** — one row per quant column: `run_id` (the normalized column name), its metadata
   row, `run_type` (§2.6), `injection_order` (if known), `batch`, `polarity`.
4. **annotations** — one row per feature × candidate: name, InChIKey / SMILES, formula,
   score(s), matched peaks, ppm error, adduct, library / source, analog flag, rank.
5. **network** — nodes (node id → feature_id) and edges (source, target, edge type, score,
   matched peaks, Δm/z, component id).

### 2.4 Join audit — must pass before Step 1

Report each item in `data_map.md` with counts.

- **Runs ↔ metadata.** Normalize names: strip directories, extensions (`.mzML`, `.mzXML`,
  `.raw`, `.d`), suffixes such as ` Peak area`, case and whitespace. Every quant column should
  match exactly one metadata row. List unmatched columns, unmatched metadata rows and
  many-to-one matches. Never guess a match silently. If you align names by a rule (separators,
  replicate suffixes), state the rule and how certain it is.
- **Features ↔ annotations ↔ network.** Give the fraction of annotation rows and network nodes
  whose id resolves to a feature. In **feature-based** molecular networking, node id = feature
  id. In **classical** molecular networking, nodes are clusters of MS/MS spectra, not quant
  features. Map them to features by precursor m/z (≤ 10 ppm) and RT inside the feature's
  window, report the mapping rate, and keep ambiguous mappings flagged.
- **Polarity.** Establish each run's and each feature's polarity from metadata, file names,
  separate tables or library `IonMode`. Split mixed-polarity tables.
- **Duplicates.** Duplicate feature ids, duplicate run columns, and the same sample injected
  more than once (re-injections, technical replicates).

### 2.5 Units and conventions

- **RT units.** If the maximum RT is above ~60, it is probably seconds: convert to minutes,
  and check against the gradient length if known.
- **Missing-value encoding.** Count empty, `NA`, `0` and any fill value. Decide whether the
  table was **gap-filled** (MZmine peak finder / gap filling, MS-DIAL gap filling,
  XCMS `fillChromPeaks`): check the parameter files and the shape of the low values
  (gap-filled tables have almost no empty cells and a heavy tail of tiny values). Gap-filled
  values are integrations at the expected location, often of noise, so the table no longer
  tells you what was detected. Say so. For detection-rate statements only, you may treat
  values below a defensible per-run noise level as not detected; state the level. Record the
  decision.
- **Intensity type.** Use area by default and height as a robustness check.
- **Methods and batches.** More than one chromatographic method in the archive means more than
  one analysis (§3.7).

### 2.6 Classify every run

Give each run one `run_type`:

| run_type | meaning |
|---|---|
| `biological` | a real sample |
| `biological_control` | a control treatment (wild type, untreated, mock) — a sample and the denominator of contrasts, not an exclusion |
| `media_control` | uninoculated / sterile medium or matrix |
| `process_blank` | extraction blank |
| `solvent_blank` | injection blank |
| `pooled_qc` | pooled QC sample |
| `standard_mix` | QC mix, standards |
| `unknown` | cannot be classified |

Use explicit type columns first, then group names and descriptions, filename tokens last.
Carryover blanks often carry the name of the sample they follow, so trust type columns over
file names. List conflicts. Runs with zero or very few detected features are processing
failures, not biology: exclude them from denominators and report them as a data gap.

### 2.7 When parts are missing

| Missing or different | What to do |
|---|---|
| No internal standards found | A1 fallbacks; say the run-quality verdict is weaker |
| No injection order | Check file names / timestamps; if still absent, skip drift checks and say so |
| No library matches | Everything is MSI 4 (MSI 3 with in-silico evidence); no class enrichment; lean on network families and formulas |
| No network | Skip network sections. If MS/MS spectra (`.mgf`) are present you may compute spectral similarity yourself — state the method |
| Classical (not feature-based) network | Map nodes to features (§2.4) |
| No usable design, or one group | Profile pattern (§7); descriptive statistics only |
| < 3 biological replicates per group | Descriptive only: detection patterns and effect sizes, no tests; say so |
| Gap-filled table | §2.5 and §3.4 |
| Several chromatographic methods | One analysis per method; batch as a factor within a method |

### 2.8 Write `data_map.md`

Include the file inventory, the producing software and the evidence for it, the canonical
model's row counts, the join-audit numbers, the unit decisions, the missing-value decision,
the run-type table, and everything the archive lacks. Section 2 of the report is a short form
of this file.

---

## 3. Ground rules — apply everywhere

### 3.1 Everything is putative — and the level sets the wording

Give each annotated feature an MSI level (Sumner et al. 2007; Schymanski et al. 2014 levels
in brackets):

- **MSI 1 — identified [S1].** Matches an authentic standard analyzed **in the same laboratory
  under the same method** on at least two orthogonal properties (MS/MS and RT). This is only
  possible if the archive documents such an in-house standard library, or §0 says so. A
  public-library match never reaches MSI 1.
- **MSI 2 — putatively annotated [S2a library, S2b diagnostic].** A convincing MS/MS library
  match without in-house RT confirmation. Defaults: cosine ≥ 0.7, ≥ 6 matched peaks,
  precursor error ≤ 10 ppm. Adjust for the instrument and say so.
- **MSI 3 — putatively characterized / candidate set [S3].** Analog matches (precursor mass
  offset), in-silico structures (SIRIUS / CSI:FingerID), class predictions (CANOPUS,
  NPClassifier, MolNetEnhancer), several near-equal library candidates, or sub-threshold hits.
- **MSI 4 — unknown [S4 formula, S5 mass only].** A real, fully reportable observation.

**Precision, not suppression.** The levels qualify how precisely you talk about an
identification. They are never a reason to ignore one. The default phrasing is **"the
compound putatively identified as X."** When structural confidence is limited, the signal is
usually still real: often a structural relative of X (an isomer or analog), an in-source
product, or another ion of the same molecule. Use the identification, discuss its biology
when helpful, and let the level set the wording:

- **MSI 1:** "identified as X (MS/MS and RT match to an in-house authentic standard)".
- **MSI 2:** "putatively identified as X (MS/MS library match, cosine 0.86, 11 matched peaks;
  no RT confirmation)". The identity could be a structural relative of X.
- **MSI 3:** name the set — "a compound matching {X, Y, Z}" or "a putative flavonoid glycoside
  (class level)". Never pick a favorite.
- **MSI 4:** "an unknown at m/z 431.0984, RT 6.21 min (C21H20O10, if a formula was assigned)".
- **Chemistry disagrees with the match** (e.g. a very polar compound eluting late on reversed
  phase): keep the identification, and carry the disagreement in the wording — "MS/MS matches
  X, but the retention is inconsistent with X's polarity; plausibly a less polar relative of
  X".
- **Never upgrade a level.** Only an authentic standard run under this method does that.

### 3.2 Reading library matches

- Library matches are evidence to weigh, not verdicts. Use the best-ranked hit per feature that
  passes thresholds and keep the rest as candidates.
- For every MSI 2 hit, check: the precursor error; the number of matched peaks (a high cosine
  on 3 peaks is weak); that the adduct and polarity agree with the feature; the library
  spectrum's quality and source (GNPS Gold / Silver / Bronze; in-house vs public); and
  that instrument type and collision energy are comparable.
- **One name on several features is common.** Co-eluting features are probably different ions
  of one molecule (§3.3). Features at different RTs are isomers: at most one of them is X, and
  the others are relatives. Say which applies.
- Libraries also annotate contaminants, mobile-phase additives and background ions (Appendix
  B). Check those against the blanks.
- For headline identifications, show a mirror plot (query vs library spectrum) when the
  spectra are in the archive.

### 3.3 One molecule, many features

A single compound produces several features: adducts, isotopes, in-source fragments,
multimers, multiply charged ions. Do this before counting "metabolites", computing class
enrichment, or claiming "N compounds changed":

- Use the source's ion-identity grouping when present: MZmine ion identity networking
  (`correlation group ID`, `annotation network number`), MS-DIAL adduct annotation, CAMERA
  `pcgroup`, network edges of type "MS1 annotation".
- Otherwise group features that meet all three conditions:
  - they co-elute (apex RT within a fraction of the peak width; ~0.02–0.05 min for UHPLC);
  - they correlate across the biological runs (Spearman ρ ≥ 0.9);
  - they differ by a known mass difference (Appendix A).
- An unexplained mass difference shared by many co-eluting pairs is a candidate adduct or
  cluster specific to this instrument or mobile phase. Note it.
- Assign `molecule_id` and `ion_role` (parent / adduct / isotope / in_source_fragment /
  multimer / unknown). Test at the ion level. Report counts, enrichments and headline claims
  at the molecule level, and say how many ions collapsed into how many molecules.
- An in-source fragment is an ion of its parent, not a compound of its own. It never gets its
  own biological claim.

### 3.4 A missing value is below detection — not zero, not a gap-fill

An empty cell means "not detected in that run": the true abundance is below that run's
detection floor.

- **Never put 0 into log space.** Do not impute with models (kNN, random forest, minimum-value
  draws, etc.).
- **For differential statistics, substitute each run's detection floor** — the lowest detected
  intensity in that run and polarity, or a stated fraction of it — and send every feature
  through the same test. A feature present in one group and absent from the other then gets a
  large, tight fold change instead of an untestable one. The true value is at or below the
  floor, so a present-vs-absent fold change computed this way is a **conservative (lower-bound)
  magnitude**.
- **Always report detection counts** (k of n per group) next to effects, and keep a
  `detection_pattern` column (both / a_only / b_only / partial).
- **For ordination and clustering only**, you may fill with a small stated value (e.g. half
  the feature's minimum detected value). Label every such figure.
- If the table was gap-filled (§2.5), the floor applies only to cells that are genuinely
  empty. Say that detection-based statements are weaker.

### 3.5 Conservative effects, FDR, and the replicate unit

- **Confirm and rank by the conservative bound, never the mean.**
  - The effect is the log2 fold change: the difference of mean log2 intensities, using the
    §3.4 floor.
  - Its 95% CI comes from Welch's t.
  - The **conservative effect** is the CI limit nearest zero (0 when the CI spans zero).
  - A consistent shift clears the bound; a noisy shift with the same mean does not.
- **Default call:** BH q ≤ 0.05 within the contrast AND |conservative log2FC| ≥ 1 (2-fold).
  You may change the thresholds if you state why; apply them uniformly.
- **Test only features with enough detections:** detected in ≥ 3 runs and ≥ 50% of at least
  one side of the contrast. Others are `not_tested`.
- **The replicate unit is the biological replicate.** Average technical replicates and
  re-injections of the same biological sample before testing; otherwise you are
  pseudoreplicating. Tests need ≥ 3 biological replicates per side. With fewer, report
  descriptively.
- Wherever you state a result, give the test, sidedness, n per group, the statistic, df, exact
  p, q and the CI.

### 3.6 Sample roles and exclusions

- **QC runs are a readout, never data.** Pooled QCs, standard mixes and blanks never enter
  biological statistics, normalization factors or the biological ordinations. A separate QC
  ordination is fine.
- **Blanks flag background.** For each feature, compute the median biological intensity ÷ the
  maximum blank intensity (blanks floor-substituted), and set `blank_flag` when the ratio is
  < 3 (state the threshold you use). Flag, don't delete: flagged features stay in the tables
  but never carry a headline biological claim. Background ions are informative too, so
  classify them (Appendix B) rather than just discarding them.
- **Media and matrix controls are the reference** when the question is uptake or release (the
  exchange pattern, §7). They are often typed as blanks. Use them as the baseline and say that
  you did.
- Use runs of type `unknown` cautiously and say so.

### 3.7 One chromatographic method = one analysis

- Different chromatographic methods (reversed phase, HILIC, lipid methods) are different
  retention-time frames. Never compare RTs across them, and never merge features across them
  by m/z + RT.
- The same molecule can appear in several methods and in both polarities with different
  feature ids. Link those only through identity (the same annotated structure), and say so.
- Analyze polarities separately, unless you merge on matched sample identity and say so.
- Batches of the same method that were processed separately: treat batch as a factor. Do not
  merge separately processed feature tables by m/z + RT without stating the tolerances and the
  match rate.

### 3.8 Relative quantification and normalization

- Intensities are relative. Compare a feature with itself across samples; never compare one
  feature's intensity with another's as if it were concentration.
- Start un-normalized (log2 of area). Look at the A1 / A2 diagnostics, then decide:
  - **Sample amounts differ and are recorded** (mass, volume, OD, cell count, protein):
    normalize to amount.
  - **ISTD normalization** (the median of the well-behaved ISTDs): only if it lowers the
    replicate CV of biological features. Test that; if it doesn't, don't normalize.
  - **Total-signal or PQN normalization:** only if total load is uncontrolled and the
    treatment is not expected to change it. If group totals differ systematically, total
    normalization can erase real biology — report the difference instead of normalizing it
    away.
  - **Batch:** include it as a model covariate. Don't apply batch-correction algorithms
    silently.
- For the key results, show that they hold with and without the chosen normalization, or
  report both.

---

## 4. Step 1 — Write the design down before any statistic (`design.md`)

The designed comparisons must be fixed before you see any result, so the data cannot choose
them. Write `design.md` from §0, the metadata and any study description in the archive:

1. **Metadata columns, classified:**
   - identifiers;
   - design factors, assigned by the investigator (genotype, treatment, dose, time, …);
   - covariates, continuous (depth, pH, biomass, OD);
   - technical variables (batch, plate, extraction date, injection order);
   - descriptive or constant columns — report constant columns as context only.
2. **Factor decomposition.** Group labels often flatten several factors into one string
   (`WT_drought_d7`). Parse them into explicit factor columns and show the parse as a table.
   Where a parse is ambiguous, say so and keep the flat label.
3. **Levels and the reference level** of each factor (which level is the control).
4. **Replicate structure:**
   - biological replicates per cell of the design;
   - technical replicates, re-injections, pooled samples;
   - balance;
   - confounding between design factors and technical variables (cross-tabulate). A factor
     fully or mostly confounded with batch or injection order is the most common fatal flaw.
     Flag it here.
5. **The design footprint**, in one line: biological groups, organisms or materials, factor
   axes, designed contrasts, polarities, methods. It sizes the report (§8.6).
6. **Designed contrasts.** For each treatment-vs-control comparison implied by the design and
   the stated aim, give an id, a label, the treatment group(s), the control group(s) and the
   factor it tests.
   - Factorial designs: add within-level contrasts (the treatment effect inside each genotype).
   - Population and survey designs with no control: no fold changes against an arbitrary
     group. Use gradients, z-scores and ANOVA instead (§7).
   - If §0 names the comparisons, use them. Label any you add as "design-implied". Never
     replace stated comparisons with groupings invented from sample names.
7. **Story-pattern routing** (§7): the primary pattern and the secondary axes, with the
   metadata evidence that fired each.
8. **Kill-controls.** For each designed contrast (or the study as a whole), name the strongest
   alternative explanation that would make a positive result meaningless, and how the data
   test it. Examples:
   - carry-in from the medium or a reagent;
   - biomass or sample-amount differences;
   - batch or injection order aligned with the factor;
   - matrix suppression that differs between sample types;
   - an ambiguous origin (host vs microbe).

   Write these down before running Part B.

---

## 5. Part A — Know the data (every experiment, before any biology)

### A1. Analytical quality — the internal standards in the biological runs

Internal standards (ISTDs) spiked into every biological sample are the run-quality readout.
Do not use pooled QCs or standard mixes for this, even when present; they are a fallback only
(below).

**Find the ISTDs**, in this order:
1. §0.
2. A standards list or method file in the archive.
3. Metadata columns.
4. Annotation names that carry isotope labels (`13C`, `15N`, `U-13C`, `d3`…`d9`, "labeled",
   "ISTD", "IS", "internal standard") or name a known non-natural spike.
5. m/z (≤ 5 ppm) + RT, when the panel's masses are known.

Leave out unlabeled co-standards on purpose: the biology also makes them, so they are not
quality readouts.

**Compute, for each ISTD × polarity, across the biological runs that have peak data:**
- `n_bio_runs`, `n_bio_detected`, `detect_frac`, `median_area`;
- `rho_vs_injection_order` (Spearman), which measures drift;
- two CVs:
  - `cv_within_group`: the median CV across the replicates of each sample group (groups with
    ≥ 3 detections). The spike is constant, so this is injection and extraction
    reproducibility — the run-quality CV.
  - `cv_pooled`: over all biological runs. It also contains differences between sample types
    (matrix suppression). A pooled CV well above the within-group CV means a matrix effect,
    not bad runs.

**`well_behaved`** = detected in ≥ 80% of biological runs with `cv_within_group` ≤ 0.35 (the
pooled CV when fewer than two groups have replicates). You may refine the rule if you state
how. **Drift is deliberately not part of the rule.** A stable standard whose area trends with
injection order is still a good readout: the trend is a finding about the run (a gradual
sensitivity change), not a failure of the standard.

**Many ISTDs are weak or undetected in any given sample matrix, and that is normal.** Matrix
suppression, low spike levels, poor retention on the column, or an ion that never made it into
the feature table all cause it. **Two or three well-behaved ISTDs, ideally spread across the RT
range, are enough to conclude the runs are analytically sound.**
- Do not demand that every ISTD look perfect.
- Do not present undetected or noisy ISTDs as a data-quality problem.
- Do not let a partial panel lower your confidence in the data.

**What does flag a real problem:**
- ISTDs that are good elsewhere collapsing in specific runs. These are outlier injections;
  name them.
- A shared monotonic trend vs injection order across several usable ISTDs. This is drift; say
  whether the groups were randomized against it.
- A group-specific ISTD shift that lines up with a contrast. Some matrix effect between sample
  types is expected and is not a run-quality problem. It matters when it lines up with a
  contrast, because it can bias that comparison's intensities. Name the contrasts it affects.

**Fallbacks when no ISTD can be found, in order:**
1. Pooled-QC repeatability: feature RSD in the pooled QCs, how tightly the QCs sit in a
   QC-only ordination, and QC drift vs injection order. The QCs remain a readout only.
2. Internal consistency: total signal and features detected per run vs injection order, and
   replicate correlation.

Say plainly that the run-quality verdict is weaker without spiked standards.

**Deliver:** the ISTD inventory table (all rows, usable ones marked); one figure of the usable
ISTDs' areas vs injection order, coloured by sample group; and a one-line verdict. Without
injection order, say so and skip drift.

### A2. Experimental quality — samples, labels, replicates

- **Sample inventory table.** One row per biological group, with:
  - runs per polarity and biological replicates;
  - organism or material;
  - decomposed factor levels;
  - role, and control or not;
  - the metadata description, quoted.

  Flag groups with fewer than 3 biological replicates.
- **Label hygiene.** Check whether sample names, group labels, descriptions and the design
  columns agree. Look for typos and near-duplicate labels, runs of unknown type, and groups
  whose names imply a factor the design columns do not record. Report what you found and how
  you resolved it.
- **Replicate reproducibility.** Per group, give:
  - the median feature CV on detected values;
  - within-group vs between-group correlation of log intensities;
  - how consistent the detection rates are.

  Flag outlier replicates (poor correlation with their group-mates, or outlying in PCA). Flag
  them; don't silently drop them. If you exclude any, show the key results with and without
  them.
- **Summary statistics:**
  - features per polarity, as ions and as collapsed molecules;
  - features detected per run (range);
  - fraction of features detected in ≥ 50% of biological runs;
  - total intensity per run, by group;
  - fraction of features with MS/MS;
  - annotation coverage by MSI level;
  - blank-flagged fraction.
- **The normalization decision** (§3.8), with its diagnostics.
- **Balance and confounding:** restate them from `design.md` with the actual run counts.

### A3. Global structure — what separates the samples?

Use the biological runs, one polarity at a time. Keep features detected in most runs of at
least one group, log2-transform, and fill for ordination per §3.4 (labeled).

- **PCA first.** Show scores coloured by each decomposed factor, by injection order and by
  batch, plus a scree plot and the top-loading features (with identities at their MSI
  wording). This is the first global figure.
- **t-SNE / UMAP** as a supplement. State the parameters, and don't over-read distances between
  clusters.
- **Clustering.** A dendrogram of samples (correlation distance), and a clustered heatmap of the
  top-variance features with annotation bars for group and factors.
- **Networks:**
  - A feature co-abundance network across biological runs (Spearman, FDR-controlled): its
    modules, and which modules track which factor.
  - The shipped molecular network: do the co-abundance modules map onto spectral families?
    Annotate each family by its library hits. A family containing X is not X: its other members
    are relatives of X, and their Δm/z to X (Appendix A) suggests how they differ.
  - Precursor–product pairs: network edges whose Δm/z matches a transformation, where the two
    features move in opposite directions across a factor (a glycoside down and its aglycone
    up). These are strong, specific observations. Check the alternative first: the "product"
    may be an in-source fragment of the "precursor". Co-elution makes that suspect; distinct
    RTs are needed.
- **Quantitative partition:**
  - PERMANOVA on the sample distance matrix with the decomposed factors and their interactions
    (+ batch), giving R² per term. Use vegan `adonis2` if R is available; otherwise implement a
    permutation PERMANOVA and state the sums-of-squares type.
  - A per-feature n-way ANOVA: a linear model on log2 floor-substituted values with factors
    and interactions (+ batch), type II sums of squares for unbalanced designs, well-detected
    features only, BH-FDR per term.
  - Report how many features (and molecules) each factor and interaction drives: a variance
    partition of the metabolome.
  - For a single-factor design, use a one-way ANOVA / Kruskal–Wallis across groups.
- **Conclude A3 in a few sentences:**
  - which factors dominate the metabolome;
  - whether the designed factor is a major or a minor axis;
  - any unexpected structure: a replicate clustering with the wrong group, an injection-order
    axis, an interaction, a group that splits in two.

---

## 6. Part B — The study design: comparisons

### B1. The designed comparisons

Run every designed contrast in `design.md` as a per-feature differential under §3.4–3.5:

- **Statistics:** log2 fold change with its Welch 95% CI, the conservative bound, BH-FDR within
  the contrast, detection counts and pattern, the blank flag, and the molecule collapse.
- **Annotate the movers.** Join hits to the annotations. Report movers at their MSI wording,
  largest conservative effect first.
- **Class enrichment among movers.**
  - The background is the tested features that have a class assignment — not the whole table,
    and not the library.
  - Use Fisher's exact test per class, with BH-FDR.
  - Say where the classes came from: classes derived from library-hit structures are
    MSI 2-based; in-silico class predictions are MSI 3.
- **Network neighbourhoods** of confidently annotated movers. Unknown movers adjacent on a
  spectral-similarity edge are prime structural-analog candidates; give the Δm/z
  interpretation. An ion-identity edge means the same molecule, not an analog.
- **Null check.** Where a group has ≥ 6 biological replicates, split it at random into two
  halves and run the same pipeline (a placebo contrast). Its number of calls estimates the
  false-call rate per test. Report it in Methods. If the placebo produces many calls, the
  thresholds or the replicate structure are wrong; fix that before reporting.
- **Kill-controls.** Test the ones named in `design.md` and report the outcome plainly. A
  result that disappears under its kill-control is a null. Say so; don't defend it.

The story pattern routed in `design.md` sets how B1 is presented (§7).

### B2. Comparisons the data suggest

Use Part A to decide whether more pairwise comparisons belong. Typical triggers:

- a factor that explains real variance in A3 but that no designed contrast addresses (time
  points, genotypes, sites or fractions against each other);
- an interaction, which calls for within-level contrasts;
- a group that splits into two clusters;
- an unexpected similarity between groups.

For each comparison you add:

- state the Part A result that motivated it;
- run it under the same rules;
- label every result **"data-suggested (not in the designed comparisons)"**;
- apply FDR across all added comparisons, and frame what they show as hypotheses.

Add only the few best-motivated comparisons. Turn the rest into next prompts (§8.7).

---

## 7. Story patterns — how to tell Part B

A study is routed to a set of story patterns. The **primary** pattern sets the question, the
lead figure of Part B and the headline. Each **secondary** pattern becomes its own axis
section, in the order listed. Routing is a starting point: if the data contradict it (no time
structure, no usable control), say so and follow the data.

### 7.1 Routing

Route from the metadata and the stated aim. Write the routing, with the evidence that fired
each pattern, in `design.md` and in the report.

| Pattern | Fires when the factors, levels, descriptions or aim mention… |
|---|---|
| Method / resource | ring trial, reproducibility, method development, reference material, standard mix, benchmark, a dataset resource |
| Fate / transformation | decomposition, degradation, catabolism, persistence, litter, tracer, a ¹³C/¹⁵N-labeled *substrate* (not the internal standards) |
| Gene function | mutant, knockout / knockdown, transgenic, overexpression, CRISPR / edited, engineered, wild type / WT, parental strain, complemented, Δgene |
| Exchange | co-culture, synthetic community, axenic / gnotobiotic, monoculture, symbiosis — or spent medium, exudate, supernatant, filtrate, uninoculated / media control |
| Treatment response | a control group, or treatment-vs-control contrasts, with no engineered-genotype cue |
| Comparative panel | ≥ 5 organisms, strains, isolates, species, accessions or cultivars, with no shared control |
| Survey / gradient | site, location, depth, horizon, gradient, elevation, habitat, transect, plot, population — nothing assigned by the investigators |
| Dynamics | a time factor: time point, day, hour, week, stage, phase, season, harvest; a time course |
| Discovery | a stated aim to find new compounds, pathways, gene-cluster products or functions |
| Multi-omics hooks | companion genome, transcriptome, proteome or microbiome data — always a closing section, never primary |
| Profile | nothing else routed |

**Priority** — the primary is the first routed pattern in this order: method / resource →
fate / transformation → gene function → exchange → treatment response → comparative panel →
survey / gradient → dynamics → discovery → profile. (A method paper with genotypes is still a
method paper; a substrate-fate study with mutants is still a fate story; a transgenic exudate
study is a gene-function story told through exchange.)

**Routing traps:**
- An exudate, spent medium or supernatant as the *sample material* alone does not make an
  exchange story. Route exchange as an axis, and let it lead only when nothing else routed.
  Organisms grown together (co-culture, synthetic community, host + microbe vs axenic) do lead.
- A bare "genotype" often means natural accessions or cultivars → comparative panel. Gene
  function needs engineered or mutant lines vs wild type or the parent.
- "Labeled" or "isotope" usually refers to the internal standards. Fate needs a labeled
  substrate.
- Inoculation or infection alone is a treatment, not an exchange design.

### 7.2 The patterns

#### Method / resource — what the dataset or method delivers, and how reproducibly
- **When:** method development, standards, reference material, a ring trial or a dataset
  resource.
- **Question:** What does this dataset or method cover, how reproducible is it, and what can
  others do with it?
- **Reference:** replicates, QC pools, sites or labs, and known positives — reproducibility is
  the comparison.
- **Lead:** Open with the gap the resource or method fills and who would use it. Say which of
  three kinds it is, because they are written differently:
  - a dataset resource — lead with coverage;
  - a method or device — lead with what it measures and its validation, then one worked
    biological example;
  - a reproducibility benchmark across sites or labs — lead with how much variation is
    technical vs biological.
- **Lead figure:** coverage overview — samples × polarities × chromatography, with features and
  annotations (by MSI level) per sample type.
- **Results, in order:**
  1. Coverage: features, annotations by MSI level, compound classes represented.
  2. Reproducibility: replicate and QC agreement, drift over injection order.
  3. Recovery of known positives or expected compounds.
  4. One worked example of use.
  5. Limits: what the method does not see (polarity range, mass range, classes absent).
- **Headline form:** The resource covers N samples and K features (M annotated); replicate CV is
  X; known positives are recovered at rate R.
- **Statistics:** CVs, replicate correlations and detection rates; across-site or across-lab
  agreement for ring trials.
- **As a secondary axis:** a short reproducibility section (replicate and QC agreement) before
  the biology.
- **Avoid:** writing it as a biology story; burying the QC instead of leading with it;
  overstating coverage by counting features as identifications.
- **Overrides a default:** here QC and standard-mix runs are the subject of the reproducibility
  claims, so analyze them as data (§3.6 still applies to any biology).

#### Fate / transformation — what happens to a substrate
- **When:** decomposition, degradation, catabolism or persistence of an added substrate or
  material, or tracing of an isotope-labeled substrate.
- **Question:** How fast, and into what, is the substrate transformed, and what persists?
- **Reference:** the substrate at time zero, and an abiotic, sterile or killed control.
- **Lead:** Open with the substrate and the transformation question.
- **Lead figure:** substrate depletion alongside the appearance of products, over time or across
  conditions.
- **Results, in order:**
  1. Substrate loss, and whether the abiotic control loses it too.
  2. Transformation products: features that appear as the substrate declines, with their mass
     differences to the substrate (network edges).
  3. The persistent fraction: what remains untransformed.
  4. Isotope incorporation, when a labeled substrate was used.
  5. Differences in fate across organisms or conditions.
- **Headline form:** Substrate S declines X-fold by time T while products P1–P3 appear; fraction
  F persists.
- **Statistics:** depletion curves; precursor–product mass-difference edges; every loss compared
  with the abiotic control.
- **As a secondary axis:** for each level of the primary axis, state whether the substrate's fate
  differs.
- **Avoid:** calling disappearance degradation without products or an abiotic control; ignoring
  sorption and extraction losses; treating a persisting signal as unchanged without checking
  for isomers.

#### Gene function — the metabolome as the phenotype of a genotype
- **When:** mutant, knockout, transgenic, edited or engineered lines compared with wild type or
  the parent.
- **Question:** What does changing this gene do to the metabolome, starting with the compounds
  the gene should touch directly?
- **Reference:** the wild-type or parental line, grown and sampled under the same conditions.
- **Lead:** Name the gene or pathway and what its loss or gain should do: which substrates
  should accumulate and which products should disappear.
- **Lead figure:** the expected direct consequence — the pathway around the edited gene, with
  the measured substrate(s) and product(s) shown per genotype (detection rate and level).
- **Results, in order:**
  1. The positive control: do the expected substrate and product changes appear? If the
     expected compounds are not detectable by this method, say so explicitly.
  2. Collateral changes outside the edited pathway, grouped by class.
  3. Unknowns that track genotype — above all features present only in wild type or only in the
     mutant (candidate pathway products).
  4. Consistency across independent lines or alleles, when there is more than one.
- **Headline form:** Loss of gene G removes product P and accumulates substrate S; beyond the
  pathway it also shifts class Y.
- **Statistics:** mutant ÷ wild type per contrast with conservative bounds; features present in
  one genotype only, reported with detection rates (often the strongest evidence).
- **As a secondary axis:** state the genotype effect within each treatment or time point.
  Combined with a treatment, the question is whether the mutant loses, keeps or inverts the
  wild-type response.
- **Avoid:** skipping the expected compounds and going straight to untargeted hits; reading
  every pleiotropic change as a direct effect of the gene; comparing a mutant in one condition
  with wild type in another.

#### Exchange — what organisms take up, release and pass between each other
- **When:** spent medium, exudates or supernatants, or organisms grown together compared with
  the same organisms alone (co-culture, community, host with microbe).
- **Question:** Which compounds does each organism consume from or release into its
  surroundings, and what changes when organisms meet?
- **Reference:** the uninoculated (sterile) medium for uptake and release; the monocultures or
  the axenic host for interaction effects.
- **Lead:** Open with the setting (the medium or the host environment) and the exchange
  question: who consumes what, and who releases what.
- **Lead figure:** an uptake / release map — features × organism (or condition), each cell
  marked depleted, released or unchanged relative to the uninoculated medium.
- **Results, in order:**
  1. Depletion: which medium components each organism consumes, and how completely.
  2. Release: which compounds appear that were absent from the medium.
  3. Overlap and partitioning between organisms: shared vs organism-specific uptake (niche
     overlap).
  4. Interaction effects: features found with partners together but in neither alone
     (emergent), or suppressed by the partner.
  5. Candidate cross-feeding: released by one organism, depleted by another.
- **Headline form:** Organism A depletes S1–S3 and releases P; in co-culture P is consumed by B.
- **Statistics:** each condition ÷ uninoculated medium for depletion and release (a depleted
  feature is detected in the medium and lower or absent after growth); co-culture against each
  monoculture for interactions.
- **As a secondary axis:** report uptake and release per level of the primary axis (per
  genotype, per treatment): does the perturbation change what is consumed or released?
- **Avoid:** dropping the medium control as a blank, which makes "released" and "already in the
  medium" indistinguishable; calling a compound produced when it was only not consumed;
  inferring cross-feeding from co-occurrence alone.
- **Overrides a default:** the uninoculated / sterile-medium runs ARE the reference for this
  pattern. They are often typed as blanks or extraction controls, which §3.6 excludes from
  biology. Here, use them as the baseline for depletion and release, and say that you did.
  Solvent and injection blanks remain exclusions.

#### Treatment response — what did the perturbation change?
- **When:** an applied perturbation (chemical, nutrient, hormone, stress, temperature, drought,
  light, pathogen) compared with untreated or ambient samples.
- **Question:** Which metabolites does the treatment change, in which direction, and how
  consistently?
- **Reference:** the untreated, mock or ambient group — every effect is treated ÷ control.
- **Lead:** Open with the perturbation and the response you would expect from it; the data then
  confirm, refine or contradict that expectation.
- **Lead figure:** the size and direction of the response per contrast — up and down feature
  counts, with detected-in-both and present-vs-absent kept separate. An ordination of treated
  vs control runs is a sanity check, not the result.
- **Results, in order:**
  1. Size and direction of the overall response, per contrast (how many features move up or
     down, how many appear or vanish).
  2. Which compound classes carry the response (class enrichment among movers).
  3. Named compounds that moved, at their MSI wording, largest conservative effect first.
  4. Dose or level consistency when several levels exist: does the effect scale or saturate?
  5. Unknown movers, each with a full entry (§8.6).
  6. What did not change that would have been expected to.
- **Headline form:** Treatment X raises (or lowers) class Y — N features, with named examples A
  and B at MSI 1–2 — relative to the untreated control.
- **Statistics:** per-contrast differential (§3.4–3.5): log2 fold change with its conservative
  bound, detection counts for present-vs-absent features, and volcano or ranked-effect plots per
  contrast.
- **As a secondary axis:** report the treatment effect within each level of the other axes (per
  genotype, per time point, per site), then say whether it holds across them.
- **Avoid:**
  - leading with a PCA and calling the separation the result;
  - pooling contrasts that have different controls;
  - with a pathogen, parasite or symbiont as the treatment, not separating compounds the host
    makes from compounds the agent makes (use the agent grown alone, when it was run);
  - stating a mechanism as a finding — a changed metabolite is an observation, the pathway
    behind it is interpretation.

#### Comparative panel — chemodiversity across many organisms
- **When:** many strains, species, isolates, accessions or cultivars profiled side by side, with
  no shared control.
- **Question:** What chemistry is shared across the panel, what is specific to which members,
  and does it follow relatedness?
- **Reference:** the panel itself — core (every member) vs accessory vs unique features. No
  member is the control.
- **Lead:** Open with what the panel spans (taxonomic breadth, ecological roles) and the
  comparative question.
- **Lead figure:** a feature presence / abundance heatmap with members ordered by relatedness or
  ecological group, plus each member's count of unique features.
- **Results, in order:**
  1. Core, accessory and unique metabolome sizes.
  2. A per-member summary table in which every member appears.
  3. Members that stand out (largest unique sets, unusual classes) — prose only for these.
  4. Whether chemical similarity follows relatedness or ecology.
  5. Unique unknowns, the strongest candidates for new chemistry, each with a full entry.
- **Headline form:** Of N features, K are core; member M carries the largest unique set,
  dominated by class Y; chemical similarity does (or does not) follow phylogeny.
- **Statistics:** detection matrices and clustering; similarity between members (Jaccard or
  cosine); comparison with relatedness when it is known. No pairwise fold changes against an
  arbitrary reference member.
- **As a secondary axis:** report the primary effect per member in one table, then name the
  members that deviate.
- **Avoid:** prose for every member (use the table); choosing one member as a pseudo-control;
  equating "not detected" with "cannot make it" (detection limit, extraction).

#### Survey / gradient — what drives variation across natural samples?
- **When:** natural samples across sites, habitats, populations, depths or gradients, with
  nothing assigned by the investigators.
- **Question:** How does the metabolome vary across the sites or the gradient, and which
  compounds track which environmental axis?
- **Reference:** no control group exists. Compare along the gradient and against the overall
  mean (population z-score); never designate a site as the control.
- **Lead:** Open with the environmental axis or the set of sites, and why they should differ
  chemically.
- **Lead figure:** an ordination coloured by site or gradient position, with the share of
  variation each axis explains, plus the features most correlated with the gradient.
- **Results, in order:**
  1. How much of the variation the site or gradient explains.
  2. Features and classes that change monotonically along the gradient.
  3. Site-specific markers (detected at one site or stratum only).
  4. The shared core (detected everywhere).
  5. Within-site variability compared with between-site differences.
- **Headline form:** Class Y increases with depth (N features, monotone), while a site-specific
  set marks site Z.
- **Statistics:** population z-scores; correlation or rank tests along gradient position;
  variance partitioning across axes. No fold changes against an arbitrary reference site.
- **As a secondary axis:** report the primary effect within each site or stratum, and say
  whether its direction holds across the gradient.
- **Avoid:** inventing a control site; causal language for correlational structure; forgetting
  that sites differ in many confounded ways at once.

#### Dynamics — trajectories over time or growth phase
- **When:** the same system sampled at several times, stages or growth phases.
- **Question:** How do metabolite levels change over time — what accumulates, what is consumed,
  what is transient, and in what order?
- **Reference:** the earliest time point, or the matched control at each time point when there
  is a treatment axis.
- **Lead:** Open with the process the time axis follows (growth, development, recovery,
  decomposition) and the phase transitions that matter.
- **Lead figure:** temporal profiles — features clustered by trajectory shape (rising, falling,
  transient, flat), with representative named compounds per cluster.
- **Results, in order:**
  1. Overall trajectory: in which interval does the metabolome change most?
  2. Trajectory classes, and the compound classes that dominate each.
  3. Order of events: what changes first, and what follows.
  4. Transient features that an endpoint comparison would miss.
  5. With a treatment axis: when the treatment effect first appears, and whether it persists.
- **Headline form:** Class Y accumulates through phase P while substrate S is depleted by time T;
  feature F is transient.
- **Statistics:** per-time-point contrasts against the baseline or the matched control, plus
  trajectory clustering. Time goes on the x-axis — never collapse to the endpoint.
- **As a secondary axis:** give each effect of the primary pattern per time point, and say when
  it emerges, peaks or fades.
- **Avoid:** reporting only the last time point; treating time points as unrelated groups when
  they form one trajectory; reading a growth (biomass) effect as metabolic regulation — say
  whether levels are normalized.

#### Discovery — characterizing the unknown
- **When:** identifying new compounds, pathways or functions is a stated aim.
- **Question:** Which unidentified features are most worth characterizing, and what can the data
  say about their structure?
- **Reference:** the identified background — what each unknown is not, and which known compounds
  it neighbours.
- **Lead:** Open with why new chemistry is expected here (the organism, the condition, a gene
  cluster).
- **Lead figure:** an evidence panel for the top unknown(s) — MS1 and formula, MS/MS with
  annotated fragments and losses, network neighbours with their identities, and the detection
  pattern across groups.
- **Results, in order:**
  1. How unknowns were prioritized (intensity, group specificity, differential behaviour).
  2. Each prioritized unknown as a full entry: m/z, RT, formula, diagnostic fragments and
     losses, neighbour class, detection pattern.
  3. Molecular families: unknowns clustered with identified neighbours.
  4. Candidates excluded, and why.
- **Headline form:** Feature F (formula, MSI 3) is specific to condition C and sits in a family
  with known class Y — a candidate new Y.
- **Statistics:** specificity and detection rates rather than fold changes; network edges as
  structural evidence, labeled by edge type.
- **As a secondary axis:** within the primary pattern, give the strongest unknown movers full
  entries in a subsection of their own.
- **Avoid:** counting unknowns instead of describing them; naming a structure beyond its MSI
  level; treating membership in a network family as an identification.

#### Multi-omics hooks — make the metabolite results joinable (closing section only)
- **When:** the metabolite data are meant to be combined with genome, transcript, protein or
  microbiome data.
- **Question:** Which metabolite results could be linked to genes, transcripts or taxa — without
  claiming the link from this archive alone?
- **Cover:**
  - Changed compounds organized by pathway or class, so they can be joined to gene or
    transcript data.
  - Compounds whose biosynthesis or degradation genes are known: the candidates for a joint
    analysis.
- **As a closing section:** list the observations ready for integration, and what companion
  data would test each.
- **Avoid:** asserting gene–metabolite or taxon–metabolite links that only the companion data
  could show.

#### Profile — what is in these samples
- **When:** no comparison spine is evident in the design.
- **Question:** What does the metabolome of these samples contain, and what stands out?
- **Reference:** detection across replicates; group differences only where groups exist.
- **Lead:** Open with what the samples are and why their chemistry is of interest.
- **Lead figure:** a composition overview — classes by feature count and intensity, per sample
  group.
- **Results, in order:**
  1. Dominant classes and the most intense annotated compounds.
  2. Group differences, descriptively, where groups exist.
  3. Notable unknowns, each with a full entry.
  4. Reproducibility across replicates.
- **Headline form:** These samples are dominated by class Y; notable unknowns include F.
- **Statistics:** detection rates and intensity distributions per group.
- **Avoid:** inventing contrasts the design does not have.

### 7.3 Where patterns meet

When both patterns of a pair are routed, add this instruction:

- **Gene function × treatment response:** the result is the interaction. Report the wild-type
  response first, then whether the mutant loses, keeps or inverts it.
- **Treatment response × dynamics:** report when the treatment effect first appears, when it
  peaks and whether it persists, from per-time-point contrasts against the matched control —
  not from the pooled time course.
- **Exchange × comparative panel:** one per-member depletion / release table, then niche overlap
  — which members compete for the same substrates.
- **Exchange × dynamics:** a depletion curve per medium component and a release curve per
  product; the order of consumption is the result.
- **Gene function × exchange:** compare the uptake / release profiles of mutant and wild type,
  each against the uninoculated medium. When the genotype is a host's and the exchange is with
  microbes, the metabolites are the proposed link between gene and microbial outcome: report
  the link the data show (genotype → compounds) and name the rest of the chain as
  interpretation.
- **Survey / gradient × dynamics:** separate the site or gradient effect from the time or season
  effect, then ask whether site differences hold at every time.
- **Treatment response × survey / gradient:** the treatment effect per site or stratum first,
  then its consistency across the gradient.
- **Comparative panel × treatment response:** each member's response to the treatment in one
  table; which members respond, and whether the response follows relatedness.
- **Fate / transformation × comparative panel:** which members transform the substrate, into
  which products, and how completely — one table, prose for the outliers.

### 7.4 Close every write-up with the candidates for validation

List the few compounds or unknowns that carry the story, each with the test that would confirm
what you claim:
- identity: an authentic standard at the same RT and MS/MS;
- effect: an exogenous addition or a bioassay;
- origin: a labeled substrate or a sterile control.

Nominate; do not claim the test's outcome.

---

## 8. Part C — Output format

### 8.1 Directory layout

Write everything under one output directory (default `analysis_output/` next to the unpacked
archive, never inside it):

```
analysis_output/
  report.html        THE deliverable: self-contained, figures embedded inline
  report.pdf         the same content as PDF
  data_map.md        Step 0: archive inventory, software, joins, units, run types
  design.md          Step 1: factors, replicate unit, contrasts, routing, kill-controls
                     (written BEFORE any statistic)
  decisions.md       every judgment call — thresholds, exclusions, mappings,
                     normalization — with its reason
  results/           machine-readable results with the fixed schemas in §8.3
  tables/            every table in the report as CSV, numbered to match (T01_…, T02_…)
  figures/           every figure as PNG (≥ 150 dpi) + SVG/PDF, numbered (F01_…, F02_…)
  scripts/           the code that produced everything, runnable in order
                     (01_map.py, 02_design.py, …), archive path as an argument,
                     random seeds fixed
  environment.txt    software and package versions
```

### 8.2 The report — sections in this order

0. **Header:** experiment name, date, the input archive file name and its SHA-256, the name
   and version of this analysis plan (from the front matter at the top of this file), and this
   disclaimer: *"Generated by an AI agent from the supplied archive. All identifications and
   statistics are machine-produced hypotheses for expert review. Identifications carry MSI
   levels; none is confirmed fact."*
1. **Summary** (5–8 sentences): what the experiment is, the data-quality verdict, the dominant
   global structure, the key designed-comparison results, and what to do next.
2. **The data and the design** (short): what the archive held and which software produced it,
   the run-type counts, the design table, the designed contrasts, the story-pattern routing with
   its evidence, the kill-controls, and what was missing. Full detail stays in `data_map.md`
   and `design.md`.
3. **Analytical quality** (A1).
4. **Experimental quality** (A2).
5. **Global structure** (A3).
6. **Designed comparisons** (B1), following the story pattern (§7).
7. **Data-suggested comparisons** (B2).
8. **Candidates for validation** (§7.4).
9. **Limitations:** what these data cannot say (e.g. no in-house standards → no MSI 1; no
   injection order → drift untested), kill-controls that could not be tested, and any
   confounding.
10. **Suggested next prompts** (§8.7).
11. **Methods and provenance:** software and versions, every parameter and threshold, the
    filtering, floor-substitution and ordination-fill statements, the normalization decision,
    exclusions with reasons, the placebo result, input-file checksums, random seeds, and a
    statement of AI use.

**Finish with ONE integrated report.** Assemble all text, tables and figures into a single
self-contained `report.html`, with figures embedded inline (base64), numbered figures and
tables, and captions per §8.4. Render the same content to `report.pdf` with whatever is
available (weasyprint, a headless browser, or matplotlib + PdfPages). If PDF conversion fails,
say so and still deliver the HTML. Intermediate files can stay, but a non-expert reader should
only need to open the report. End your final message by pointing to `report.html` and
`report.pdf`.

### 8.3 Results files — fixed schemas

Use these names and columns every time, so results from different projects can be compared and
reloaded. Add columns at the end if needed; never rename these.

**`results/feature_annotations.csv`** — one row per feature:
`feature_id, polarity, mz, rt_min, molecule_id, ion_role, parent_feature_id, adduct,
label` (the name as it must be written, per §3.1)`, msi_level, msi_basis, candidate_set,
library_score, matched_peaks, ppm_error, library_source, inchikey, smiles, formula,
formula_source, class, class_source, network_component, detect_frac_bio, blank_ratio,
blank_flag, contaminant_flag, notes`

**`results/differential_results.csv`** — one row per feature × contrast:
`contrast_id, contrast_origin` (designed | design-implied | data-suggested)`, feature_id,
polarity, mz, rt_min, molecule_id, ion_role, n_a, k_a, n_b, k_b, detection_pattern,
floor_substituted_a, floor_substituted_b, mean_log2_a, mean_log2_b, log2fc, ci95_low,
ci95_high, log2fc_conservative, t, df, p, q, call` (up | down | ns | not_tested)`, blank_flag,
label, msi_level, class, network_component`

**`results/anova_terms.csv`** — per feature × model term:
`feature_id, polarity, term, df, F, p, q, partial_eta2`

**`results/permanova.csv`** — per polarity × term: `polarity, term, df, R2, F, p,
n_permutations`

**`results/istd_inventory.csv`** — per ISTD × polarity:
`istd_name, polarity, kind` (heavy_labeled | spiked_nonnatural)`, feature_id, mz, rt_min,
match_basis, n_bio_runs, n_bio_detected, detect_frac, median_area, cv_within_group,
n_groups_for_cv, cv_pooled, rho_vs_injection_order, well_behaved, note`

**`results/sample_inventory.csv`** — per run:
`run_id, run_type, sample_group, <one column per decomposed factor>, biological_replicate_id,
injection_order, batch, polarity, n_features_detected, total_intensity, outlier_flag,
excluded, exclusion_reason`

**`results/network_node_stats.csv`** — one row per network node, wide over contrasts:
`node_id` (the network's own node key)`, feature_id, label, msi_level, class, ion_role`, then
per contrast `log2fc__<contrast_id>, cons__<contrast_id>, q__<contrast_id>,
call__<contrast_id>`. This lets the reader paint any comparison on the network themselves. In
Cytoscape: File → Import → Table from File, key column = the node id. Do not pre-select one
comparison for them.

**`results/candidates_for_validation.csv`** — one row per nominated compound:
`rank, feature_id, polarity, mz, rt_min, label, msi_level, supporting_result` (figure / table
reference)`, claim_to_test` (identity | effect | origin)`, proposed_test,
expected_outcome_if_true`

### 8.4 Figures and tables

- Number every figure and table (Figure 1…, Table 1…) in the order cited. Save each to
  `figures/` / `tables/` with the matching number.
- **Captions** say what is plotted and what to take from it:
  - name each axis, with units;
  - define every line, band, colour, threshold and marker;
  - give n;
  - **name the comparator or control in the caption itself**, not only in the text — a figure
    is read on its own.

  When the control is baked into how a set was defined ("present in treatment, absent from
  control"), plot the control's own distribution as a second series so the reader sees the
  comparison.
- Keep group colours consistent across all figures. Use colour-blind-safe palettes.
- Label features by their §3.1 wording (e.g. "putative X (MSI 2)"), never by a bare name.
- **Minimum figure set:**
  - usable ISTD areas vs injection order (A1);
  - PCA scores by factor, plus the scree plot (A3);
  - clustered heatmap (A3);
  - variance partition: features per ANOVA term + PERMANOVA R² (A3);
  - per designed contrast, a volcano plot (x = log2 FC, y = −log10 q; calls highlighted by the
    conservative-bound rule; present-vs-absent features marked distinctly) or a ranked
    conservative-effect plot (B1);
  - class enrichment (B1);
  - the story pattern's lead figure (§7);
  - a network-family panel for the key result;
  - mirror plots for headline identifications when spectra are available.
- **Minimum table set:** sample inventory; ISTD inventory; design and contrasts; the top movers
  per contrast; candidates for validation. Long tables show the top rows in the report and link
  the full CSV.

### 8.5 Writing rules

- **Observation first, then interpretation — labeled and brief.** State what was measured. Put
  any interpretation in a sentence that begins "Interpretation:" and keep it short. Mechanistic
  speculation and literature reviews are not the job.
- **Every number traces to a file.** Every number in the text must appear in a CSV under
  `tables/` or `results/`. A number in no file is either a fabrication or a computation you
  forgot to save; fix one or the other.
- **Effects come with uncertainty.** Give n, the test, the CI or conservative bound, and q.
  Never give a bare mean.
- **Every compound name carries its MSI wording** on first mention in each section.
- **Define every codename, abbreviation and group label on first use.** Use one name per entity
  throughout.
- **No vague intensifiers** ("dramatic", "substantial", "striking", "notably", "importantly"):
  give the number.
- **Keep sentences to ~35 words or fewer.**
- **Calibrate headline numbers.** Say whether each is good or bad against a stated baseline:
  the placebo false-call rate, a typical replicate CV, the fraction expected by chance.
- **Name the domain confounds** a specialist would raise (carry-in, biomass, batch, matrix,
  host vs microbe origin). For each, give the control that addresses it, or list it under
  Limitations.
- **Don't headline nulls.** A result that disappears under its kill-control is reported as a
  null, plainly. Documenting what did not pan out is good science; presenting it as a finding
  is not.

### 8.6 Reporting scope — completeness over brevity for observations

Concise writing is good; omitting observations is not. **Scale the length of the description to
the design footprint (`design.md`); keep the interpretation budget small either way.**

- **Cover every axis of the design.** Each factor, each level, and each organism or group that
  was measured gets its observed outcome stated: which features and classes moved, which didn't,
  and the detection patterns. A multi-axis design (several factors, mixed materials or
  fractions, many organisms) needs prose per axis and per crossing that matters. Do not collapse
  axes to save space.
- **Screen-like designs stay compact.** One factor applied across many parallel arms (e.g. a
  depletion screen over an isolate panel) is best reported as a per-arm summary table, with
  prose only for the arms that stand out. Compact is not incomplete: every arm appears in the
  table.
- **Unknowns get full entries, not a count.** Each intense or differential MSI 3/4 feature gets a
  short descriptive entry: m/z, RT, formula (if any), diagnostic fragments or losses, neighbour
  class in the network, and the detection pattern across groups. "N unknown features changed" is
  never a sufficient section.
- **What the extra space is NOT for:** mechanistic speculation, literature reviews, or repeating
  the confidence rules. Describe fully; interpret briefly, and label the interpretation.

### 8.7 Suggested next prompts

Give 3–6 concrete prompts a reader could paste into an AI co-scientist next. Tie each to a
specific figure, table or result in this report, and order them by expected value. Example:

> "Test whether the genotype × drought interaction in Figure 6 holds for the flavonoid module
> (Table 9): fit the per-feature model within the module only, report the interaction term's
> conservative effect per feature, and show the wild-type vs mutant response side by side."

This section is how the reader decides where to go next. Make it specific: name the data, the
analysis and the expected output. The comparisons you chose not to add in B2 belong here.

---

## 9. Before you hand over — self-check

- [ ] `data_map.md` exists. Every quant column maps to exactly one metadata row, or the
      exceptions are listed.
- [ ] RT units, polarity and the missing-value / gap-fill decision are stated.
- [ ] `design.md` was written before any statistic: contrasts, replicate unit, confounding,
      routing with its evidence, kill-controls.
- [ ] No QC, blank or standard-mix run entered biological statistics (except as the media
      reference in the exchange pattern, or as the subject of a method / resource study —
      each stated).
- [ ] No zeros in log space. Floor substitution is stated. Detection counts sit next to every
      effect.
- [ ] Calls use the conservative bound + BH-FDR; technical replicates were averaged.
- [ ] Adducts, isotopes and in-source fragments are collapsed for counts and headlines.
- [ ] Every compound mention carries its MSI wording. No level was upgraded, and no
      identification was ignored.
- [ ] The ISTD verdict is one line. A partial panel is not presented as a problem.
- [ ] Every designed contrast was run. Data-suggested ones are labeled and FDR-corrected
      together.
- [ ] Kill-controls are tested or listed under Limitations. The placebo contrast is reported
      where possible.
- [ ] Every number in the text is in a CSV. Every figure has a full caption that names its
      comparator.
- [ ] Unknown movers have full entries. Every design axis is covered.
- [ ] `results/` files follow §8.3 exactly. `scripts/` reproduces them.
- [ ] `report.html` is self-contained, `report.pdf` exists (or its failure is stated), and the
      report ends with the next prompts.

---

## Appendix A. Mass differences

**Co-eluting ions of the same molecule** (Δ = higher m/z − lower m/z, singly charged):

| Relationship | Δ (Da) |
|---|---|
| ¹³C isotope (M+1) | 1.00335 |
| ³⁴S isotope | 1.99580 |
| ³⁷Cl isotope | 1.99705 |
| ⁸¹Br isotope | 1.99795 |
| [M+NH4]+ vs [M+H]+ | 17.02655 |
| [M+H]+ vs [M+H−H2O]+ | 18.01056 |
| [M+Na]+ vs [M+H]+ | 21.98194 |
| [M+K]+ vs [M+H]+ | 37.95588 |
| [M+Cl]− vs [M−H]− | 35.97668 |
| [M+HCOO]− vs [M−H]− | 46.00548 |
| [M+CH3COO]− vs [M−H]− | 60.02113 |
| [M−H]− vs [M+Na−2H]− | 21.98194 |
| sodium formate cluster spacing | 67.98742 |
| dimer: [2M+H]+ = 2·[M+H]+ − 1.00728; [2M−H]− = 2·[M−H]− + 1.00728 | — |
| doubly charged ion: isotope spacing 0.50168 | — |

**Common in-source losses / transformations / structural differences** (on network edges or
between co-eluting ions; same Δ, different meaning — RT and correlation decide):

| Δ (Da) | Formula | Typical meaning |
|---|---|---|
| 2.01565 | H2 | desaturation / reduction |
| 14.01565 | CH2 | methylation; homolog series |
| 15.99491 | O | hydroxylation / oxidation |
| 17.02655 | NH3 | ammonia loss; amination |
| 18.01056 | H2O | water loss; dehydration |
| 27.99491 | CO | carbonyl loss |
| 28.03130 | C2H4 | ethyl / homolog |
| 42.01056 | C2H2O | acetylation |
| 43.98983 | CO2 | decarboxylation |
| 46.00548 | CH2O2 | formic acid loss |
| 57.02146 | C2H3NO | glycine conjugation |
| 79.95682 | SO3 | sulfation |
| 79.96633 | HPO3 | phosphorylation |
| 86.00039 | C3H2O3 | malonylation |
| 132.04226 | C5H8O4 | pentose |
| 146.03678 | C9H6O2 | p-coumaroyl |
| 146.05791 | C6H10O4 | deoxyhexose (e.g. rhamnose) |
| 162.03169 | C9H6O3 | caffeoyl — **not** a hexose (Δ 0.021 Da; resolvable at high resolution) |
| 162.05282 | C6H10O5 | hexose |
| 176.03209 | C6H8O6 | glucuronic acid |
| 176.04734 | C10H8O3 | feruloyl — **not** glucuronic acid (Δ 0.015 Da) |

## Appendix B. Common background ions — check against blanks before any biological claim

| Background | Signature |
|---|---|
| Polyethylene glycol (PEG), detergents (Triton, Tween) | ladders spaced 44.02621 (C2H4O) |
| Polypropylene glycol | ladders spaced 58.04186 (C3H6O) |
| Polysiloxanes | ladders spaced 74.01879 (C2H6OSi) |
| Phthalate plasticizers | fragment m/z 149.0233 (C8H5O3+); DEHP [M+H]+ 391.2843, [M+Na]+ 413.2662; dibutyl phthalate [M+H]+ 279.1591 |
| Slip agents (fatty acid amides) | oleamide [M+H]+ 282.2791; erucamide [M+H]+ 338.3417 |
| Anionic surfactants (negative mode) | docusate [M−H]− 421.2265; dodecyl sulfate [M−H]− 265.1479; C12 alkylbenzenesulfonate [M−H]− 325.1843 (homologs ±14.01565) |
| Mobile-phase additives and clusters | formate / acetate / ammonium clusters; medronic acid [M−H]− 174.9567 (a HILIC additive) |

A background ion is information, not just noise. Classify each: in the blanks, ubiquitous
across sample types, a known contaminant class, or a degenerate ion of a real compound
(§3.3). Then decide what it can and cannot support.
