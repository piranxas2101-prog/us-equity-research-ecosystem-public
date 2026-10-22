# Security checks and OpenClaw host readiness

This repository has an on-demand, one-shot security audit in `ecosystem_security.py`.
It does not run a watcher, scheduled loop, Windows service, or automatic host
hardening. Run individual checks with `python ecosystem_security.py preflight`,
`secrets`, `dependencies`, `python-runtime`, `workflows`, or `sbom`. `all` runs these checks
once. Summaries are written to `output/security/<command>_latest.json`, which is
ignored by Git. The raw Syft SBOM stays in `output/security/` as a local artifact.
The Gitleaks summary records only counts and rule names, never matching secret
values. The command exits nonzero for findings, errors, missing inventory or an
OpenClaw preflight that is not yet ready; inspect the JSON report for the reason.

The adaptation draws on the official GitHub projects [Gitleaks], [Trivy],
[Syft], [Grype], and [zizmor]. Gitleaks checks Git history and a bounded copy of
current source files. Trivy checks supported dependency and configuration
inventories. Syft generates a software bill of materials and Grype checks its
identified packages. zizmor audits GitHub Actions workflows offline. The CI
workflow runs zizmor and audits the locked Python dependencies on push and pull
request events, with pinned action commits and without credential persistence.
Run the local Gitleaks check before pushing changes; secret scanning is not yet
enforced in CI.

The original Python requirements use version ranges and optional, unpinned
dependencies. If Trivy reports `no_inventory`, that means it could not identify
a supported dependency inventory; zero reported vulnerabilities is **not** a
clean bill of health. The SBOM similarly covers only packages Syft actually
identifies; review its `package_count` before relying on Grype's zero count.

`python-runtime` uses an isolated `.security-venv` copy of pip-audit to check
packages installed in the Python interpreter that invokes the script. It does
not upgrade that interpreter. The 19 September 2026 baseline found 336 packages,
30 affected packages and 200 advisory records. The project-specific
`requirements-windows-py312.lock` resolves the core requirements to 108 exact,
hash-pinned package versions for Windows/Python 3.12; an audit of that proposed
lock found no known vulnerabilities. The isolated `.venv-next` environment was
installed from that lock and passed 300 unit tests and `uv pip check` (108
packages compatible). The independent full-market research runner now selects
`.venv-next` and passes it to its daily pipeline stages. Other launchers may
still use the global Python installation when `.venv` is absent. Optional
extension requirements are not in this core lock and require their own review
before activation.

OpenClaw must run outside this research/trading process. The preferred Windows
deployment is Windows Hub with an app-owned WSL2 Gateway, with Windows drive
automount and interop disabled and Windows-node command execution left off.
The fallback native-Windows deployment requires a dedicated standard account
and checked workspace ACLs. Neither deployment should mount or copy the trading
credentials into an agent workspace. The `preflight` check reports Defender,
firewall profiles, selected listeners, Node CLI compatibility, WSL availability,
and the native-account state. It **never** declares isolation ready until the
chosen deployment has been installed, its permissions checked, and OpenClaw's
own `security audit --deep` and sandbox inspection have passed. It changes no
firewall, Defender, account, or filesystem permissions.

The current host has Windows 11 Home, enabled Defender and firewall profiles,
no OpenClaw CLI, no WSL distro, no Docker command, and Node 24.14.0. That Node
version is below OpenClaw CLI's current 24.16+ or 26.1+ requirement; a Windows
Hub-managed WSL Gateway may provision its own runtime. No system-level change
should be called complete on the basis of the read-only host preflight alone.

The following upstream projects were assessed but deliberately not deployed to
this host: [AttackSurfaceAnalyzer] and [HardeningKitty] for audit-only host
baselines; [Harden-Windows-Security] for selective policy review; [Sysmon config]
and [Sysmon modular] for future event telemetry; [Cosign] and [OpenSSF Scorecard]
for artifact provenance and upstream dependency assessment; and [promptfoo] for local-only agent
input testing after OpenClaw's deployment and network behavior are defined.
Host policy changes and resident telemetry require a separate change plan and
validation on this Windows edition.

[Gitleaks]: https://github.com/gitleaks/gitleaks
[Trivy]: https://github.com/aquasecurity/trivy
[Syft]: https://github.com/anchore/syft
[Grype]: https://github.com/anchore/grype
[zizmor]: https://github.com/zizmorcore/zizmor
[AttackSurfaceAnalyzer]: https://github.com/microsoft/AttackSurfaceAnalyzer
[HardeningKitty]: https://github.com/scipag/HardeningKitty
[Harden-Windows-Security]: https://github.com/HotCakeX/Harden-Windows-Security
[Sysmon config]: https://github.com/SwiftOnSecurity/sysmon-config
[Sysmon modular]: https://github.com/olafhartong/sysmon-modular
[Cosign]: https://github.com/sigstore/cosign
[OpenSSF Scorecard]: https://github.com/ossf/scorecard
[pip-audit]: https://github.com/pypa/pip-audit
[promptfoo]: https://github.com/promptfoo/promptfoo
