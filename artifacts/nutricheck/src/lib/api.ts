/**
 * LegalAkshi backend API client — shapes follow backend/authoritative/openapi.json.
 *
 * Base URL from VITE_API_URL (artifacts/nutricheck/.env.local), API under
 * /api/v1. Falls back to the documented backend default http://localhost:8000
 * only when VITE_API_URL is unset — never hard-coded per component.
 * Authentication: when a token provider is configured (Clerk session JWT),
 * requests carry `Authorization: Bearer`. Local-dev role headers
 * (X-LegalAkshi-Role) are sent ONLY when VITE_DEMO_ROLE_SWITCH === 'true'
 * and an explicit dev role is passed — never self-asserted in production.
 * The backend is the final authorization authority (401/403).
 */
const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';
export const API_V1 = `${BASE.replace(/\/$/, '')}/api/v1`;

const DEV_HEADERS_ENABLED =
  (import.meta.env.VITE_DEMO_ROLE_SWITCH as string | undefined) === 'true';

type TokenProvider = () => Promise<string | null>;
let tokenProvider: TokenProvider | null = null;

export function setAuthTokenProvider(fn: TokenProvider | null): void {
  tokenProvider = fn;
}

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
  // Inspection context: PACKAGE_ONLY | ONLINE_LISTING |
  // PACKAGE_AND_ONLINE_LISTING. E-commerce checks require online-listing
  // evidence (listing URL / screenshot) before any FAIL.
  inspection_context?: string;
  online_listing_url?: string;
  food?: boolean;
  fssai_license?: string;
  ingredients_raw?: string;
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

export type BackendRule = {
  rule_id: string;
  rule_number: string;
  title: string;
  field: string;
  check_type: string;
  mandatory_default?: boolean;
  requirement: string;
  source_reference: string;
};

export type Complaint = {
  complaint_id: string;
  reporter_id: string;
  product_name: string;
  retailer: string;
  city: string;
  severity: string;
  description: string;
  status: string;
  inspection_id?: string | null;
  violation_id?: string | null;
  created_at?: string;
  updated_at?: string;
  timeline?: { event_type: string; from_status?: string | null; to_status?: string | null; actor_id?: string; note?: string; created_at?: string }[];
};

export type OfficerStats = {
  awaiting_review: number;
  high_priority: number;
  action_taken: number;
  resolved: number;
  total_violations: number;
  resolution_rate: number;
  avg_response_days: number | null;
  active_rules: number;
  open_complaints: number;
  total_complaints: number;
};

export type AuthOptions = {
  /** Local-dev role only; honored solely when VITE_DEMO_ROLE_SWITCH is on. */
  devRole?: 'consumer' | 'officer' | 'admin';
  /** Local-dev user id (X-LegalAkshi-User); likewise dev-only. */
  devUser?: string;
};

