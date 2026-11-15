# FixProof backend update

## 1. Keep your API key safe
The uploaded archive contained a `.env` file. This updated package intentionally does not include it.
If that key was real and the archive was shared, revoke it in Google AI Studio and create a replacement.

Copy `.env.example` to `.env`, then set `GEMINI_API_KEY`.

## 2. Install (Windows PowerShell)
From `fixproof/backend`:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
# Edit .env and paste your key
python run.py
```

If Python 3.11 is not installed, use your installed Python 3.10 temporarily:
`python -m venv venv`

## 3. Test
Open `http://127.0.0.1:8000/docs`.
1. Test `GET /health`.
2. Test `POST /scan` with a public URL.
3. Test `POST /audit` with a public URL.

The API score is a custom estimate, not a WCAG certification or conformance score.
Patch verification is performed in an isolated browser session and is not applied to the user's live website.

## Security notes
- Only HTTP/HTTPS on ports 80 and 443 are accepted.
- Browser requests, redirects, and subresources are revalidated.
- DOM patches are declarative and limited to allowlisted operations/tags/attributes.
- Application-level DNS checks cannot fully eliminate DNS rebinding/TOCTOU risk. For public deployment, run browser workers behind network-level egress restrictions or a controlled proxy.
- This is a hackathon prototype, not a production security boundary.
