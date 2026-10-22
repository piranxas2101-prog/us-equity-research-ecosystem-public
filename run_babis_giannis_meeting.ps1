param(
    [int]$DurationMinutes = 30,
    [string]$BabisFile = ".\output\babis_shortlist_latest.json",
    [string]$GiannisFile = ".\output\giannis_rule_based_latest.json",
    [string]$YankosFile = ".\output\yankos_strategy_latest.json",
    [string]$PeriklisFile = ".\output\periklis_strategy_latest.json",
    [string]$PetrosFile = ".\output\petros_review_latest.json",
    [string]$CompareFile = "",
    [string]$OutputDir = ".\output",
    [string]$LogDir = ".\logs"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Path $OutputDir | Out-Null }
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$startTs = Get-Date
$stamp = $startTs.ToString("yyyyMMdd_HHmmss")
$logPath = Join-Path $LogDir "meeting_worker_$stamp.log"

function Log-Line {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format o), $Message
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

Log-Line "Meeting worker started."
Log-Line "DurationMinutes=$DurationMinutes"
Log-Line "BabisFile=$BabisFile"
Log-Line "GiannisFile=$GiannisFile"
Log-Line "YankosFile=$YankosFile"
Log-Line "PeriklisFile=$PeriklisFile"
Log-Line "PetrosFile=$PetrosFile"
Log-Line "CompareFile=$CompareFile"

if (-not (Test-Path $BabisFile)) {
    throw "Missing Babis file: $BabisFile"
}
if (-not (Test-Path $GiannisFile)) {
    throw "Missing Giannis file: $GiannisFile"
}
if (-not (Test-Path $YankosFile)) {
    throw "Missing Yankos file: $YankosFile"
}
if (-not (Test-Path $PetrosFile)) {
    throw "Missing Petros file: $PetrosFile"
}

$babis = Get-Content $BabisFile -Raw | ConvertFrom-Json
$giannis = Get-Content $GiannisFile -Raw | ConvertFrom-Json
$yankos = Get-Content $YankosFile -Raw | ConvertFrom-Json
$periklis = if (Test-Path $PeriklisFile) { Get-Content $PeriklisFile -Raw | ConvertFrom-Json } else { [PSCustomObject]@{} }
$petros = Get-Content $PetrosFile -Raw | ConvertFrom-Json
$sourceRunIds = @(
    @($babis.run_id, $giannis.run_id, $yankos.run_id, $periklis.run_id, $petros.run_id) |
        Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } |
        Select-Object -Unique
)
if ($sourceRunIds.Count -gt 1) {
    throw "Meeting inputs contain mixed run_ids: $($sourceRunIds -join ', ')"
}
$ecosystemRunId = if ($sourceRunIds.Count -eq 1) { [string]$sourceRunIds[0] } elseif ($env:ECOSYSTEM_RUN_ID) { [string]$env:ECOSYSTEM_RUN_ID } else { "run_$stamp" }
if ($env:ECOSYSTEM_RUN_ID -and $sourceRunIds.Count -eq 1 -and $ecosystemRunId -ne [string]$env:ECOSYSTEM_RUN_ID) {
    throw "Meeting run_id does not match ECOSYSTEM_RUN_ID."
}
$compare = $null
if (-not [string]::IsNullOrWhiteSpace($CompareFile) -and (Test-Path $CompareFile)) {
    $compare = Get-Content $CompareFile -Raw | ConvertFrom-Json
}

$checkpoints = @(5, 10, 15, 20, 25, 30)
foreach ($min in $checkpoints) {
    if ($min -gt $DurationMinutes) { break }
    $remaining = $min - ((Get-Date) - $startTs).TotalMinutes
    if ($remaining -gt 0) {
        Start-Sleep -Seconds [int]($remaining * 60)
    }
    Log-Line "Meeting checkpoint reached: ${min}m"
}

$endTs = Get-Date
$durationReal = [Math]::Round((($endTs - $startTs).TotalMinutes), 2)