export type OcrFieldStatus = 'DETECTED' | 'NEEDS_REVIEW' | 'NOT_DETECTED';
export type OcrEvidenceSource = { image?: string | null; image_index?: number | null; confidence?: number | null; box?: unknown; value?: string | null; unit?: string | null };
export type OcrField = { value: string | null; provenance: string; confidence: number | null; image?: string | null; image_index?: number | null; status?: OcrFieldStatus; box?: unknown; sources?: OcrEvidenceSource[] };
// Stage 2B: server-side OCR + Vision reconciliation attached to the OCR
// response (NEW keys only; legacy fields are byte-identical).
export type ReconciledField = {
  field: string; final_value: string | null; unit?: string | null;
  status: OcrFieldStatus; confidence: number; sources: string[];
  candidates: { source: string; value: string; confidence?: number | null }[];
  agreement?: string; evidence?: (string | null)[];
  needs_review_reason?: string; provider?: string | null; model?: string | null;
  region?: string | null; region_image?: string | null;
};
export type VisionDiagnostics = {
  vision_enabled: boolean; vision_provider?: string | null;
  vision_model?: string | null; vision_status?: string;
  vision_status_detail?: 'active' | 'partial' | 'unavailable' | string;
  vision_error?: string | null; vision_calls?: number;
  vision_latency_ms?: number | null; ocr_calls?: number;
  ocr_ms?: number | null; reconciliation_ms?: number | null;
  fields_requested?: string[]; fields_returned?: string[];
  fields?: { detected?: number; needs_review?: number; not_detected?: number };
};
export type OcrResponse = {
  status: 'OK' | 'NEEDS_REVIEW';
  engine: string;
  front: { text: string; lines: { text: string; confidence: number }[]; error?: string | null };
  back: { text: string; lines: { text: string; confidence: number }[]; error?: string | null } | null;
  fields: Record<string, OcrField>;
  errors: string[];
  images?: { image: string; image_index: number; text: string; variants?: string[]; error?: string | null }[];
  images_analyzed?: number;
  timings?: { total_ms?: number; provider_calls?: number; stage1_ms?: number; ingredient_ms?: number; declaration_ms?: number; nutrition_ms?: number; symbol_ms?: number; reconciliation_ms?: number; images?: Record<string, { image_ms?: number; stage1_ms?: number; orientation?: string; image_quality?: Record<string, string | number | null>; regions?: { kind: string; ms: number; lines: number }[] }> };
  food?: { status: string; provenance: string; fields: Record<string, { value: unknown; confidence: number | null; provenance: string; detection?: string }>; timings?: { ingredients_ms?: number; nutrition_ms?: number } };
  veg_nonveg_symbol?: { status: 'DETECTED' | 'NOT_DETECTED' | 'NEEDS_REVIEW'; classification: 'VEGETARIAN' | 'NON_VEGETARIAN' | 'UNKNOWN'; confidence: number | null; provenance: string; reason?: string };
  // Stage 2B: present when the backend ran the vision stage (or its
  // OCR-only fallback). Absent on older backends — callers must cope.
  vision?: VisionDiagnostics;
  reconciliation?: {
    fields: Record<string, ReconciledField>;
    readiness?: { ready: boolean; blocked_fields: string[] };
    dropped_vision?: { field: string; value: string | null; reason: string }[];
  };
};
export type FoodIngredientItem = { ingredient?: string; name?: string; status?: string; percentage?: string | null; ins_number?: string | null; reason?: string; confidence?: number | null };
export type IngredientRejectedLine = { text?: string; reason?: string; confidence?: number | null; source_box?: unknown };
export type FoodIngredients = { raw_text?: string | null; cleaned_text?: string | null; confidence?: number | null; provenance?: string; ocr_source?: string; detection?: string; coherence?: { score?: number }; cleaning_notes?: string[]; parsed_items?: { name?: string }[]; analysis?: { ingredients?: FoodIngredientItem[] }; accepted_lines?: string[]; rejected_lines?: IngredientRejectedLine[]; heading?: string | null };
export type VerificationStatus = 'VERIFIED' | 'NEEDS_REVIEW' | 'NOT_VERIFIED';
export type VerificationMatch = {
  product_id: string; product_name?: string | null; brand?: string | null;
  manufacturer?: string | null; pack_size?: string | null; barcode?: string | null;
  status: VerificationStatus; reason?: string;
  inspection_date?: string; inspection_type?: string; inspection_id?: string;
};
export type DeclarationRow = { label: string; state: 'Verified' | 'Not verified' | 'Needs review' };
export type VerificationDetail = VerificationMatch & {
  not_verified_note?: string | null;
  batch_lot?: string | null;
  declarations: DeclarationRow[];
  review?: { reviewed: boolean; action: string; reviewed_by: string; reviewed_on?: string | null; note: string } | null;
};
export type Nutrient = { name: string; value: string; about: string };
export type NutritionInfo = {
  product_id: string; product_name?: string | null; available: boolean;
  nutrients: Nutrient[]; note?: string | null;
};
export type ConsumerOverview = {
  products_checked: number; verified_products: number;
  complaints_raised: number; open_complaints: number;
  recent_checks: VerificationMatch[];
};
export type ConsumerReport = {
  complaint_id: string; product_name?: string | null; barcode?: string | null;
  date_submitted?: string; last_update?: string | null; status: string;
  description?: string | null;
  related_inspection?: {
    inspection_id: string; inspection_date?: string; inspection_type?: string;
    status: VerificationStatus; report_available: boolean;
    outcome: { pass: number; fail: number; review: number };
    product: VerificationMatch;
  } | null;
  state_note?: string | null;
};
export type ConsumerReportDetail = ConsumerReport & {
  timeline: { event_type: string; from_status?: string | null; to_status?: string | null; note?: string; created_at?: string }[];
  declarations: { label: string; state: string }[];
};
export type Suggestion = {
  suggestion_id: string; title: string; category: string; description?: string | null;
  context?: string | null; location?: string | null; status: string;
  officer_note?: string | null; created_at?: string; updated_at?: string | null;
  consumer?: string; reviewed_by?: string | null;
  timeline?: { event_type: string; from_status?: string | null; to_status?: string | null; note?: string; created_at?: string }[];
};
export type BackendNotification = {
  notification_id: string;
  audience: string;
  type: string;
  title: string;
  body: string;
  link: string;
  read: boolean;
  created_at?: string;
};

