# 🎬 BMS IMAX Odyssey Watcher

Automatically checks [PVR Palladium Mall, Ahmedabad](https://in.bookmyshow.com/cinemas/ahmedabad/pvr-palladium-mall-ahmedabad/buytickets/PPAM/20260801)
for **The Odyssey (IMAX)** tickets on **Saturday, 1 August 2026** and emails everyone on your list the moment they go live.

---

## 📋 Quick Setup (5 minutes)

### Step 1 — Get a Gmail App Password

> Your watcher needs a Gmail account to send alerts.
> **Do NOT use your real password** — use an App Password instead.

1. Go to **[myaccount.google.com](https://myaccount.google.com)**
2. **Security** → **2-Step Verification** (enable if not already on)
3. Scroll down → **App Passwords**
4. App = **Mail**, Device = **Other** → name it `BMS Watcher`
5. Copy the **16-character code** (e.g. `abcd efgh ijkl mnop`)

---

### Step 2 — Fill in `.env`

Open `.env` in Notepad and fill in your details:

```env
SENDER_EMAIL=yourgmail@gmail.com
SENDER_APP_PASSWORD=abcd efgh ijkl mnop
RECIPIENT_EMAILS=you@gmail.com,friend1@gmail.com,friend2@gmail.com
CHECK_INTERVAL_MINUTES=30
```

---

### Step 3 — Test the email

Double-click **`send_test_email.bat`**

You should receive a test alert in your inbox within ~1 minute.

---

## Option A — Run Locally (laptop must stay on)

Double-click **`run_watcher.bat`**

- Sets up Python virtual environment automatically
- Installs all dependencies
- Starts checking every 30 minutes
- Logs everything to `watcher.log`
- Press Ctrl+C to stop

---

## Option B — GitHub Actions (FREE, no laptop needed!)

Run on GitHub servers for free. Monitor from your phone via the GitHub mobile app.

### One-time setup:

#### 1. Create a private GitHub repo

Go to github.com/new, name it `bms-watcher`, set to Private, then Create.

#### 2. Push this code

Open PowerShell in this folder (scraping102) and run:

```powershell
git init
git add .
git commit -m "Initial BMS watcher setup"
git remote add origin https://github.com/YOUR_USERNAME/bms-watcher.git
git push -u origin main
```

Replace YOUR_USERNAME with your GitHub username.

#### 3. Add your secrets to GitHub

Go to your repo -> Settings -> Secrets and variables -> Actions -> New repository secret

Add these 3 secrets:

| Secret Name | Value |
|---|---|
| SENDER_EMAIL | yourgmail@gmail.com |
| SENDER_APP_PASSWORD | abcd efgh ijkl mnop |
| RECIPIENT_EMAILS | you@gmail.com,friend1@gmail.com |

#### 4. Enable Actions

Go to your repo -> Actions tab -> click "I understand my workflows, go ahead and enable them"

That's it! GitHub checks every 30 minutes automatically.

---

## Monitor from your Phone

1. Install GitHub Mobile (iOS / Android)
2. Open your bms-watcher repo -> Actions
3. Each run shows: green = tickets found, yellow/grey = not yet
4. Trigger a manual check anytime: Actions -> BMS Odyssey IMAX Watcher -> Run workflow

---

## File Structure

```
scraping102/
├── watcher.py              # Main script
├── requirements.txt        # Python dependencies
├── .env                    # Your secrets (never committed!)
├── .gitignore              # Protects .env from git
├── run_watcher.bat         # One-click local launcher (Windows)
├── send_test_email.bat     # Send a test email immediately
├── watcher.log             # Check history (auto-created)
└── .github/
    └── workflows/
        └── watch.yml       # GitHub Actions schedule
```