$bTop = @($babis.shortlist | Select-Object -First 10)
$gResults = @($giannis.results)
$yResults = @($yankos.results)
$kResults = @($periklis.results)
$pResults = @($petros.results)
$gTop = @($gResults | Select-Object -First 10)
$gWorst = @($gResults | Sort-Object score_final | Select-Object -First 10)
$yTop = @($yResults | Where-Object { $_.signal_verdict -eq "candidate" } | Select-Object -First 10)
$yWatch = @($yResults | Where-Object { $_.signal_verdict -eq "watch" } | Select-Object -First 10)
$kTop = @($kResults | Where-Object { $_.signal_verdict -eq "candidate" } | Select-Object -First 10)
$kWatch = @($kResults | Where-Object { $_.signal_verdict -eq "watch" } | Select-Object -First 10)
$pTop = @($pResults | Where-Object { $_.petros_verdict -eq "candidate" } | Select-Object -First 10)
$pManual = @($pResults | Where-Object { $_.petros_verdict -eq "manual_review" } | Select-Object -First 10)
$tierGroups = @($gResults | Group-Object giannis_tier | Sort-Object Count -Descending)

$giannisTierByTicker = @{}
$giannisByTicker = @{}
foreach ($row in $gResults) {
    $giannisTierByTicker[[string]$row.ticker] = [string]$row.giannis_tier
    $giannisByTicker[[string]$row.ticker] = $row
}
$yankosByTicker = @{}
foreach ($row in $yResults) {
    $yankosByTicker[[string]$row.ticker] = $row
}
$periklisByTicker = @{}
foreach ($row in $kResults) {
    $periklisByTicker[[string]$row.ticker] = $row
}
$petrosByTicker = @{}
foreach ($row in $pResults) {
    $petrosByTicker[[string]$row.ticker] = $row
}

$sharedConviction = @()
foreach ($row in $pResults) {
    $ticker = [string]$row.ticker
    $pVerdict = [string]$row.petros_verdict
    $executionReady = ($row.research_readiness.execution_ready -eq $true)
    $gTier = [string]$giannisTierByTicker[$ticker]
    $yRow = $yankosByTicker[$ticker]
    $kRow = $periklisByTicker[$ticker]
    if (($null -eq $yRow) -or ($null -eq $kRow)) { continue }
    $yVerdict = [string]$yRow.signal_verdict
    $kVerdict = [string]$kRow.signal_verdict
    if ($executionReady -and ($pVerdict -eq "candidate") -and ($gTier -in @("Core Watchlist", "Selective Watchlist")) -and ($yVerdict -in @("candidate", "watch")) -and ($kVerdict -in @("candidate", "watch"))) {
        $sharedConviction += [PSCustomObject]@{
            ticker = $ticker
            petros = $row.petros_score
            giannis = $row.giannis_score
            yankos = $yRow.signal_score
            periklis = $kRow.signal_score
            petros_verdict = $pVerdict
            yankos_verdict = $yVerdict
            periklis_verdict = $kVerdict
            score_provenance = "Fundamental gate passed first; Yankos and Periklis then confirmed technical interest"
        }
    }
}
$sharedConviction = @($sharedConviction | Sort-Object petros -Descending | Select-Object -First 20)

$healthyDisagreements = @()
foreach ($row in $pResults) {
    $ticker = [string]$row.ticker
    $pVerdict = [string]$row.petros_verdict
    $executionReady = ($row.research_readiness.execution_ready -eq $true)
    $gTier = [string]$giannisTierByTicker[$ticker]
    $yRow = $yankosByTicker[$ticker]
    $kRow = $periklisByTicker[$ticker]
    if (($null -eq $yRow) -or ($null -eq $kRow)) { continue }
    $yVerdict = [string]$yRow.signal_verdict
    $kVerdict = [string]$kRow.signal_verdict
    $needsDiscussion = $false
    $reason = ""
    if ((-not $executionReady) -and (($yVerdict -eq "candidate") -or ($kVerdict -eq "candidate"))) {
        $needsDiscussion = $true
        $reason = "Technical interest exists, but the pre-trader fundamental gate is not execution-ready."
    } elseif (($pVerdict -eq "candidate") -and (($yVerdict -eq "reject") -or ($kVerdict -eq "reject"))) {
        $needsDiscussion = $true
        $reason = "The fundamental thesis passed, but at least one trader rejects the current technical entry."
    } elseif (($gTier -eq "Core Watchlist") -and (($yVerdict -eq "reject") -or ($kVerdict -eq "reject"))) {
        $needsDiscussion = $true
        $reason = "Giannis likes the company profile, but at least one trader rejects current timing."
    } elseif (($pVerdict -eq "reject") -and (($yVerdict -in @("candidate", "watch")) -or ($kVerdict -in @("candidate", "watch")))) {
        $needsDiscussion = $true
        $reason = "A trader sees tactical potential, but the upstream fundamental thesis rejects the security."
    }
    if ($needsDiscussion) {
        $healthyDisagreements += [PSCustomObject]@{
            ticker = $ticker
            reason = $reason
            petros_verdict = $pVerdict
            giannis_tier = $gTier
            yankos_verdict = $yVerdict
            periklis_verdict = $kVerdict
            score_provenance = "Qualitative disagreement queue; no numerical ranking applied"
        }
    }
}
$healthyDisagreements = @($healthyDisagreements | Select-Object -First 20)

