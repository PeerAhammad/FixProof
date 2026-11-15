import os, time
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

names = [m.name.replace("models/", "") for m in genai.list_models()
         if "generateContent" in m.supported_generation_methods]
print("Available:", names)

for n in [x for x in names if "flash" in x][:6]:
    t = time.time()
    try:
        r = genai.GenerativeModel(n).generate_content(
            'Return JSON {"ok": true}',
            generation_config={"response_mime_type": "application/json"},
            request_options={"timeout": 30},
        )
        print(f"{n}: {time.time()-t:.1f}s -> {r.text.strip()[:40]}")
    except Exception as e:
        print(f"{n}: FAILED {type(e).__name__}")