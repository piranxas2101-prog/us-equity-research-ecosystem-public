# Problem and Risk Reporting Standard

## What counts as a problem

A **problem** is an evidence-backed deviation from stated, expected, or safe system behavior. A report must identify:

- the triggering condition or input;
- the expected behavior;
- the observed behavior and reproducible evidence;
- the affected component and downstream scope;
- the problem type and current status.

Valid problem types are: software defect, data-quality defect, control gap, design limitation, documentation drift, and operational incident.

An untested idea, preference, or optional enhancement is not automatically a problem. Record it as an **improvement opportunity** unless evidence shows that current behavior is wrong, unsafe, misleading, or fails an agreed requirement.

## What counts as risk

A **risk** is a possible future consequence if a problem or uncertain condition is triggered. State it separately from the problem:

- scenario and consequence;
- likelihood from 1 (rare) to 5 (frequent);
- impact from 1 (minor) to 5 (critical);
- inherent score = likelihood × impact;
- confidence in the estimate;
- existing controls and residual exposure.

| Score | Severity | Normal response |
|---:|---|---|
| 20–25 | Critical | Stop affected path; fix before release or trading use |
| 12–19 | High | Prioritize before production readiness |
| 6–11 | Medium | Schedule with owner and verification criteria |
| 1–5 | Low | Track or accept explicitly |

Known current harm or the possibility of uncontrolled order execution can raise severity regardless of the numeric score.

## Required report entry

```text
ID / title:
Type / status / owner:
Condition:
Expected:
Observed evidence:
Affected scope:
Problem statement:
Risk scenario:
Likelihood / impact / inherent score / confidence:
Existing controls / residual risk:
Recommended change:
Acceptance test:
References (file, line, artifact, timestamp):
```

This format distinguishes facts from forecasts and improvements from defects. It also gives the implementing agent a testable definition of “done.”
