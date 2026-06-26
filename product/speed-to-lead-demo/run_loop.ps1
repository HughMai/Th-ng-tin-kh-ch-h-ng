# Headless voice goal-loop: measure -> (if still failing) fix -> repeat.
#
# run_loop.ps1 owns the loop. Each iteration it runs goal_loop.py (which assesses
# the voice eval, decides GOAL_MET / CONTINUE / ABORT, and writes .loop/fix_brief.md
# when a fix is needed), then — only on CONTINUE — runs `claude -p` against
# .loop/fix_prompt.md so the fixer applies ONE surgical edit. goal_loop enforces
# the allowlist at the start of every pass, reverting any off-scope edit the
# fixer made, so the loop can't drift outside voice_engine.py / workflow.py /
# knowledge.md.
#
# Bounded two ways: goal_loop's own --max-iters / --spend-cap, plus the -MaxLoops
# backstop here. Fixes are NOT committed — they stay in the working tree for
# Hughie to review and commit. Ctrl+C stops the loop any time.
#
# Usage (from this dir, venv active, ANTHROPIC_API_KEY + claude CLI available):
#   pwsh -File run_loop.ps1
#   pwsh -File run_loop.ps1 -Tenant dave -MaxLoops 8
#
# First time, add --reset on goal_loop (edit below) so the iteration count starts
# at zero, or delete .loop/state.json before starting.

param(
    [string]$Tenant = "dave",
    [int]$MaxLoops = 8,
    [int]$MaxIters = 8
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
if (-not (Test-Path ".loop\fix_prompt.md")) {
    Write-Host "missing .loop/fix_prompt.md — run from the product dir" -ForegroundColor Red
    exit 2
}
$fixPrompt = Get-Content -Raw ".loop\fix_prompt.md"

for ($i = 1; $i -le $MaxLoops; $i++) {
    Write-Host "`n=== loop $i ===" -ForegroundColor Cyan

    # 1. Measure + decide. (goal_loop reverts off-scope edits at its start.)
    & $py goal_loop.py --mode voice --tenant $Tenant --max-iters $MaxIters
    $rc = $LASTEXITCODE
    if ($rc -eq 0) { Write-Host "GOAL MET — stopping." -ForegroundColor Green; break }
    if ($rc -eq 2) { Write-Host "ABORTED by goal_loop (cap / thrash / engine). See Telegram + stderr. Stopping." -ForegroundColor Red; break }
    # rc -eq 1 -> CONTINUE: a fresh fix brief is in .loop\fix_brief.md.

    # 2. The fixer applies ONE edit. The next loop iteration re-measures it.
    claude -p $fixPrompt
    if ($LASTEXITCODE -ne 0) { Write-Host "fixer (claude -p) exited $LASTEXITCODE — stopping." -ForegroundColor Red; break }
}

Write-Host "`nLoop finished. Review the (uncommitted) changes before keeping them:" -ForegroundColor Yellow
Write-Host "  git status" -ForegroundColor DarkGray
Write-Host "  git diff voice_engine.py workflow.py knowledge.md" -ForegroundColor DarkGray
Write-Host "  git checkout -- voice_engine.py workflow.py knowledge.md   # to discard a bad run" -ForegroundColor DarkGray