$selectedCompanies = @()
foreach ($row in $sharedConviction) {
    $focusReason = "Core-team alignment after meeting."
    if ([string]$row.periklis_verdict -notin @("", "watch", "candidate")) {
        $focusReason = "Core-team alignment after meeting; Periklis remains more cautious."
    }
    $selectedCompanies += [PSCustomObject]@{
        ticker = $row.ticker
        review_bucket = "focus_now"
        giannis_score = $row.giannis
        yankos_score = $row.yankos
        periklis_score = $row.periklis
        petros_score = $row.petros
        giannis_tier = "Core/Selective aligned"
        yankos_verdict = $row.yankos_verdict
        periklis_verdict = $row.periklis_verdict
        petros_verdict = $row.petros_verdict
        reason = $focusReason
        score_provenance = $row.score_provenance
    }
}
foreach ($row in $healthyDisagreements) {
    if (($selectedCompanies | Where-Object { $_.ticker -eq $row.ticker }).Count -gt 0) {
        continue
    }
    $selectedCompanies += [PSCustomObject]@{
        ticker = $row.ticker
        review_bucket = "review_next"
        giannis_score = $null
        yankos_score = $null
        periklis_score = $null
        petros_score = $null
        giannis_tier = $row.giannis_tier
        yankos_verdict = $row.yankos_verdict
        periklis_verdict = $row.periklis_verdict
        petros_verdict = $row.petros_verdict
        reason = $row.reason
        score_provenance = $row.score_provenance
    }
}

$fillCandidates = @()
foreach ($row in $pResults) {
    if ($selectedCompanies.Count -ge 20) {
        break
    }

    $ticker = [string]$row.ticker
    if (($selectedCompanies | Where-Object { $_.ticker -eq $ticker }).Count -gt 0) {
        continue
    }

    $gRow = $giannisByTicker[$ticker]
    $yRow = $yankosByTicker[$ticker]
    $kRow = $periklisByTicker[$ticker]
    if ($null -eq $gRow) {
        continue
    }

    $gTier = [string]$gRow.giannis_tier
    $pVerdict = [string]$row.petros_verdict
    $technicalReviewEligible = ($row.research_readiness.technical_review_eligible -eq $true)
    $executionReady = ($row.research_readiness.execution_ready -eq $true)
    $yVerdict = if ($null -ne $yRow) { [string]$yRow.signal_verdict } else { "" }
    $kVerdict = if ($null -ne $kRow) { [string]$kRow.signal_verdict } else { "" }
    $giannisScore = if ($null -ne $gRow.score_final) { [double]$gRow.score_final } else { 0.0 }
    $petrosScore = if ($null -ne $row.petros_score) { [double]$row.petros_score } else { 0.0 }
    $yankosScore = if (($null -ne $yRow) -and ($null -ne $yRow.signal_score)) { [double]$yRow.signal_score } else { 0.0 }
    $periklisScore = if (($null -ne $kRow) -and ($null -ne $kRow.signal_score)) { [double]$kRow.signal_score } else { 0.0 }

    $hasGiannisSupport = $gTier -in @("Core Watchlist", "Selective Watchlist")
    $hasPetrosSupport = $technicalReviewEligible
    $hasYankosSupport = $yVerdict -in @("candidate", "watch")
    $hasPeriklisSupport = $kVerdict -in @("candidate", "watch")
    if (($pVerdict -eq "reject") -or (-not $technicalReviewEligible)) {
        continue
    }
    $supportCount = 0
    if ($hasGiannisSupport) { $supportCount += 1 }
    if ($hasYankosSupport) { $supportCount += 1 }
    if ($hasPeriklisSupport) { $supportCount += 1 }

    $scoreProvenance = ""
    if (($null -ne $yRow) -and ($null -ne $kRow)) {
        $rankingScore = [Math]::Round((0.55 * $giannisScore) + (0.225 * $yankosScore) + (0.225 * $periklisScore), 4)
        $scoreProvenance = "Fundamental packet eligibility first; reporting rank then uses 55% Giannis + 22.5% Yankos + 22.5% Periklis"
    } elseif ($null -ne $yRow) {
        $rankingScore = [Math]::Round((0.60 * $giannisScore) + (0.40 * $yankosScore), 4)
        $scoreProvenance = "Fundamental packet eligibility first; reporting rank then uses 60% Giannis + 40% Yankos; Periklis missing"
    } elseif ($null -ne $kRow) {
        $rankingScore = [Math]::Round((0.60 * $giannisScore) + (0.40 * $periklisScore), 4)
        $scoreProvenance = "Fundamental packet eligibility first; reporting rank then uses 60% Giannis + 40% Periklis; Yankos missing"
    } else {
        continue
    }
    if (($supportCount -lt 2) -or ($rankingScore -lt 5.5)) {
        continue
    }

    $reason = "Strong remaining team interest after meeting."
    $bucket = "team_watch"
    if ($hasGiannisSupport -and (-not $hasYankosSupport) -and (-not $hasPeriklisSupport)) {
        $bucket = "fundamental_followup"
        $reason = "Giannis supports the company, but neither trading agent confirms timing."
    } elseif ($hasGiannisSupport -and ($hasYankosSupport -xor $hasPeriklisSupport)) {
        $bucket = "timing_followup"
        $reason = "Giannis and one independent trading agent support it; the second trader disagrees or is absent."
    } elseif ((-not $hasGiannisSupport) -and ($hasYankosSupport -or $hasPeriklisSupport)) {
        $bucket = "tactical_followup"
        $reason = "Trading agents see tactical potential, but the independent quality gate is not supportive."
    } elseif ($hasGiannisSupport -and $hasYankosSupport -and $hasPeriklisSupport) {
        $bucket = "team_watch"
        $reason = if ($executionReady) { "Fundamental execution gate passed; both traders independently support the technical setup." } else { "Both traders show technical interest, but execution remains blocked by fundamental readiness." }
    }

    $fillCandidates += [PSCustomObject]@{
        ticker = $ticker
        review_bucket = $bucket
        giannis_score = [Math]::Round($giannisScore, 2)
        yankos_score = [Math]::Round($yankosScore, 2)
        periklis_score = if ($null -ne $kRow) { [Math]::Round($periklisScore, 2) } else { $null }
        petros_score = [Math]::Round($petrosScore, 2)
        giannis_tier = $gTier
        yankos_verdict = if ($yVerdict) { $yVerdict } else { "no_signal" }
        periklis_verdict = if ($kVerdict) { $kVerdict } else { "no_signal" }
        petros_verdict = $pVerdict
        reason = $reason
        ranking_score = $rankingScore
        score_provenance = $scoreProvenance
    }
}

