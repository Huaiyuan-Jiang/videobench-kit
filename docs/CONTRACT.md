# Task contract

How to write a task that the framework can set up, collect and grade. `generators/SAMPLE.py` and `grading/SAMPLE/` follow every rule here; copy them.

## Files

```
generators/<ROLE>.py              builds the task from a fixed seed
tasks/<ROLE>/<TASK>/brief.md      what the model reads
tasks/<ROLE>/<TASK>/input/        input files
tasks/<ROLE>/<TASK>/refs/         reference documents (open-book tasks)
grading/<ROLE>/<TASK>/meta.json   task settings
grading/<ROLE>/<TASK>/answer.json answer key
grading/<ROLE>/<TASK>/manifest.json  sha256 of every file under tasks/<ROLE>/<TASK>/
grading/<ROLE>/grader.py          one grader per role
grading/<ROLE>/DESIGN.md          how each task is scored, in prose
```

The model only ever sees a copy of `tasks/<ROLE>/<TASK>/`. Nothing under `grading/` goes into its working directory. The manifest lets collection tell the original inputs from files the model wrote or changed.

## Generator

Same seed, same bytes. Run it twice and the manifest must not change. Generators write both `tasks/` and `grading/`.

## meta.json

| Field | Meaning |
|---|---|
| `id`, `role`, `title`, `short_title` | names |
| `difficulty` | `easy`, `medium` or `hard`; sets the default time target |
| `dimension` | what the task mainly tests, e.g. `accuracy` |
| `needs` | `text`, `vision`, `shell`: what the model must be able to do |
| `q_pass` | pass line for Q, 0 to 1 |
| `quality_spec` | `parts` (id, label, weight), `formula` in words, `params` |
| `required_deliverables` | files under the working directory that must exist |
| `allowed_writes` | glob patterns the model may write, usually `out/**` |
| `required_claims` | claims the report must make, each with `id` and `desc` |
| `constraints` | task rules, each with `id`, `desc` and `hard` |
| `traps` | hidden traps, each with an id the grader reports on |
| `open_book` | null, or what reference the model may use |
| `leak_tokens` | strings that would reveal the answer if they showed up in inputs |
| `generator` | script and seed |

## brief.md

Plain language, written to the model. Say what to deliver, where, and in what format. End with the report section that `bench.common.brief_report_section` produces, so every task asks for the same `report.json`.

## report.json

```json
{
  "task_id": "SAMPLE1",
  "status": "done",
  "deliverables": ["out/table.json"],
  "files_modified": ["out/table.json"],
  "commands_run": ["python read_frames.py"],
  "checks": [{"name": "table parses", "ran": true, "passed": true, "note": ""}],
  "frames_viewed": [{"path": "input/frames/frame_01.png", "source": null, "t": null}],
  "claims": {"frames_read": 4, "missing_count": 1},
  "unresolved": [],
  "notes": ""
}
```

`status` is `done`, `partial` or `blocked`. `claims` holds the values the brief asked for. `frames_viewed` lists every image the model looked at; the framework checks those files exist.

## grader.py

```python
def grade(work, task, key, meta, report) -> dict
def make_reference(work, key, meta) -> None
def make_wrong(work, key, meta) -> None
```

`work` is the collected working directory, `task` the original task folder, `key` the grading folder, `report` the parsed `report.json` or None.

`grade` returns:

| Key | Content |
|---|---|
| `quality_parts` | `{part_id: 0..1}` for every part in `quality_spec` |
| `items` | per-item results, built with `common.item(id, label, ok, expected, got)` |
| `constraints` | `common.constraint(id, ok, note)` for each rule |
| `claims` | `common.claim_eq(id, said, actual)` and friends: `supported`, `contradicted` or `unverifiable` |
| `traps` | `common.trap(id, spotted, detail)` |
| `abstain` | `common.abstain(count, out_of)`: items the model refused without reason |
| `evidence_frames` | images the score depends on |
| `taste`, `rule_checks`, `details` | optional extras |

Mark a claim `contradicted` only with hard evidence. It zeroes the task. When in doubt, use `unverifiable`.

`make_reference` writes a correct answer into `work`. `make_wrong` writes a plausible wrong one. The self-check expects the reference to score at least 95 and an empty directory at most 10. Choice tasks may also define `dummy_strategies` and `make_dummy` for answer patterns such as "always A"; those must score low.

## Self-check

```sh
python -B selfcheck.py <ROLE>
```

Builds each task in a temp folder, runs reference, empty, wrong and dummy answers through the grader, and reports any task that scores outside its range.
