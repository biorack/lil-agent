# Using LBL-hosted models on CBorg

CBorg (`https://api.cborg.lbl.gov`) is LBL's LiteLLM gateway. It serves
OpenAI-style `/v1/chat/completions` and Anthropic-style `/v1/messages`. The
`lbl/*` models run on LBL hardware (`ext1`/`ext2.cborg.lbl.gov`) and cost
nothing. There are no nodes to allocate, no model load wait, and no tunnel.

Key: `~/secrets/cborg-api-key.txt` (or `$CBORG_API_KEY`).

## Commands

```bash
lil-agent models                            # LBL-hosted models (--all for all ~1500)
lil-agent models --probe lbl/gemma-4        # quick reply + one tool call, per matching model
cborg/claude-cborg                          # Claude Code driven by an LBL model (default GLM)
CBORG_MODEL=lbl/gemma-4 cborg/claude-cborg -p "..." --allowedTools Read
```

## Which model is behind each alias

CBorg changes the models behind its aliases often. Each entry in CBorg's
model list (`/model/info`, also shown at https://cborg.lbl.gov/models/)
has a label, `display_name`, that names the current model: for example
"CBorg Coder Ultra - Mistral Large 4" or "CBorg Coder Max - GLM 5.3
Flash". `lil-agent models` shows it.

- Models count as LBL-hosted when CBorg serves them from
  `*.cborg.lbl.gov`. Their names don't always start with `lbl/`: for
  example, `cborg-coder-ultra-medium` and `mistral-large-4` are LBL-hosted.
- Every lil-agent run and resume records the model's provenance in
  `.lil/run.json`, under `model_provenance`: CBorg's label, context size
  and backend, a hash of CBorg's entry, the model's self-report, and what
  [cborg_models.json](cborg_models.json) says.
- `cborg_models.json` keeps what the live label can't: which model each
  backend ran, and when. CBorg switches models without notice. lil-agent
  warns at the start of a run when CBorg's label or context size no longer
  matches the record, which is how a switch shows up. Add a history entry
  when that happens.

As of 2026-11-04:
- `cborg-coder-ultra` (and its `-low` … `-max` reasoning-effort variants,
  and `mistral-large-4`) is Mistral Large 4.
- `cborg-coder-max` is GLM 5.3 Flash with a 229k context. The
  2026-10-30 tests below used an earlier GLM deployment of the same alias:
  917k context, served from `ext2`, release unknown.

The `-compact`, `-compressed` and `-flex` suffixes are proxy variants of the
same models.

## Model comparison (2026-10-30)

These tests used a small neutral-loss tool-calling agent (not part of this
repository), which gave the model two mass-difference lookup tools. The
example was quercetin-3-O-glucoside [M+H]+ with 3 fragments.

| Model | Tool calls | Time | Chemistry |
|---|---|---|---|
| `lbl/cborg-coder-max` (GLM) | 3 | 11 s | Best. Named the aglycone as protonated quercetin (303.0499). Flagged the "formic acid" match as implausible for quercetin and read it as H₂O + CO. Checked the composite gaps. |
| `lbl/cborg-deepthought` | 1 | 6 s | Quercetin hexoside, isoquercitrin. Took formic acid at face value. |
| `lbl/cborg-coder` | 1 | 6 s | Flavonoid glycoside; did not name quercetin. |
| `lbl/gemma-4-thinking` | 1 | 3 s | Correct losses; vague ("flavonoids or hydroxycinnamic acids"). |
| `lbl/gpt-oss-120b-high` | 1 | 9 s | Correct losses. Read formic acid as a carboxylic acid. Suggested a quinine glucoside, which is chemically off. |
| DeepSeek V4 Flash on Perlmutter (2026-10-29) | 3 | 22 s | Correct losses. Took formic acid at face value. |

A second, harder test used kaempferol-3-O-rutinoside: [M+H]+ 595.1657,
fragments 449.1078, 287.0550 and 153.0182. Rutinose (308.1107) is not in
the loss table, so the model has to assemble it from deoxyhexose + hexose.

| Model | Time | Chemistry |
|---|---|---|
| `lbl/cborg-coder-max` (GLM) | 22 s | Excellent. Kaempferol rutinoside (nicotiflorin) as the lead candidate. Flagged the isobaric luteolin. Identified 153.0182 as the C₇H₅O₄⁺ retro-Diels–Alder fragment. Used the loss order to argue for a terminal rhamnose. Listed what the data can't resolve. |
| `lbl/gemma-4` | 3 s | Got the losses and "rutinoside or similar". Passed a wrong mass it had computed itself to the tool (134.0032; the true gap is 134.0368). Said kaempferol **or quercetin** fits 287, but quercetin's aglycone is 303. |

GLM became lil-agent's default model on 2026-10-30.

## Claude Code on CBorg

`cborg/claude-cborg` sets `ANTHROPIC_BASE_URL` to CBorg and points every
model role at one `lbl/*` model. Tested with GLM: it used Read/Grep
correctly and answered correctly (2–3 turns, 5–8 s).

The wrapper has to work around four things:

- **Vertex and Bedrock.** A Claude Code configured for Vertex
  (`CLAUDE_CODE_USE_VERTEX`) or Bedrock ignores `ANTHROPIC_BASE_URL` and
  rejects the model name before sending any request. The wrapper unsets
  both.
- **User settings** pin Claude model IDs, so the wrapper passes
  `--setting-sources project,local`. Skills and settings in the run
  directory's `.claude/` still load.
- **Context window.** Claude Code prints `[claude-code:unrecognized_model]`
  for non-Claude models and assumes a 200k window. The wrapper sets
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS` from CBorg's `max_input_tokens`.
- **Nested launches.** Variables from a parent Claude Code session
  (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, …) are cleared, so the wrapper
  also works when launched from inside one.

## Caveats for comparisons

- **Hidden prompts:** the `lbl/cborg-*` aliases have LBL-customized system
  prompts, per the CBorg docs.
- **No temperature control on most aliases:** the proxy drops
  `temperature` (and sometimes `reasoning_effort`) for most `cborg-*`
  aliases. `lbl/cborg-coder-max` is the exception and honors temperature.
  `lbl/gemma-4` and `lbl/gpt-oss-120b` honor it too, and have no hidden
  prompt either.
- **Response caching (confirmed):** CBorg replays the stored reply to an
  identical request: same response ID, 0.1 s. Sending
  `"cache": {"no-cache": true}` gets a fresh answer; `lil-agent models
  --probe` sends it. Agent runs are rarely affected, because each request
  carries a different conversation.
- **No version pinning:** exact model versions and quantization aren't
  published, and the lineup changes (see `inkling-small`). Record
  `/model/info` with every benchmark run.
- **Transient 403s:** the first `/model/info` call got a 403. The same
  request succeeded a minute later.
- **Data:** CBorg says LBL-hosted models are covered by enterprise
  agreements and the provider does not train on your data. Data goes to LBL
  servers, not a third party.
