import sys
sys.path.insert(0, '.')
from fastapi.testclient import TestClient
from app.factory import create_app
from app.repositories.memory import MemoryRepo
from app.services import reports as R
from fpdf import FPDF

orig = FPDF.multi_cell
n = [0]
def traced(self, w, h=None, text="", *a, **k):
    n[0] += 1
    print(f"CALL {n[0]} font={self.font_family}/{self.font_size_pt} x={self.get_x():.1f}: {repr(text)[:80]}")
    return orig(self, w, h, text, *a, **k)
FPDF.multi_cell = traced

repo = MemoryRepo()
c = TestClient(create_app(repo))
iid = c.post("/api/v1/inspections", json={"inspector_id": "D", "inspector_name": "D",
    "business_name": "D", "inspection_date": "2026-09-15"}).json()["inspection_id"]
c.post(f"/api/v1/inspections/{iid}/analyze", json={"product": {
    "product_name": "D", "category": "GENERAL", "is_prepackaged": True}})
data = R.build_report_data(repo, iid)
score = R.finding_score(data["findings"], data["policy"])
try:
    R.render_pdf(data, score)
    print("PDF OK")
except Exception as e:
    print("FAILED:", e)