foreach ($row in ($fillCandidates | Sort-Object ranking_score -Descending)) {
    if ($selectedCompanies.Count -ge 20) {
        break
    }
    if (($selectedCompanies | Where-Object { $_.ticker -eq $row.ticker }).Count -gt 0) {
        continue
    }
    $selectedCompanies += [PSCustomObject]@{
        ticker = $row.ticker
        review_bucket = $row.review_bucket
        giannis_score = $row.giannis_score
        yankos_score = $row.yankos_score
        periklis_score = $row.periklis_score
        petros_score = $row.petros_score
        giannis_tier = $row.giannis_tier
        yankos_verdict = $row.yankos_verdict
        periklis_verdict = $row.periklis_verdict
        petros_verdict = $row.petros_verdict
        reason = $row.reason
        score_provenance = $row.score_provenance
    }
}

$selectedCompaniesUnique = New-Object System.Collections.Generic.List[object]
$selectedTickerSet = @{}
foreach ($row in $selectedCompanies) {
    $ticker = [string]$row.ticker
    if ($selectedTickerSet.ContainsKey($ticker)) {
        continue
    }
    $selectedTickerSet[$ticker] = $true
    $selectedCompaniesUnique.Add($row)
}
$selectedCompanies = @($selectedCompaniesUnique | Select-Object -First 20)

$meetingPath = Join-Path $OutputDir "meeting_team_daily_$stamp.md"
$meetingJsonPath = Join-Path $OutputDir "meeting_team_daily_$stamp.json"
$donePath = Join-Path $OutputDir "meeting_team_done_$stamp.txt"
$decisionLatestMdPath = Join-Path $OutputDir "team_decision_latest.md"
$decisionLatestJsonPath = Join-Path $OutputDir "team_decision_latest.json"