/** Ordinary-read timeout: slow reads fail fast into a retryable error
 *  instead of hanging the UI forever (Neon cold starts can stall a first
 *  query). Long-running calls (OCR extract, analysis, PDF bytes) bypass
 *  this via `longRunning: true`. */
export const READ_TIMEOUT_MS = 12000;
export const LONG_TIMEOUT_MS = 180000;

class TimeoutError extends Error {
  constructor(path: string, ms: number) {
    super(`API timeout ${path} after ${Math.round(ms / 1000)}s — the server may be waking up; please retry.`);
    this.name = 'TimeoutError';
  }
}

/** In-flight GET coalescing: concurrent mounts/navigation asking for the
 *  same URL share one network request instead of duplicating it. */
const inflight = new Map<string, Promise<unknown>>();

/** Tiny TTL cache for safe read-heavy UI data (rules, profile, overview
 *  summary). Never used for decisions, transitions, or evaluations —
 *  those always fetch fresh. TTLs are short; callers needing freshness
 *  pass `noCache: true`. */
const ttlCache = new Map<string, { at: number; data: unknown }>();
const TTL_BY_PREFIX: [string, number][] = [
  ['/rules', 90000],
  ['/officer/profile', 60000],
  ['/consumer/overview', 20000],
];
function ttlFor(path: string): number {
  for (const [prefix, ms] of TTL_BY_PREFIX) {
    if (path === prefix || path.startsWith(`${prefix}?`)) return ms;
  }
  return 0;
}
export function clearApiCache(path?: string): void {
  if (!path) {
    ttlCache.clear();
    return;
  }
  for (const key of [...ttlCache.keys()]) {
    if (key === path || key.startsWith(`${path}?`)) ttlCache.delete(key);
  }
}

type ReqOptions = { timeoutMs?: number; longRunning?: boolean; noCache?: boolean };

async function req<T>(path: string, init?: RequestInit, auth?: AuthOptions, opts: ReqOptions = {}): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase();
  const isGet = method === 'GET';
  // The 12s ordinary-read timeout applies to GET reads only. Mutations
  // (POST/PUT/PATCH/DELETE) run multi-query Neon transactions that can
  // legitimately exceed 12s on a cold database — aborting those client-side
  // produced "API timeout .../products" on real inspections. They share the
  // bounded long-running budget instead (never infinite).
  const timeoutMs = (opts.longRunning || !isGet) ? LONG_TIMEOUT_MS : (opts.timeoutMs ?? READ_TIMEOUT_MS);
  if (isGet && !opts.noCache) {
    const ttl = ttlFor(path);
    if (ttl > 0) {
      const hit = ttlCache.get(path);
      if (hit && Date.now() - hit.at < ttl) return hit.data as T;
    }
    const pending = inflight.get(path);
    if (pending) return pending as Promise<T>;
  }
  const run = (async (): Promise<T> => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (tokenProvider) {
      try {
        const token = await tokenProvider();
        if (token) headers.Authorization = `Bearer ${token}`;
      } catch {
        /* proceed unauthenticated; backend decides */
      }
    }
    if (DEV_HEADERS_ENABLED && auth?.devRole) {
      headers['X-LegalAkshi-Role'] = auth.devRole;
      if (auth.devUser) headers['X-LegalAkshi-User'] = auth.devUser;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    let res: Response;
    try {
      res = await fetch(`${API_V1}${path}`, {
        headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) },
        ...init,
        signal: ctrl.signal,
      });
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === 'AbortError') throw new TimeoutError(path, timeoutMs);
      // Raw fetch network failures surface as an opaque "Failed to fetch"
      // TypeError. Map it to actionable guidance (API URL / CORS / server)
      // instead of leaving officers and consumers guessing.
      if (e instanceof TypeError) {
        throw new Error(`Cannot reach the LegalAkshi backend at ${API_V1}${path} (network, CORS, or server down). Check the API URL and that the backend is running.`);
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`API ${res.status} ${path}: ${text}`);
    }
    const data = await res.json() as T;
    if (isGet && !opts.noCache && ttlFor(path) > 0) ttlCache.set(path, { at: Date.now(), data });
    return data;
  })();
  if (isGet && !opts.noCache) {
    inflight.set(path, run);
    run.then(
      () => { if (inflight.get(path) === run) inflight.delete(path); },
      () => { if (inflight.get(path) === run) inflight.delete(path); },
    );
  }
  return run;
}

