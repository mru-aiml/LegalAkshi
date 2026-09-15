/**
 * LegalAkshi backend API client — shapes follow backend/authoritative/openapi.json.
 *
 * Base URL from VITE_API_URL (default http://localhost:8000), API under /api/v1.
 * Existing mock-data UI is untouched; adopt these helpers screen by screen:
 *  1. createInspection  2. addProduct  3. analyze  4-7. findings / score /
 *  violations / review status via the AnalysisResponse + compliance/report calls.
 * All helpers throw on HTTP errors — callers fall back to mock data when the
 * backend is unreachable so the UI keeps working during development.
 */
const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';
export const API_V1 = `${BASE.replace(/\/$/, '')}/api/v1`;

export type InspectionCreate = {
  inspector_id: string;
  inspector_name: string;
  business_name: string;
  inspection_date: string;
  department?: string;
  state?: string;
  district?: string;
  inspection_type?: string;
  location?: string;
};

export type ProductDeclaration = {
  product_name: string;
  category: string;
  is_prepackaged?: boolean;
  brand?: string;
  manufacturer?: string;
  importer?: string;
  country_of_origin?: string;
  subcategory?: string;
  quantity?: number | null;
  quantity_unit?: string;
  quantity_type?: string;
  manufacturing_date?: string;
  best_before?: string;
  imported?: boolean;
  ecommerce?: boolean;
  barcode?: string;
  qr_code?: string;
  source_listing_url?: string;
  [extra: string]: unknown; // manual declarations, e.g. mrp, consumer_care
};

export type Finding = {
  rule_id: string;
  status: 'PASS' | 'FAIL' | 'NEEDS_REVIEW' | 'NOT_APPLICABLE';
  requirement: string;
  evidence: { detected_value?: string | null; confidence?: number | null; confidence_origin?: string };
  explanation: string;
  source_reference?: string;
};

export type AnalysisResponse = {
  inspection_id: string;
  product_id: string;
  status: string;
  score: { value: number; out_of: number; policy: string; policy_version: string; finalizable: boolean };
  findings: Finding[];
  recommendations: string[];
  violation_ids: string[];
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_V1}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status} ${path}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  base: API_V1,
  health: () => req<{ status: string }>('/health'),
  listInspections: () => req<unknown[]>('/inspections'),
  createInspection: (body: InspectionCreate) =>
    req<{ inspection_id: string }>('/inspections', { method: 'POST', body: JSON.stringify(body) }),
  addProduct: (inspectionId: string, decl: ProductDeclaration) =>
    req<{ product_id: string }>(`/inspections/${inspectionId}/products`, {
      method: 'POST',
      body: JSON.stringify(decl),
    }),
  analyze: (inspectionId: string, body: { product?: ProductDeclaration; product_id?: string; as_of_date?: string } = {}) =>
    req<AnalysisResponse>(`/inspections/${inspectionId}/analyze`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  compliance: (inspectionId: string, productId: string) =>
    req<Finding[]>(`/inspections/${inspectionId}/products/${productId}/compliance`),
  rules: () => req<unknown[]>('/rules'),
  ruleDetail: (checkId: string) => req<unknown>(`/rules/${checkId}`),
  violations: (inspectionId: string) =>
    req<{ items: unknown[] }>(`/inspections/${inspectionId}/violations`),
  verifyViolation: (
    violationId: string,
    decision: 'PENDING' | 'CONFIRMED' | 'REJECTED' | 'REQUIRES_REVIEW',
    inspector_id: string,
    verification_notes = '',
  ) =>
    req<{ violation_id: string; inspector_status: string }>(`/violations/${violationId}/verify`, {
      method: 'POST',
      body: JSON.stringify({ decision, inspector_id, verification_notes }),
    }),
  report: (inspectionId: string) => req<unknown>(`/reports/${inspectionId}`),
};