$lines = @()
$lines += "# Team Meeting Report - babis + giannis + yankos + periklis + petros"
$lines += ""
$lines += "- Start: $($startTs.ToString('o'))"
$lines += "- End: $($endTs.ToString('o'))"
$lines += "- Duration target: $DurationMinutes minutes"
$lines += "- Duration actual: $durationReal minutes"
$lines += ""
$lines += "## Session Purpose"
$lines += ""
$lines += "- Build one team identity around a shared investing mission."
$lines += "- Keep fundamental synthesis complete and frozen before comparing two independent technical views."
$lines += "- Let each agent understand how the others think and where they disagree."
$lines += "- Turn disagreements into better rules, not into noise."
$lines += ""
$lines += "## Team Charter"
$lines += ""
$lines += "1. babis finds opportunity."
$lines += "2. giannis protects quality and balance-sheet discipline."
$lines += "3. petros completes the fundamental-only thesis, readiness decision, and frozen handoff."
$lines += "4. yankos checks whether an eligible thesis fits his technical swing-trade playbook right now."
$lines += "5. periklis independently checks early accumulation and pre-run technical structure."
$lines += "6. The user remains the only real decision-maker."
$lines += ""
$lines += "## Babis Snapshot"
$lines += ""
$lines += "- Source run: $($babis.run_timestamp)"
$lines += "- Shortlist count: $($babis.shortlist_total)"
$lines += "- Candidate pool: $($babis.candidates_total)"
$lines += ""
$lines += "Top 10 from babis:"
foreach ($row in $bTop) {
    $lines += "- #$($row.rank) $($row.ticker) | $($row.catalyst_bucket) | risk=$($row.risk_level) | verdict=$($row.verdict)"
}
$lines += ""
$lines += "## Giannis Snapshot (No LLM)"
$lines += ""
$lines += "- Source run: $($giannis.run_timestamp)"
$lines += "- Coverage: $($giannis.count)"
$lines += ""
$lines += "Tier split:"
foreach ($g in $tierGroups) {
    $lines += "- $($g.Name): $($g.Count)"
}
$lines += ""
$lines += "Top 10 by giannis score:"
foreach ($row in $gTop) {
    $lines += "- $($row.ticker) | score=$($row.score_final)/10 | tier=$($row.giannis_tier) | risk=$($row.risk_level)"
}
$lines += ""
$lines += "## Yankos Snapshot"
$lines += ""
$lines += "- Source run: $($yankos.run_timestamp)"
$lines += "- Coverage: $($yankos.count)"
$lines += ""
$lines += "Top swing candidates:"
if ($yTop.Count -gt 0) {
    foreach ($row in $yTop) {
        $lines += "- $($row.ticker) | score=$($row.signal_score)/10 | verdict=$($row.signal_verdict) | playbook=$($row.playbook_name)"
    }
} else {
    $lines += "- No Yankos candidates today."
}
$lines += ""
$lines += "Top swing watch names:"
if ($yWatch.Count -gt 0) {
    foreach ($row in $yWatch) {
        $lines += "- $($row.ticker) | score=$($row.signal_score)/10 | verdict=$($row.signal_verdict)"
    }
} else {
    $lines += "- No Yankos watch names today."
}
$lines += ""
$lines += "## Periklis Snapshot"
$lines += ""
$lines += "- Source run: $($periklis.run_timestamp)"
$lines += "- Coverage: $($periklis.count)"
$lines += ""
$lines += "Top swing candidates:"
if ($kTop.Count -gt 0) {
    foreach ($row in $kTop) {
        $lines += "- $($row.ticker) | score=$($row.signal_score)/10 | verdict=$($row.signal_verdict) | playbook=$($row.playbook_name)"
    }
} else {
    $lines += "- No Periklis candidates today."
}
$lines += ""
$lines += "Top swing watch names:"
if ($kWatch.Count -gt 0) {
    foreach ($row in $kWatch) {
        $lines += "- $($row.ticker) | score=$($row.signal_score)/10 | verdict=$($row.signal_verdict)"
    }
} else {
    $lines += "- No Periklis watch names today."
}
$lines += ""
$lines += "## Petros Snapshot"
$lines += ""
$lines += "- Source run: $($petros.run_timestamp)"
$lines += "- Coverage: $($petros.count)"
$lines += ""
$lines += "Fundamental candidates (before technical analysis):"
if ($pTop.Count -gt 0) {
    foreach ($row in $pTop) {
        $lines += "- $($row.ticker) | score=$($row.petros_score)/10 | verdict=$($row.petros_verdict) | security_readiness=$($row.research_readiness.security_thesis_readiness) | execution_ready=$($row.research_readiness.execution_ready)"
    }
} else {
    $lines += "- No Petros candidates today."
}
$lines += ""
$lines += "Manual-review queue:"
if ($pManual.Count -gt 0) {
    foreach ($row in $pManual) {
        $lines += "- $($row.ticker) | score=$($row.petros_score)/10 | verdict=$($row.petros_verdict)"
    }
} else {
    $lines += "- No Petros manual-review names today."
}
$lines += ""
$lines += "Bottom 10 (risk-heavy cluster):"
foreach ($row in $gWorst) {
    $flags = "none"
    if ($row.dilution_flags -and $row.dilution_flags.Count -gt 0) {
        $flags = ($row.dilution_flags -join ", ")
    }
    $lines += "- $($row.ticker) | score=$($row.score_final)/10 | tier=$($row.giannis_tier) | price=$($row.share_price) | flags=$flags"
}
$lines += ""
$lines += "## Logic Shift Vs Previous Scan"
$lines += ""
if ($null -ne $compare) {
    $lines += "- Baseline label: $($compare.baseline_label)"
    $lines += "- Current label: $($compare.current_label)"
    $lines += "- Compare file: $CompareFile"
    $lines += ""
    $lines += "Babis changes:"
    $lines += "- Top-10 overlap: $($compare.babis.top10_overlap_count)/10"
    $lines += "- Shortlist size: $($compare.babis.old_shortlist_total) -> $($compare.babis.new_shortlist_total)"
    $addedRows = @($compare.babis.added_to_shortlist | Select-Object -First 5)
    if ($addedRows.Count -gt 0) {
        $lines += "- New shortlist names: $(($addedRows | ForEach-Object { $_.ticker }) -join ', ')"
    } else {
        $lines += "- No new shortlist names reached the top compare window."
    }
    $rankRows = @($compare.babis.best_rank_improvements | Select-Object -First 5)
    if ($rankRows.Count -gt 0) {
        $lines += "- Strongest rank improvements: $(($rankRows | ForEach-Object { '{0}(+{1})' -f $_.ticker, $_.rank_delta }) -join ', ')"
    } else {
        $lines += "- No major Babis rank jumps versus baseline."
    }
    $lines += ""
    $lines += "Giannis changes:"
    $newCoreRows = @($compare.giannis.new_core_names | Select-Object -First 5)
    if ($newCoreRows.Count -gt 0) {
        $lines += "- New Core names: $(($newCoreRows | ForEach-Object { $_.ticker }) -join ', ')"
    } else {
        $lines += "- No new Core names versus baseline."
    }
    $upgradeRows = @($compare.giannis.tier_upgrades | Select-Object -First 5)
    if ($upgradeRows.Count -gt 0) {
        $lines += "- Tier upgrades: $(($upgradeRows | ForEach-Object { '{0}({1})' -f $_.ticker, $_.new_tier }) -join ', ')"
    } else {
        $lines += "- No major Giannis tier upgrades."
    }
    $lines += ""
    $lines += "Yankos changes:"
    $newYankosCandidates = @($compare.yankos.new_candidates | Select-Object -First 5)
    $newYankosWatch = @($compare.yankos.new_watch | Select-Object -First 5)
    if ($newYankosCandidates.Count -gt 0) {
        $lines += "- New swing candidates: $(($newYankosCandidates | ForEach-Object { $_.ticker }) -join ', ')"
    } else {
        $lines += "- No new Yankos candidates versus baseline."
    }
    if ($newYankosWatch.Count -gt 0) {
        $lines += "- New swing watch names: $(($newYankosWatch | ForEach-Object { $_.ticker }) -join ', ')"
    } else {
        $lines += "- Yankos watch board stayed broadly stable."
    }
    $lines += ""
    if ($compare.periklis) {
        $lines += "Periklis changes:"
        $newPeriklisCandidates = @($compare.periklis.new_candidates | Select-Object -First 5)
        $newPeriklisWatch = @($compare.periklis.new_watch | Select-Object -First 5)
        if ($newPeriklisCandidates.Count -gt 0) {
            $lines += "- New swing candidates: $(($newPeriklisCandidates | ForEach-Object { $_.ticker }) -join ', ')"
        } else {
            $lines += "- No new Periklis candidates versus baseline."
        }
        if ($newPeriklisWatch.Count -gt 0) {
            $lines += "- New swing watch names: $(($newPeriklisWatch | ForEach-Object { $_.ticker }) -join ', ')"
        } else {
            $lines += "- Periklis watch board stayed broadly stable."
        }
        $lines += ""
    }
    $lines += "Petros changes:"
    $newPetrosCandidates = @($compare.petros.new_candidates | Select-Object -First 5)
    $droppedPetrosCandidates = @($compare.petros.dropped_candidates | Select-Object -First 5)
    if ($newPetrosCandidates.Count -gt 0) {
        $lines += "- New fundamental candidates: $(($newPetrosCandidates | ForEach-Object { $_.ticker }) -join ', ')"
    } else {
        $lines += "- No new Petros candidates versus baseline."
    }
    if ($droppedPetrosCandidates.Count -gt 0) {
        $lines += "- Dropped fundamental candidates: $(($droppedPetrosCandidates | ForEach-Object { $_.ticker }) -join ', ')"
    } else {
        $lines += "- Petros candidate board stayed broadly stable."
    }
    $lines += ""
    $lines += "- Team reading: compare these changes with the old logic to decide which upgrades are real and which are only noise."
} else {
    $lines += "- No comparison file was supplied for this meeting."
}
$lines += ""
$lines += "## Core-Team Conviction"
$lines += ""
if ($sharedConviction.Count -gt 0) {
    foreach ($row in $sharedConviction) {
        $consensusParts = @(
            "$($row.ticker)",
            "giannis=$($row.giannis)/10",
            "yankos=$($row.yankos)/10"
        )
        if ($null -ne $row.periklis) {
            $consensusParts += "periklis=$($row.periklis)/10"
        }
        $consensusParts += "petros=$($row.petros)/10"
        $lines += "- " + ($consensusParts -join " | ")
    }
} else {
    $lines += "- No core-team conviction names today."
}
$lines += ""
$lines += "## Healthy Disagreements"
$lines += ""
if ($healthyDisagreements.Count -gt 0) {
    foreach ($row in $healthyDisagreements) {
        $disagreementParts = @(
            "$($row.ticker)",
            "giannis=$($row.giannis_tier)",
            "yankos=$($row.yankos_verdict)"
        )
        if (-not [string]::IsNullOrWhiteSpace([string]$row.periklis_verdict)) {
            $disagreementParts += "periklis=$($row.periklis_verdict)"
        }
        $disagreementParts += @(
            "petros=$($row.petros_verdict)",
            "$($row.reason)"
        )
        $lines += "- " + ($disagreementParts -join " | ")
    }
} else {
    $lines += "- No major strategic disagreements today."
}
$lines += ""
$lines += "## Team Lessons"
$lines += ""
$lines += "- babis teaches the team where fresh catalysts are appearing."
$lines += "- giannis teaches the team where balance-sheet or structure risk should slow us down."
$lines += "- petros teaches the team to separate company quality, security valuation, priced-in expectations, and readiness before timing."
$lines += "- yankos teaches the team when a fundamentally eligible company is still a bad swing entry today."
$lines += "- periklis teaches the team whether a fundamentally eligible setup is early enough for a cleaner swing entry."
$lines += ""
$lines += "## Joint Decisions"
$lines += ""
$lines += "1. No one agent is the boss of the others; each one contributes a different kind of edge."
$lines += "2. Daily conviction requires a passed fundamental gate followed by independent technical confirmation; the two layers are never averaged into one hidden opinion."
$lines += "3. Healthy disagreement is valuable because it tells the team where the rules are still incomplete."
$lines += "4. The team goal is not just to find setups, but to learn together which setups actually work."
$lines += ""
$lines += "## Action Items"
$lines += ""
$lines += "- Keep daily refresh on locked symbols."
$lines += "- Review the shared-conviction names first."
$lines += "- Record disagreements that later outperform or fail; those are the best training examples."
$lines += "- Use the next daily meeting to compare what each agent saw versus what the outcomes proved."

