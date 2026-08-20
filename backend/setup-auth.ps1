# ============================================================
#  Baseline — Discord sign-in + Stripe billing setup
#
#  Run from E:\baseline\backend:   .\setup-auth.ps1
#
#  Secrets are typed into YOUR terminal and go straight to Railway.
#  Nothing is echoed, nothing is written to a file, and nothing passes
#  through a chat transcript. Press Enter on any prompt to skip that
#  variable and leave whatever Railway already has.
# ============================================================

$ErrorActionPreference = "Stop"

function Read-Secret([string]$Label, [string]$Hint) {
    Write-Host ""
    Write-Host $Label -ForegroundColor Cyan
    if ($Hint) { Write-Host "  $Hint" -ForegroundColor DarkGray }
    $secure = Read-Host "  value (blank = skip)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try   { return [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Read-Plain([string]$Label, [string]$Hint) {
    Write-Host ""
    Write-Host $Label -ForegroundColor Cyan
    if ($Hint) { Write-Host "  $Hint" -ForegroundColor DarkGray }
    return (Read-Host "  value (blank = skip)")
}

# Long random values we can generate rather than make you invent.
function New-Secret([int]$Bytes = 32) {
    $b = New-Object byte[] $Bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return -join ($b | ForEach-Object { $_.ToString("x2") })
}

Write-Host "======================================================" -ForegroundColor Green
Write-Host "  BASELINE — AUTH + BILLING SETUP" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Before you start, have these open:" -ForegroundColor Yellow
Write-Host "  Discord dev portal : https://discord.com/developers/applications"
Write-Host "  Stripe dashboard   : https://dashboard.stripe.com/test/apikeys"
Write-Host ""
Write-Host "In the Discord portal, under OAuth2 -> Redirects, add EXACTLY:" -ForegroundColor Yellow
Write-Host "  https://backend-production-84ab.up.railway.app/api/auth/callback" -ForegroundColor White
Write-Host ""
Write-Host "In Stripe, create two RECURRING prices (weekly + monthly) and copy" -ForegroundColor Yellow
Write-Host "each price_... id. Start with TEST keys (sk_test_...)." -ForegroundColor Yellow
Write-Host ""

$vars = @{}

# ── Discord ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "--- DISCORD SIGN-IN -----------------------------------" -ForegroundColor Magenta

$v = Read-Plain  "DISCORD_CLIENT_ID"     "Dev portal -> your app -> OAuth2 -> Client ID"
if ($v) { $vars["DISCORD_CLIENT_ID"] = $v }

$v = Read-Secret "DISCORD_CLIENT_SECRET" "OAuth2 -> Client Secret (Reset Secret if never shown)"
if ($v) { $vars["DISCORD_CLIENT_SECRET"] = $v }

$v = Read-Secret "DISCORD_BOT_TOKEN"     "Bot -> Token. The SAME token the bot service already uses."
if ($v) { $vars["DISCORD_BOT_TOKEN"] = $v }

$v = Read-Plain  "DISCORD_GUILD_ID"      "Right-click your server -> Copy Server ID (needs Developer Mode on)"
if ($v) { $vars["DISCORD_GUILD_ID"] = $v }

$v = Read-Plain  "DISCORD_PREMIUM_ROLE_IDS" "Right-click the premium role -> Copy Role ID. Comma-separate for several."
if ($v) { $vars["DISCORD_PREMIUM_ROLE_IDS"] = $v }

$v = Read-Plain  "BILLING_OWNER_DISCORD_IDS" "YOUR Discord user id — this is what gives you access with no paywall, ever."
if ($v) { $vars["BILLING_OWNER_DISCORD_IDS"] = $v }

$vars["DISCORD_REDIRECT_URI"] = "https://backend-production-84ab.up.railway.app/api/auth/callback"

# ── Stripe ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "--- STRIPE --------------------------------------------" -ForegroundColor Magenta

$v = Read-Secret "STRIPE_SECRET_KEY"     "Stripe -> Developers -> API keys -> Secret key (sk_test_... to start)"
if ($v) { $vars["STRIPE_SECRET_KEY"] = $v }

$v = Read-Secret "STRIPE_WEBHOOK_SECRET" "Webhooks -> your endpoint -> Signing secret (whsec_...)"
if ($v) { $vars["STRIPE_WEBHOOK_SECRET"] = $v }

$v = Read-Plain  "STRIPE_PRICE_WEEKLY"   "The weekly recurring price id (price_...)"
if ($v) { $vars["STRIPE_PRICE_WEEKLY"] = $v }

$v = Read-Plain  "STRIPE_PRICE_MONTHLY"  "The monthly recurring price id (price_...)"
if ($v) { $vars["STRIPE_PRICE_MONTHLY"] = $v }

# ── Generated secrets ────────────────────────────────────────────────────
Write-Host ""
Write-Host "--- GENERATED ------------------------------------------" -ForegroundColor Magenta
Write-Host "Session-signing and sync secrets are generated here rather than"
Write-Host "invented by hand. Regenerating APP_SESSION_SECRET later signs"
Write-Host "everyone out, which is also how you force a global sign-out."
$vars["APP_SESSION_SECRET"] = New-Secret 32
$vars["BILLING_SYNC_TOKEN"]  = New-Secret 24
$vars["BILLING_OWNER_TOKEN"] = New-Secret 32
Write-Host "  APP_SESSION_SECRET   generated" -ForegroundColor Green
Write-Host "  BILLING_SYNC_TOKEN   generated" -ForegroundColor Green
Write-Host "  BILLING_OWNER_TOKEN  generated" -ForegroundColor Green

# ── Apply ────────────────────────────────────────────────────────────────
if ($vars.Count -eq 0) { Write-Host "Nothing to set." -ForegroundColor Yellow; exit 0 }

Write-Host ""
Write-Host "Setting $($vars.Count) variable(s) on the backend service..." -ForegroundColor Yellow

$cliArgs = @()
foreach ($k in $vars.Keys) { $cliArgs += "--set"; $cliArgs += "$k=$($vars[$k])" }
& railway variables @cliArgs | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "railway variables failed. Are you linked to baseline-backend?" -ForegroundColor Red
    Write-Host "Run 'railway status' from E:\baseline\backend to check." -ForegroundColor Red
    exit 1
}
Write-Host "Variables set. Railway is redeploying." -ForegroundColor Green

# ── Verify (presence only — these endpoints never echo a value) ──────────
Write-Host ""
Write-Host "Waiting for the redeploy, then verifying..." -ForegroundColor Yellow
Start-Sleep -Seconds 90
foreach ($path in @("/api/auth/config", "/api/billing/config")) {
    try {
        $r = Invoke-RestMethod -Uri "https://backend-production-84ab.up.railway.app$path" -TimeoutSec 40
        $ok = if ($r.ready) { "READY" } else { "NOT READY" }
        $color = if ($r.ready) { "Green" } else { "Red" }
        Write-Host ""
        Write-Host "$path -> $ok" -ForegroundColor $color
        $r | Format-List | Out-String | Write-Host
    } catch {
        Write-Host "$path -> could not reach (may still be deploying)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "If both say READY, open the app and the landing screen goes live." -ForegroundColor Green
Write-Host "Test a purchase with card 4242 4242 4242 4242, any future expiry." -ForegroundColor Green
