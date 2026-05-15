# ============================================================
#  Baseline — Complete Deployment Script
#  Run this in a PowerShell terminal: .\deploy.ps1
# ============================================================

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  BASELINE DEPLOYMENT SCRIPT" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Railway Login ──────────────────────────────────
Write-Host "STEP 1: Railway Login" -ForegroundColor Yellow
Write-Host "A browser window will open — log in with your Railway account."
Write-Host ""
railway login
if ($LASTEXITCODE -ne 0) { Write-Host "Railway login failed. Exiting." -ForegroundColor Red; exit 1 }
Write-Host "Railway login successful." -ForegroundColor Green

# ── Step 2: Deploy Backend to Railway ─────────────────────
Write-Host ""
Write-Host "STEP 2: Deploying backend to Railway..." -ForegroundColor Yellow
Set-Location "E:\baseline\backend"

# Init Railway project linked to backend folder
railway init --name baseline-backend 2>&1 | Write-Host

# Deploy
Write-Host "Uploading backend (this takes 2-4 minutes)..."
railway up --detach 2>&1 | Write-Host

# ── Step 3: Set Railway Environment Variables ──────────────
Write-Host ""
Write-Host "STEP 3: Setting Railway environment variables..." -ForegroundColor Yellow

railway variables set APP_PASSWORD=apples123
railway variables set PROXY_HOST=gate.decodo.com
railway variables set "PROXY_PORT_LIST=10004,10005,10006,10007,10008,10009,10010"
railway variables set PROXY_USERNAME=spywgc898n
railway variables set "PROXY_PASSWORD=t~vuxG9qoX1yP19seU"
railway variables set "MATCHSTAT_API_KEY=9d67382466msh1ab70a329fbf230p1ba6bcjsn4b3da85bddc6"

Write-Host "Environment variables set." -ForegroundColor Green

# ── Step 4: Get Railway URL ────────────────────────────────
Write-Host ""
Write-Host "STEP 4: Getting Railway public URL..." -ForegroundColor Yellow
Start-Sleep -Seconds 10  # give Railway a moment

$railwayUrl = railway domain 2>&1
Write-Host "Raw railway domain output: $railwayUrl"

# If no domain yet, generate one
if ($railwayUrl -notmatch "https://") {
    Write-Host "Generating Railway domain..."
    railway domain generate 2>&1 | Write-Host
    Start-Sleep -Seconds 5
    $railwayUrl = railway domain 2>&1
}

# Extract URL
if ($railwayUrl -match "(https://[^\s]+)") {
    $RAILWAY_URL = $matches[1].TrimEnd("/")
    Write-Host "Railway URL: $RAILWAY_URL" -ForegroundColor Green
} else {
    Write-Host "Could not auto-detect Railway URL." -ForegroundColor Red
    $RAILWAY_URL = Read-Host "Paste your Railway public URL (from railway.app dashboard)"
    $RAILWAY_URL = $RAILWAY_URL.TrimEnd("/")
}

# ── Step 5: Update CORS in main.py ────────────────────────
Write-Host ""
Write-Host "STEP 5: Updating backend CORS with Vercel URL..." -ForegroundColor Yellow
$VERCEL_URL = "https://baseline-app-three.vercel.app"
$mainPy = Get-Content "E:\baseline\backend\main.py" -Raw
$newCors = $mainPy -replace '"[*]"', "`"$VERCEL_URL`", `"https://baseline-fmzu5s8rl-shawnf1213-4230s-projects.vercel.app`""
Set-Content "E:\baseline\backend\main.py" $newCors
Write-Host "CORS updated." -ForegroundColor Green

# ── Step 6: Redeploy backend with CORS fix ─────────────────
Write-Host ""
Write-Host "STEP 6: Redeploying backend with CORS fix..." -ForegroundColor Yellow
Set-Location "E:\baseline\backend"
railway up --detach 2>&1 | Write-Host
Write-Host "Backend redeployment triggered." -ForegroundColor Green

# ── Step 7: Update Vercel with Railway URL ─────────────────
Write-Host ""
Write-Host "STEP 7: Updating Vercel with Railway backend URL..." -ForegroundColor Yellow
Set-Location "E:\baseline\frontend"

# Remove old placeholder if exists
vercel env rm VITE_API_URL production --yes 2>&1 | Out-Null

# Set real Railway URL
echo $RAILWAY_URL | vercel env add VITE_API_URL production
Write-Host "Vercel VITE_API_URL set to: $RAILWAY_URL" -ForegroundColor Green

# ── Step 8: Redeploy Vercel ────────────────────────────────
Write-Host ""
Write-Host "STEP 8: Redeploying Vercel frontend with Railway URL..." -ForegroundColor Yellow
vercel --prod --yes 2>&1 | Select-Object -Last 10

# ── Step 9: GitHub (optional) ─────────────────────────────
Write-Host ""
Write-Host "STEP 9: GitHub repository setup..." -ForegroundColor Yellow
$doGitHub = Read-Host "Push to GitHub? (y/n)"
if ($doGitHub -eq "y") {
    $ghUser = Read-Host "Enter your GitHub username"
    Write-Host "Opening GitHub to create repo — create a public repo named 'baseline', then press Enter here."
    Start-Process "https://github.com/new"
    Read-Host "Press Enter after creating the GitHub repo"
    Set-Location "E:\baseline"
    git remote add origin "https://github.com/$ghUser/baseline.git"
    git branch -M main
    git push -u origin main
    Write-Host "Pushed to GitHub: https://github.com/$ghUser/baseline" -ForegroundColor Green
}

# ── Step 10: Test backend ──────────────────────────────────
Write-Host ""
Write-Host "STEP 10: Testing backend health..." -ForegroundColor Yellow
Start-Sleep -Seconds 15
try {
    $resp = Invoke-RestMethod -Uri "$RAILWAY_URL/" -TimeoutSec 30
    Write-Host "Backend response: $($resp | ConvertTo-Json)" -ForegroundColor Green
} catch {
    Write-Host "Backend not ready yet — check Railway dashboard in 2 minutes." -ForegroundColor Yellow
}

# ── Done ───────────────────────────────────────────────────
Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  PUBLIC URL  : https://baseline-app-three.vercel.app" -ForegroundColor White
Write-Host "  PASSWORD    : apples123" -ForegroundColor White
Write-Host "  BACKEND     : $RAILWAY_URL" -ForegroundColor White
Write-Host ""
Write-Host "  ACCESS FROM PHONE  : Open https://baseline-app-three.vercel.app" -ForegroundColor Gray
Write-Host "  SHARE WITH OTHERS  : Send URL + password apples123" -ForegroundColor Gray
Write-Host ""
Write-Host "  CHANGE PASSWORD IN FUTURE:" -ForegroundColor Gray
Write-Host "    1. Go to vercel.com → baseline-app → Settings → Environment Variables" -ForegroundColor Gray
Write-Host "    2. Find VITE_APP_PASSWORD → update value → Save" -ForegroundColor Gray
Write-Host "    3. Go to Deployments → Redeploy latest" -ForegroundColor Gray
Write-Host ""