$lines | Set-Content -Path $meetingPath -Encoding UTF8

$decisionLines = @()
$decisionLines += "# Team Decision - Final Post-Meeting Output"
$decisionLines += ""
$decisionLines += "- Meeting timestamp: $($endTs.ToString('o'))"
$decisionLines += "- This is the final team view after discovery, filtering, two swing timing reads, and synthesis."
$decisionLines += "- Review list size: $($selectedCompanies.Count)"
$decisionLines += ""
$decisionLines += "## Selected Companies For User Review"
$decisionLines += ""
if ($selectedCompanies.Count -gt 0) {
    foreach ($row in $selectedCompanies) {
        $decisionParts = @(
            "$($row.ticker)",
            "bucket=$($row.review_bucket)",
            "yankos=$($row.yankos_verdict)"
        )
        if (-not [string]::IsNullOrWhiteSpace([string]$row.periklis_verdict)) {
            $decisionParts += "periklis=$($row.periklis_verdict)"
        }
        $decisionParts += @(
            "petros=$($row.petros_verdict)",
            "$($row.reason)"
        )
        $decisionLines += "- " + ($decisionParts -join " | ")
    }
} else {
    $decisionLines += "- No companies selected for user review."
}
$decisionLines += ""
$decisionLines += "## Core-Team Focus Now"
$decisionLines += ""
if ($sharedConviction.Count -gt 0) {
    foreach ($row in $sharedConviction) {
        $focusParts = @(
            "$($row.ticker)",
            "giannis=$($row.giannis)/10",
            "yankos=$($row.yankos)/10"
        )
        if ($null -ne $row.periklis) {
            $focusParts += "periklis=$($row.periklis)/10"
        }
        $focusParts += "petros=$($row.petros)/10"
        $decisionLines += "- " + ($focusParts -join " | ")
    }
} else {
    $decisionLines += "- No core-team agreement names today."
}
$decisionLines += ""
$decisionLines += "## Review Next"
$decisionLines += ""
if ($healthyDisagreements.Count -gt 0) {
    foreach ($row in $healthyDisagreements) {
        $reviewParts = @(
            "$($row.ticker)",
            "giannis=$($row.giannis_tier)",
            "yankos=$($row.yankos_verdict)"
        )
        if (-not [string]::IsNullOrWhiteSpace([string]$row.periklis_verdict)) {
            $reviewParts += "periklis=$($row.periklis_verdict)"
        }
        $reviewParts += @(
            "petros=$($row.petros_verdict)",
            "$($row.reason)"
        )
        $decisionLines += "- " + ($reviewParts -join " | ")
    }
} else {
    $decisionLines += "- No active disagreement queue today."
}
$decisionLines | Set-Content -Path $decisionLatestMdPath -Encoding UTF8

