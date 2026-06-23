# deploy.ps1 — Create and deploy cps-trip-sync to Azure
#
# Prerequisites:
#   az login  (already authenticated)
#   func v4:  winget install --id Microsoft.Azure.FunctionsCoreTools
#
# Usage:
#   .\deploy.ps1           # create infra + deploy
#   .\deploy.ps1 deploy    # deploy only (infra already exists)

param(
    [string]$Mode = "all"
)

$ErrorActionPreference = "Stop"

# ── Config ────────────────────────────────────────────────────────────────────
$SUBSCRIPTION    = "sub-capacitysystem-dev"
$RESOURCE_GROUP  = "rg-capacity"
$LOCATION        = "swedencentral"
$SUBNET_ID       = "/subscriptions/62ca3ec4-deec-4283-abbe-029935175c27/resourceGroups/rg-network/providers/Microsoft.Network/virtualNetworks/vnet-capacitysystem-d-01/subnets/subnet-10.11.11.128-25"
$STORAGE_ACCOUNT = "rgcapacityac09"
$APP_INSIGHTS    = "appi-capacity-01"
$FUNCTION_APP    = "cps-trip-sync"
$PYTHON_VERSION  = "3.12"

# ── PROD env vars ─────────────────────────────────────────────────────────────
$Settings = @(
    "NAV_BASE=http://navbatchsrv:7348/WSNTLM/ODataV4"
    "NAV_USERNAME=Standindriver"
    "NAV_PASS=Driver2022"
    "NAV_DOMAIN=admin"
    "NAV_COMPANIES=AA Grus,AA Transportservice,Alex Andersen Frugt & Grønt,Alex Andersen Greve,Alex Andersen Holland,Alex Andersen Japanlaan,Alex Andersen Logistics,Alex Andersen Lux,Alex Andersen Norge AS,Alex Andersen Ølund,Alex Andersen Ølund Holding,Alex Andersen Sverige,Alex Andersen Tyskland,Easy Security,Elin & Alex Andersen Transport,Konsolidering AAØ,Maxim Wash,Thomas B Pedersen,Vognmand Keld Demant,WS"
    "SQL_SERVER=sqs-capacity-d-01.database.windows.net"
    "SQL_DATABASE=sqd-capacity-d-01_prd"
)

# ── Preflight checks ──────────────────────────────────────────────────────────
if (-not (Get-Command func -ErrorAction SilentlyContinue)) {
    Write-Error "func not found. Install with: winget install --id Microsoft.Azure.FunctionsCoreTools`nThen restart your terminal."
}

az account set --subscription $SUBSCRIPTION

if ($Mode -ne "deploy") {

    Write-Host "── 1/3  Function App (Flex Consumption + VNet) ──────────────────────"
    az functionapp create `
        --resource-group $RESOURCE_GROUP `
        --name $FUNCTION_APP `
        --storage-account $STORAGE_ACCOUNT `
        --flexconsumption-location $LOCATION `
        --app-insights $APP_INSIGHTS `
        --runtime python `
        --runtime-version $PYTHON_VERSION `
        --subnet $SUBNET_ID

    Write-Host "── 2/3  App Settings ─────────────────────────────────────────────────"
    az functionapp config appsettings set `
        --resource-group $RESOURCE_GROUP `
        --name $FUNCTION_APP `
        --settings @Settings

    Write-Host "── 3/4  Storage Queues ───────────────────────────────────────────────"
    az storage queue create `
        --name "trip-sync-fast-queue" `
        --account-name $STORAGE_ACCOUNT `
        --auth-mode login
    az storage queue create `
        --name "trip-sync-full-queue" `
        --account-name $STORAGE_ACCOUNT `
        --auth-mode login

    Write-Host "── 4/4  Verify VNet integration ──────────────────────────────────────"
    az functionapp vnet-integration list `
        --resource-group $RESOURCE_GROUP `
        --name $FUNCTION_APP
}

Write-Host "── Deploy ────────────────────────────────────────────────────────────"
Set-Location $PSScriptRoot

# NSG outbound TCP/443 from subnet is now open (Globeteam, 2026-06-23)
# WEBSITE_VNET_ROUTE_ALL=1 is safe to keep during deploy — no toggle needed.
func azure functionapp publish $FUNCTION_APP --python

Write-Host ""
Write-Host "Done. Verify at:"
Write-Host "https://portal.azure.com/#resource/subscriptions/62ca3ec4-deec-4283-abbe-029935175c27/resourceGroups/rg-capacity/providers/Microsoft.Web/sites/$FUNCTION_APP/functions"
