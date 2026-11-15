import asyncio
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from scanner import scan_url
from security import UnsafeURL
from gemini_client import analyze_violations
from verifier import verify_groups

load_dotenv()

app = FastAPI(
    title="FixProof API",
    version="1.0.0",
    description="AI-assisted accessibility auditing with isolated DOM patch verification.",
)

origins = [
    item.strip()
    for item in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

scan_lock = asyncio.Semaphore(1)
audit_lock = asyncio.Semaphore(1)


class ScanRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    violations: list[dict] = Field(default_factory=list, max_length=100)


@app.get("/")
async def root():
    return {"name": "FixProof API", "docs": "/docs", "version": app.version}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "gemini_configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
    }


@app.post("/scan")
async def scan(req: ScanRequest):
    async with scan_lock:
        try:
            return await asyncio.wait_for(scan_url(req.url), timeout=60)
        except UnsafeURL as exc:
            raise HTTPException(400, str(exc))
        except asyncio.TimeoutError:
            raise HTTPException(504, "Website scan timed out.")
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    try:
        return await asyncio.wait_for(
            analyze_violations(req.url, req.violations), timeout=90
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "AI analysis timed out.")
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


@app.post("/audit")
async def audit(req: ScanRequest):
    async with audit_lock:
        async with scan_lock:
            try:
                result = await asyncio.wait_for(scan_url(req.url), timeout=60)
            except UnsafeURL as exc:
                raise HTTPException(400, str(exc))
            except asyncio.TimeoutError:
                raise HTTPException(504, "Website scan timed out.")
            except RuntimeError as exc:
                raise HTTPException(502, str(exc))

        result["groups"] = []
        result["after_score"] = None
        result["warning"] = None

        if not result["violations"]:
            result["warning"] = "No axe-core violations were found; AI analysis was not needed."
            return result

        try:
            ai = await asyncio.wait_for(
                analyze_violations(req.url, result["violations"]), timeout=90
            )
            result["groups"] = ai.get("groups", [])
        except Exception as exc:
            result["warning"] = (
                f"Scan completed, but AI analysis failed: "
                f"{type(exc).__name__}: {str(exc)[:220]}"
            )
            return result

        if not result["groups"]:
            result["warning"] = "AI returned no usable, scan-grounded fix groups."
            return result

        try:
            verified = await asyncio.wait_for(
                verify_groups(
                    req.url,
                    result["groups"],
                    result["violations"],
                    max_patches=10,
                    budget_s=45,
                ),
                timeout=55,
            )
            result["groups"] = verified["groups"]
            result["after_score"] = verified["after_score"]
        except Exception as exc:
            for group in result["groups"]:
                for patch in group.get("patches", []):
                    patch["status"] = "skipped"
                    patch["detail"] = "Verification did not complete."
            result["warning"] = (
                f"AI analysis completed, but verification failed: "
                f"{type(exc).__name__}: {str(exc)[:180]}"
            )
        return result