export const api = {
  base: API_V1,
  devHeadersEnabled: DEV_HEADERS_ENABLED,
  health: () => req<{ status: string }>('/health'),
  listInspections: () => req<unknown[]>('/inspections'),
  getInspection: (id: string) => req<unknown>(`/inspections/${id}`),
  createInspection: (body: InspectionCreate, auth?: AuthOptions) =>
    req<{ inspection_id: string }>('/inspections', { method: 'POST', body: JSON.stringify(body) }, auth),
  addProduct: (inspectionId: string, decl: ProductDeclaration, auth?: AuthOptions) =>
    req<{ product_id: string }>(`/inspections/${inspectionId}/products`, {
      method: 'POST',
      body: JSON.stringify(decl),
    }, auth),
  analyze: (inspectionId: string, body: { product?: ProductDeclaration; product_id?: string; as_of_date?: string } = {}, auth?: AuthOptions) =>
    req<AnalysisResponse>(`/inspections/${inspectionId}/analyze`, {
      method: 'POST',
      body: JSON.stringify(body),
    }, auth, { longRunning: true }),
  // Analysis requirements: which review fields the applicable checks
  // need, derived server-side from the Rule Engine (read-only).
  analysisRequirements: (params: { food?: boolean; imported?: boolean; ecommerce?: boolean; category?: string; quantity_type?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.food !== undefined) q.set('food', String(params.food));
    if (params.imported !== undefined) q.set('imported', String(params.imported));
    if (params.ecommerce !== undefined) q.set('ecommerce', String(params.ecommerce));
    if (params.category) q.set('category', params.category);
    if (params.quantity_type) q.set('quantity_type', params.quantity_type);
    const qs = q.toString();
    return req<{
      required: { field: string; ocr_key: string | null; form_key: string | null; checks: { check_id: string; title: string; requirement: string }[]; reasons: string[] }[];
      optional: { field: string; ocr_key: string | null; form_key: string | null; checks: { check_id: string; title: string; requirement: string }[]; reasons: string[] }[];
    }>(`/analysis/requirements${qs ? `?${qs}` : ''}`);
  },
  compliance: (inspectionId: string, productId: string) =>
    req<Finding[]>(`/inspections/${inspectionId}/products/${productId}/compliance`),
  rules: () => req<BackendRule[]>('/rules'),
  ruleDetail: (checkId: string) => req<{
    check: BackendRule;
    versions: { rule_version_id: string; requirement: string; legal_text_or_paraphrase?: string; effective_from: string; effective_to?: string | null; status: string }[];
    applicability: { condition_expression?: unknown; applicable_result: string; reason: string }[];
  }>(`/rules/${checkId}`),
  violations: (inspectionId: string) =>
    req<{ items: unknown[] }>(`/inspections/${inspectionId}/violations`),
  verifyViolation: (
    violationId: string,
    decision: 'PENDING' | 'CONFIRMED' | 'REJECTED' | 'REQUIRES_REVIEW',
    inspector_id: string,
    verification_notes = '',
    auth?: AuthOptions,
  ) =>
    req<{ violation_id: string; inspector_status: string }>(`/violations/${violationId}/verify`, {
      method: 'POST',
      body: JSON.stringify({ decision, inspector_id, verification_notes }),
    }, auth),
  // Report JSON aggregates findings + scoring server-side; like the PDF
  // bytes it is long-running work, not an ordinary read.
  report: (inspectionId: string) => req<unknown>(`/reports/${inspectionId}`, undefined, undefined, { longRunning: true }),
  reportPdf: async (inspectionId: string): Promise<Blob> => {
    const headers: Record<string, string> = {};
    if (tokenProvider) {
      try {
        const token = await tokenProvider();
        if (token) headers.Authorization = `Bearer ${token}`;
      } catch {
        /* proceed unauthenticated */
      }
    }
    // Long-running by nature (report rendering); never the 12s read timeout.
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), LONG_TIMEOUT_MS);
    let res: Response;
    try {
      res = await fetch(`${API_V1}/reports/${inspectionId}?format=pdf`, { headers, signal: ctrl.signal });
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        throw new Error(`Report download timed out — please retry.`);
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`API ${res.status} /reports/${inspectionId}: ${text}`);
    }
    return res.blob();
  },
  // --- complaints (consumer intake lifecycle) ---
  createComplaint: (body: { product_name: string; retailer: string; city: string; severity?: string; description?: string; inspection_id?: string; violation_id?: string; evidence?: unknown[] }) =>
    req<Complaint>('/complaints', { method: 'POST', body: JSON.stringify(body) }),
  listComplaints: () => req<Complaint[]>('/complaints'),
  getComplaint: (id: string) => req<Complaint>(`/complaints/${id}`),
  transitionComplaint: (id: string, to_status: string, note = '', auth?: AuthOptions) =>
    req<Complaint>(`/complaints/${id}/transitions`, {
      method: 'POST',
      body: JSON.stringify({ to_status, note }),
    }, auth),
  // --- officer console (officer/admin role enforced server-side) ---
  officerQueue: (params: Record<string, string> = {}, auth?: AuthOptions) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== ''));
    return req<unknown[]>(`/officer/queue${q.toString() ? `?${q}` : ''}`, undefined, auth);
  },
  officerStats: (auth?: AuthOptions) => req<OfficerStats>('/officer/stats', undefined, auth),
  officerProfile: (auth?: AuthOptions) => req<{
    user_id: string; role: string; role_source: string; email: string; name: string;
    permissions: string[]; activity: { verifications_recorded: number; last_active: string | null };
  }>('/officer/profile', undefined, auth),
  officerCase: (violationId: string, auth?: AuthOptions) =>
    req<unknown>(`/officer/cases/${violationId}`, undefined, auth),
  officerAction: (violationId: string, body: Record<string, unknown>, auth?: AuthOptions) =>
    req<unknown>(`/officer/cases/${violationId}/actions`, {
      method: 'POST',
      body: JSON.stringify(body),
    }, auth),
  // --- admin rule management (admin role enforced server-side) ---
  syncPreview: (auth?: AuthOptions) => req<unknown>('/admin/rules/sync/preview', undefined, auth),
  syncApply: (auth?: AuthOptions) => req<unknown>('/admin/rules/sync/apply', {
    method: 'POST',
    body: JSON.stringify({ confirm: true }),
  }, auth),
  // --- consumer product verification (read-only; persisted inspections) ---
  consumerLookup: (params: { barcode?: string; product_code?: string; product_name?: string }) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== ''));
    return req<{ matches: VerificationMatch[]; count: number }>(`/consumer/products/lookup${q.toString() ? `?${q}` : ''}`);
  },
  consumerVerification: (productId: string) =>
    req<VerificationDetail>(`/consumer/products/${productId}/verification`),
  consumerNutrition: (productId: string) =>
    req<NutritionInfo>(`/consumer/products/${productId}/nutrition`),
  consumerOverview: () => req<ConsumerOverview>('/consumer/overview'),
  consumerReports: () => req<{ reports: ConsumerReport[]; count: number }>('/consumer/reports'),
  consumerReport: (complaintId: string) =>
    req<ConsumerReportDetail>(`/consumer/reports/${complaintId}`),
  // --- consumer suggestions (own rows only; officer review is separate) ---
  createSuggestion: (body: { title: string; category: string; description?: string; context?: string; location?: string }) =>
    req<Suggestion>('/consumer/suggestions', { method: 'POST', body: JSON.stringify(body) }),
  listSuggestions: () => req<Suggestion[]>('/consumer/suggestions'),
  getSuggestion: (id: string) => req<Suggestion>(`/consumer/suggestions/${id}`),
  // --- officer suggestions (officer role enforced server-side) ---
  officerSuggestions: (params: Record<string, string> = {}, auth?: AuthOptions) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== ''));
    return req<Suggestion[]>(`/officer/suggestions${q.toString() ? `?${q}` : ''}`, undefined, auth);
  },
  officerSuggestion: (id: string, auth?: AuthOptions) =>
    req<Suggestion>(`/officer/suggestions/${id}`, undefined, auth),
  reviewSuggestion: (id: string, to_status: string, note = '', auth?: AuthOptions) =>
    req<Suggestion>(`/officer/suggestions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ to_status, note }),
    }, auth),
  // --- notifications (real event inbox; scoped server-side to caller) ---
  notifications: (auth?: AuthOptions) =>
    req<BackendNotification[]>('/notifications', undefined, auth),
  markNotificationRead: (id: string, auth?: AuthOptions) =>
    req<BackendNotification>(`/notifications/${id}/read`, { method: 'POST' }, auth),
  markAllNotificationsRead: (auth?: AuthOptions) =>
    req<{ marked: number }>('/notifications/read-all', { method: 'POST' }, auth),
  // --- OCR extraction (pixels -> fields; never a verdict) ---
  // front/back cover the classic two-sided flow; extras[] carries side,
  // bottom or additional package photos as ONE inspection; listing carries
  // an optional e-commerce listing screenshot for online-listing context.
  ocrExtract: async (front: File | null, back: File | null, auth?: AuthOptions, extras: File[] = [], listing: File | null = null): Promise<OcrResponse> => {
    const headers: Record<string, string> = {};
    if (tokenProvider) {
      try {
        const token = await tokenProvider();
        if (token) headers.Authorization = `Bearer ${token}`;
      } catch {
        /* proceed unauthenticated; backend decides */
      }
    }
    if (DEV_HEADERS_ENABLED && auth?.devRole) {
      headers['X-LegalAkshi-Role'] = auth.devRole;
      if (auth.devUser) headers['X-LegalAkshi-User'] = auth.devUser;
    }
    const form = new FormData();
    if (front) form.append('front_image', front);
    if (back) form.append('back_image', back);
    for (const file of extras) form.append('images', file);
    if (listing) form.append('listing_image', listing);
    // OCR runs seconds-to-minutes; never the 12s ordinary-read timeout.
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), LONG_TIMEOUT_MS);
    let res: Response;
    try {
      res = await fetch(`${API_V1}/ocr/extract`, { method: 'POST', headers, body: form, signal: ctrl.signal });
    } catch (e: unknown) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        throw new Error(`OCR timed out after ${Math.round(LONG_TIMEOUT_MS / 60000)} minutes — try fewer or smaller photos.`);
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`API ${res.status} /ocr/extract: ${text}`);
    }
    return res.json() as Promise<OcrResponse>;
  },
  // --- Stage 2: Package Intelligence (extraction only, never verdicts) ---
  visionStatus: (auth?: AuthOptions) =>
    req<{
      status: string; enabled: boolean; provider: string | null;
      model: string | null; configured: boolean; reachable: boolean;
      reason: string; api_key_present: boolean;
    }>('/package-intelligence/vision-status', undefined, auth),
  // Stage 2C: officer-only single liveness probe (text-only, no
  // package images). Never carries credentials; backend decides.
  visionHealth: (auth?: AuthOptions) =>
    req<{ status: string; reason?: string; provider?: string | null; model?: string | null }>(
      '/package-intelligence/vision-health', { method: 'POST' }, auth),
  reconcile: (body: Record<string, unknown>, auth?: AuthOptions) =>
    req<{
      fields: Record<string, {
        field: string; final_value: string | null; status: OcrFieldStatus;
        confidence: number; sources: string[];
        candidates: { source: string; value: string; confidence: number | null }[];
        agreement: string; evidence: (string | null)[]; needs_review_reason: string;
      }>;
      readiness: { ready: boolean; blocked_fields: string[] };
      diagnostics: Record<string, unknown>;
    }>('/package-intelligence/reconcile', { method: 'POST', body: JSON.stringify(body) }, auth),
  // --- Stage 2: declaration corrections (officer-only, append-only) ---
  createCorrection: (inspectionId: string, productId: string, body: {
    field_key: string; original_value?: string | null; corrected_value?: string | null;
    original_status?: string; original_confidence?: number | null;
    evidence_snapshot?: Record<string, unknown>; correction_reason?: string;
  }, auth?: AuthOptions) =>
    req<unknown>(`/inspections/${inspectionId}/products/${productId}/corrections`, {
      method: 'POST',
      body: JSON.stringify(body),
    }, auth),
  listCorrections: (inspectionId: string, productId: string, auth?: AuthOptions) =>
    req<{
      id: string; field_key: string; original_value: string | null;
      corrected_value: string | null; verified: boolean; created_at: string;
    }[]>(`/inspections/${inspectionId}/products/${productId}/corrections`, undefined, auth),
  // --- Stage 2: learning (officer-only; verified rows only are trusted) ---
  errorPatterns: (auth?: AuthOptions) =>
    req<{ patterns: { field: string; failure_type: string; occurrences: number }[] }>(
      '/officer/learning/error-patterns', undefined, auth),
  learningQueue: (auth?: AuthOptions) =>
    req<{ queue: { priority: string; reason: string }[] }>(
      '/officer/learning/review-queue', undefined, auth),
};
