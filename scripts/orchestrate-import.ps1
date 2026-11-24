<#
.SYNOPSIS
  Imports ClaimBack's tools, knowledge base and agents into watsonx Orchestrate
  (PowerShell replacement for `make orchestrate-import`). Checked against ADK 2.20.

.PARAMETER BackendUrl
  Public HTTPS URL of the FastAPI backend, e.g. https://claimback-api.onrender.com
  Orchestrate runs in IBM's cloud and cannot reach http://localhost.

.EXAMPLE
  .\scripts\orchestrate-import.ps1 -BackendUrl https://claimback-api.onrender.com
  .\scripts\orchestrate-import.ps1 -BackendUrl https://claimback-api.onrender.com -SkipConnection   # re-run

  Prerequisites: `orchestrate env activate <env>` done; backend deployed with
  TOOLS_API_KEY set. You'll be prompted for that same TOOLS_API_KEY value (input
  is hidden) so Orchestrate can send it as the X-API-Key header.
#>
param(
  [Parameter(Mandatory = $true)][string]$BackendUrl,
  [string]$AppId = "claimback_api",
  [switch]$SkipCredentials,
  # Re-runs: the connection and its credentials already exist
  [switch]$SkipConnection
)

$ErrorActionPreference = "Stop"
# The CLI prints characters like a checkmark; when its output is captured, Python
# would otherwise encode with the Windows ANSI code page and crash.
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Step([string]$Title, [scriptblock]$Cmd, [switch]$AllowError) {
  Write-Host "`n-- $Title " -ForegroundColor Cyan
  # The orchestrate CLI can print "[ERROR]" yet exit 0, so check its output too.
  # (stderr is merged; Continue stops PS 5.1 treating stderr lines as fatal.)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $output = & $Cmd 2>&1 | ForEach-Object { $line = "$_"; Write-Host $line; $line }
  } finally {
    $ErrorActionPreference = $prev
  }
  if ($AllowError) { return }
  if ($LASTEXITCODE -ne 0 -or ($output -match "\[ERROR\]")) {
    throw "Step failed: $Title"
  }
}

$BackendUrl = $BackendUrl.TrimEnd("/")
if ($BackendUrl -notmatch "^https://") {
  throw "BackendUrl must be a public https:// URL (Orchestrate can't reach localhost)."
}

# 1. Point the OpenAPI spec at the deployed backend (temp copy; repo file untouched)
# Done in Python: Windows PowerShell 5.1 writes UTF-8 *with* a BOM, which the
# ADK's JSON parser rejects, and its ConvertTo-Json can reshape nested arrays.
$tmpSpec = Join-Path $env:TEMP "claimback_openapi.json"
python scripts/set_openapi_server.py "orchestrate/tools/claimback_openapi.json" $BackendUrl $tmpSpec
if ($LASTEXITCODE -ne 0) { throw "Could not prepare the tool spec" }
Write-Host "Tool spec points at $BackendUrl"

# 2. Connection holding the X-API-Key the backend expects (draft + live)
if (-not $SkipConnection) {
  Invoke-Step "Create connection '$AppId' (ok if it already exists)" {
    orchestrate connections add -a $AppId
  } -AllowError
  foreach ($envName in @("draft", "live")) {
    Invoke-Step "Configure connection for $envName" {
      orchestrate connections configure -a $AppId --env $envName --type team --kind api_key --name X-API-Key --url $BackendUrl
    }
  }
  if (-not $SkipCredentials) {
    $secure = Read-Host "Enter the backend TOOLS_API_KEY (hidden)" -AsSecureString
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
      [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    foreach ($envName in @("draft", "live")) {
      Invoke-Step "Set credentials for $envName" {
        orchestrate connections set-credentials -a $AppId --env $envName --api-key $plain
      }
    }
    $plain = $null
  }
} else {
  Write-Host "Skipping connection setup (-SkipConnection)"
}

# 3. Tools, knowledge base, then agents (collaborators before the supervisor)
Invoke-Step "Import tools (OpenAPI)" {
  orchestrate tools import -k openapi -f $tmpSpec --app-id $AppId
}
Invoke-Step "Import knowledge base" {
  orchestrate knowledge-bases import -f orchestrate/knowledge/faq.yaml
}
foreach ($agent in @("intake_agent", "explainer_agent", "followup_agent", "claimback_supervisor")) {
  Invoke-Step "Import agent $agent" {
    orchestrate agents import -f "orchestrate/agents/$agent.yaml"
  }
}

Remove-Item $tmpSpec -ErrorAction SilentlyContinue
Write-Host "`nOK: Imported. Next:" -ForegroundColor Green
Write-Host "  orchestrate channels webchat embed --agent-name=claimback_supervisor"
Write-Host "  (approval flow not imported: orchestrate/flows/approval_and_followup.py needs rewriting for the real ADK flow API)"