$decisionPayload = [ordered]@{
    run_id = $ecosystemRunId
    generated_at = $endTs.ToString("o")
    meeting_report = $meetingPath
    compare_report = $CompareFile
    team = @("babis", "giannis", "yankos", "periklis", "petros")
    selected_companies = @(
        foreach ($row in $selectedCompanies) {
            [ordered]@{
                ticker = $row.ticker
                review_bucket = $row.review_bucket
                giannis_score = $row.giannis_score
                yankos_score = $row.yankos_score
                periklis_score = $row.periklis_score
                petros_score = $row.petros_score
                giannis_tier = $row.giannis_tier
                yankos_verdict = $row.yankos_verdict
                periklis_verdict = $row.periklis_verdict
                petros_verdict = $row.petros_verdict
                reason = $row.reason
                score_provenance = $row.score_provenance
            }
        }
    )
    focus_now = @(
        foreach ($row in $sharedConviction) {
            [ordered]@{
                ticker = $row.ticker
                giannis_score = $row.giannis
                yankos_score = $row.yankos
                periklis_score = $row.periklis
                petros_score = $row.petros
                yankos_verdict = $row.yankos_verdict
                periklis_verdict = $row.periklis_verdict
                petros_verdict = $row.petros_verdict
                consensus = "core_team_alignment"
                score_provenance = $row.score_provenance
            }
        }
    )
    review_next = @(
        foreach ($row in $healthyDisagreements) {
            [ordered]@{
                ticker = $row.ticker
                giannis_tier = $row.giannis_tier
                yankos_verdict = $row.yankos_verdict
                periklis_verdict = $row.periklis_verdict
                petros_verdict = $row.petros_verdict
                reason = $row.reason
                consensus = "needs_discussion"
            }
        }
    )
}
$decisionPayload | ConvertTo-Json -Depth 8 | Set-Content -Path $meetingJsonPath -Encoding UTF8
$decisionPayload | ConvertTo-Json -Depth 8 | Set-Content -Path $decisionLatestJsonPath -Encoding UTF8

$done = @(
    "meeting_complete",
    "start=$($startTs.ToString('o'))",
    "end=$($endTs.ToString('o'))",
    "duration_target_minutes=$DurationMinutes",
    "duration_actual_minutes=$durationReal",
    "report=$meetingPath",
    "report_json=$meetingJsonPath",
    "team_decision_md=$decisionLatestMdPath",
    "team_decision_json=$decisionLatestJsonPath",
    "log=$logPath",
    "agents=babis,giannis,yankos,petros"
)
$done | Set-Content -Path $donePath -Encoding UTF8

Log-Line "Meeting report generated: $meetingPath"
Log-Line "Done file generated: $donePath"
Log-Line "Meeting worker finished."
