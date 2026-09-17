param(
  [ValidateSet('scan', 'submit', 'outcomes', 'report')][string]$Mode = 'scan',
  [string]$Plan,
  [string]$Stores = '',
  [ValidateRange(1, 500)][int]$Limit = 50
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
  if ($Mode -eq 'report') {
    & node 'report.mjs'
  } else {
    if ($Mode -eq 'submit' -and -not $Plan) { throw 'Submit requires -Plan with an approved review plan.' }
    $taskArgs = @('run.mjs', '--mode', $Mode, '--stores', $Stores, '--limit', "$Limit")
    if ($Plan) { $taskArgs += @('--plan', $Plan) }
    & node @taskArgs
  }
  if ($LASTEXITCODE -ne 0) { throw "DoorDash $Mode failed with exit code $LASTEXITCODE." }
} finally { Pop-Location }
