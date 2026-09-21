/**
 * Officer Scan & Inspect — enforcement-oriented label workflow.
 *
 * Capture front/back (+ extra package photos as ONE inspection, optional
 * e-commerce listing screenshot) -> OCR EXTRACTION review/edit -> backend
 * analysis -> findings -> violations -> official report. Every regulatory
 * result comes from the FastAPI backend; OCR never decides compliance.
 */
import { useEffect, useRef, useState } from 'react';
import { Link } from 'wouter';
import { ArrowRight, Check, Download, Info, ScanLine, ShieldCheck } from 'lucide-react';
import { api, type AnalysisResponse, type AuthOptions, type FoodIngredients, type OcrFieldStatus, type OcrResponse } from '@/lib/api';
import { PhotoSlot, clearScanPhotos, currentScanPhotos, setScanPhoto } from '@/components/scan';

/** Staged progress labels shown while the backend OCR pipeline runs. */
const OCR_STEPS = [
  'Preparing images...',
  'Reading package text...',
  'Finding label sections...',
  'Extracting ingredients...',
  'Checking declarations...',
  'Preparing compliance analysis...',
];

type Phase = 'capture' | 'review' | 'result';

const PILL: Record<string, string> = {
  PASS: 'bg-[#e3f7ed] text-[#08784e]',
  FAIL: 'bg-[#fce6e4] text-[#b43b37]',
  NEEDS_REVIEW: 'bg-[#fff4cf] text-[#946b09]',
  NOT_APPLICABLE: 'bg-[#edf1ef] text-[#53625b]',
  PENDING: 'bg-[#fff4cf] text-[#946b09]',
  CONFIRMED: 'bg-[#e3f7ed] text-[#08784e]',
  REJECTED: 'bg-[#edf1ef] text-[#53625b]',
  REQUIRES_REVIEW: 'bg-[#fff4cf] text-[#946b09]',
};

const ONLINE_CHECKS = new Set(['CHK-ECOMMERCE-DECL', 'CHK-ECOMMERCE-COO-FILTER']);

function IngredientStatusPill({ detection }: { detection?: string }) {
  const tone = detection === 'DETECTED'
    ? 'bg-[#e3f7ed] text-[#08784e]'
    : detection === 'NEEDS_REVIEW'
      ? 'bg-[#fff4cf] text-[#946b09]'
      : 'bg-[#edf1ef] text-[#53625b]';
  const label = detection === 'DETECTED' ? 'OCR' : detection === 'NEEDS_REVIEW' ? 'NEEDS REVIEW' : 'MANUAL';
  return <span className={`rounded-full px-2 py-0.5 font-mono text-[10px] ${tone}`} data-testid="ingredients-status">{label}</span>;
}

function IngredientGroup({ title, items, match, tone, testId }: {
  title: string;
  items: { ingredient?: string; name?: string; status?: string; ins_number?: string | null }[];
  match: (status: string) => boolean;
  tone: 'ok' | 'warn' | 'muted';
  testId: string;
}) {
  const shown = items.filter((it) => match(it.status ?? ''));
  if (shown.length === 0) return null;
  const cls = tone === 'ok'
    ? 'bg-[#e3f7ed] text-[#08784e]'
    : tone === 'warn'
      ? 'bg-[#fff4cf] text-[#946b09]'
      : 'bg-[#edf1ef] text-[#53625b]';
  return <div data-testid={testId}>
    <p className="mb-1 text-[11px] font-bold uppercase tracking-wide text-[#607069]">{title} ({shown.length})</p>
    <div className="flex flex-wrap gap-1.5">
      {shown.map((it, i) => <span key={i} className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${cls}`}>{it.ingredient ?? it.name}{it.ins_number ? ` · ${it.ins_number}` : ''}</span>)}
    </div>
  </div>;
}

function HighlightedUncertain({ text, items }: {
  text: string;
  items: { ingredient?: string; name?: string; status?: string }[];
}) {
  // Highlight the uncertain spans (NEEDS_REVIEW / UNKNOWN names) inside
  // the editable cleaned list so the officer corrects only those parts
  // instead of re-typing everything.
  const names = [...new Set(items
    .filter((it) => it.status === 'NEEDS_REVIEW' || it.status === 'UNKNOWN')
    .map((it) => (it.ingredient ?? it.name ?? '').trim())
    .filter((n) => n.length >= 3))].sort((a, b) => b.length - a.length);
  if (!text || names.length === 0) return null;
  const pattern = new RegExp(`(${names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
  const parts = text.split(pattern);
  if (parts.length <= 1) return null;
  return <p className="mt-2 text-xs leading-relaxed text-[#3d5145]" data-testid="ingredients-uncertain">
    Uncertain spans: {parts.map((p, i) => names.some((n) => n.toLowerCase() === p.toLowerCase())
    ? <mark key={i} className="rounded bg-[#fff4cf] px-0.5 font-semibold text-[#946b09]">{p}</mark>
    : <span key={i}>{p}</span>)}
  </p>;
}

type Fields = {
  product_name: string; category: string; manufacturer: string; quantity: string;
  quantity_unit: string; manufacturing_date: string; mrp: string; consumer_care: string;
  fssai_license: string; batch_lot: string; best_before: string; country_of_origin: string;
  ingredients_raw: string;
  imported: boolean; ecommerce: boolean; is_food: boolean;
};

export function OfficerScanPage() {
  const [phase, setPhase] = useState<Phase>('capture');
  const [photos, setPhotos] = useState({ front: currentScanPhotos.front, back: currentScanPhotos.back });
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [business, setBusiness] = useState('');
  const [inspectorId, setInspectorId] = useState('');
  const [fields, setFields] = useState<Fields>({
    product_name: '', category: 'GENERAL', manufacturer: '', quantity: '',
    quantity_unit: 'g', manufacturing_date: '', mrp: '', consumer_care: '',
    fssai_license: '', batch_lot: '', best_before: '', country_of_origin: '',
    ingredients_raw: '',
    imported: false, ecommerce: false, is_food: true,
  });
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [inspectionId, setInspectionId] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [files, setFiles] = useState<{ front: File | null; back: File | null }>({ front: null, back: null });
  const [extraFiles, setExtraFiles] = useState<File[]>([]);
  const [listingFile, setListingFile] = useState<File | null>(null);
  const [inspectionContext, setInspectionContext] = useState('PACKAGE_ONLY');
  const [listingUrl, setListingUrl] = useState('');
  const [ocr, setOcr] = useState<OcrResponse | null>(null);
  // Provenance of the CURRENT form values: registry field -> OCR confidence.
  // Present only while the value is byte-identical to the OCR output; any
  // edit deletes the entry (value becomes MANUAL with null confidence).
  const [ocrMeta, setOcrMeta] = useState<Record<string, { conf: number | null }>>({});
  // Per-field OCR evidence status (DETECTED / NEEDS_REVIEW / NOT_DETECTED)
  // for unedited fields; deleted on edit alongside ocrMeta.
  const [ocrFieldStatus, setOcrFieldStatus] = useState<Record<string, OcrFieldStatus>>({});
  // Fields the officer changed AFTER OCR filled them: shown distinctly as
  // OFFICER CORRECTED (not lumped in with untouched MANUAL blanks).
  const [corrected, setCorrected] = useState<Record<string, boolean>>({});
  // Frozen OCR evidence snapshot per form field (value/confidence/source
  // image/box/status at capture time) for the "Review evidence" panels.
  // Stage 3C: provider + region ride along when reconciliation supplies
  // them; detailed evidence stays behind the toggle by default.
  const [ocrEvidence, setOcrEvidence] = useState<Record<string, { value: string; conf: number | null; image?: string | null; box?: unknown; status?: OcrFieldStatus; provider?: string | null; region?: string | null }>>({});
  // Stage 2 reconciliation sources per registry field: e.g. ["rapidocr",
  // "vision"], plus conflicting candidates for the needs-review display.
  const [fieldSources, setFieldSources] = useState<Record<string, string[]>>({});
  const [fieldCandidates, setFieldCandidates] = useState<Record<string, { source: string; value: string; confidence?: number | null }[]>>({});
  const [fieldReasons, setFieldReasons] = useState<Record<string, string>>({});
  // Corrections persisted to the backend (append-only, officer-only).
  const [savedCorrections, setSavedCorrections] = useState(0);
  const [productId, setProductId] = useState('');
  // Stage 2B vision diagnostics for the review header (provider, calls,
  // latency). Null when the backend ran OCR-only or is older.
  const [visionInfo, setVisionInfo] = useState<{
    enabled: boolean; provider?: string | null; calls?: number;
    latencyMs?: number | null; error?: string | null;
    detail?: string | null;
  } | null>(null);
  // Correction audit (Stage 2 human-in-the-loop): every officer edit of
  // an OCR-valued field keeps original value, corrected value, source,
  // timestamp, field and evidence. Persisted append-only to the backend
  // once the inspection product exists (see runAnalysis); only verified
  // rows may ever enter the trusted learning dataset — nothing here
  // trains anything.
  const [corrections, setCorrections] = useState<{ field: string; from: string; to: string; at: string; image?: string | null; box?: unknown; status?: string; conf?: number | null }[]>([]);
  // Analysis requirements from the Rule Engine (read-only): which
  // review fields the applicable checks need for the current context.
  const [requirements, setRequirements] = useState<{ required: { field: string; ocr_key: string | null; form_key: string | null; checks: { check_id: string; title: string }[] }[]; optional: { field: string; ocr_key: string | null; form_key: string | null }[] } | null>(null);
  const [ocrStatus, setOcrStatus] = useState('');
  const [ocrStep, setOcrStep] = useState(0);
  const [ocrElapsed, setOcrElapsed] = useState(0);
  const [ocrBackendMs, setOcrBackendMs] = useState<number | null>(null);
  // Full ingredient evidence: raw OCR, cleaned list, coherence, and the
  // recognised / additives / unknown breakdown for the review panel.
  const [ingDetail, setIngDetail] = useState<FoodIngredients | null>(null);
  const ocrTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  /** Form key -> engine registry field for provenance tracking. */
  const FORM_TO_REGISTRY: Record<string, string> = {
    product_name: 'common_generic_name', manufacturer: 'manufacturer',
    quantity: 'net_quantity', mrp: 'mrp',
    manufacturing_date: 'mfg_month_year', consumer_care: 'consumer_care',
    fssai_license: 'fssai_license', batch_lot: 'batch_lot',
    best_before: 'best_before', country_of_origin: 'country_of_origin',
    ingredients_raw: 'ingredients_raw',
  };
  const auth: AuthOptions | undefined = api.devHeadersEnabled ? { devRole: 'officer' } : undefined;
  // Analysis requirements (Rule-Engine-derived, read-only) for the
  // current inspection context; refetched when context toggles change.
  useEffect(() => {
    if (phase !== 'review') return;
    api.analysisRequirements({
      food: fields.is_food, imported: fields.imported,
      ecommerce: fields.ecommerce || inspectionContext !== 'PACKAGE_ONLY',
      category: fields.category, quantity_type: 'weight',
    }).then(setRequirements).catch(() => setRequirements(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, fields.is_food, fields.imported, fields.ecommerce, fields.category, inspectionContext]);
  /** Whether a required review field is usable for analysis: non-blank
   *  plus DETECTED, officer-corrected, or officer-typed (MANUAL) value.
   *  Untouched NEEDS_REVIEW / NOT_DETECTED blanks are not usable. */
  const fieldUsable = (formKey: string): boolean => {
    const raw = fields[formKey as keyof Fields];
    if (typeof raw !== 'string' || raw.trim() === '') return false;
    if (formKey === 'quantity' && String(fields.quantity_unit).trim() === '') return false;
    const reg = FORM_TO_REGISTRY[formKey] ?? formKey;
    const st = ocrFieldStatus[reg];
    if (st === 'DETECTED') return true;
    if (corrected[reg]) return true;
    if (st === undefined && ocrMeta[reg] === undefined) return true; // officer-typed
    return false;
  };
  const requiredReqs = (requirements?.required ?? []).filter((r) => r.form_key);
  const usableCount = requiredReqs.filter((r) => fieldUsable(r.form_key as string)).length;
  const blockedReqs = requiredReqs.filter((r) => !fieldUsable(r.form_key as string));
  const autoCount = Object.values(ocrFieldStatus).filter((s) => s === 'DETECTED').length;
  const reviewCount = Object.values(ocrFieldStatus).filter((s) => s === 'NEEDS_REVIEW').length;
  const missingCount = Object.keys(FORM_TO_REGISTRY).length - autoCount - reviewCount;
  // Gate is active only when the requirements endpoint answered with
  // reviewable fields; otherwise the legacy product-name gate applies
  // (never block on an unreachable endpoint).
  const gateActive = requirements !== null && requiredReqs.length > 0;
  const analysisReady = gateActive ? blockedReqs.length === 0 : !!fields.product_name;

  const pick = (side: 'front' | 'back', file: File) => {
    setScanPhoto(side, { name: file.name, url: URL.createObjectURL(file) });
    setPhotos({ ...currentScanPhotos });
    setFiles((f) => ({ ...f, [side]: file }));
  };
  const remove = (side: 'front' | 'back') => {
    setScanPhoto(side, null);
    setPhotos({ ...currentScanPhotos });
    setFiles((f) => ({ ...f, [side]: null }));
  };
  const set = (key: keyof Fields) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    const v = e.target.type === 'checkbox' ? (e.target as HTMLInputElement).checked : e.target.value;
    setFields((f) => ({ ...f, [key]: v as never }));
    // Any keystroke converts the field to a reviewed value. Fields that
    // held an OCR value become OFFICER CORRECTED (with a correction
    // record); untouched blanks stay MANUAL.
    const regKey = FORM_TO_REGISTRY[key] ?? key;
    if (ocrMeta[regKey] !== undefined && typeof v !== 'boolean') {
      const prev = fields[key];
      if (typeof prev === 'string' && prev !== v) {
        const ev = ocrEvidence[key as string];
        const prevStatus = ocrFieldStatus[regKey];
        const prevConf = ocrMeta[regKey]?.conf ?? null;
        setCorrected((c) => (c[regKey] ? c : { ...c, [regKey]: true }));
        setCorrections((list) => [...list, {
          field: key as string, from: prev, to: v as string,
          at: new Date().toISOString(),
          image: ev?.image ?? null, box: ev?.box,
          status: prevStatus, conf: prevConf,
        }]);
      }
    }
    setOcrMeta((m) => {
      const next = { ...m };
      delete next[regKey];
      return next;
    });
    setOcrFieldStatus((m) => {
      const next = { ...m };
      delete next[regKey];
      return next;
    });
  };

  /** Real OCR: images -> backend -> structured fields (never a verdict). */
  async function runOcr() {
    if (!files.front && !files.back && extraFiles.length === 0) return;
    setWorking(true);
    setError('');
    // Live progress: staged labels + elapsed timer so the UI never freezes
    // silently while the backend pipeline runs.
    const started = Date.now();
    setOcrStep(0);
    setOcrElapsed(0);
    setOcrBackendMs(null);
    if (ocrTimer.current) clearInterval(ocrTimer.current);
    ocrTimer.current = setInterval(() => {
      const s = (Date.now() - started) / 1000;
      setOcrElapsed(s);
      setOcrStep(Math.min(OCR_STEPS.length - 1, Math.floor(s / 2)));
    }, 250);
    try {
      const res = await api.ocrExtract(files.front, files.back, auth, extraFiles, listingFile);
      const f = res.fields;
      const val = (k: string) => f[k]?.value ?? '';
      const foodVal = (k: string) => {
        const v = res.food?.fields?.[k]?.value;
        return typeof v === 'string' ? v : '';
      };
      const ing = res.food?.fields?.ingredients as FoodIngredients | undefined;
      setIngDetail(ing ?? null);
      setFields((prev) => ({
        ...prev,
        product_name: val('product_name') || foodVal('brand_name'),
        manufacturer: val('manufacturer') || foodVal('manufacturer'),
        quantity: val('quantity'),
        quantity_unit: val('unit') || 'g',
        manufacturing_date: val('manufacturing_date'),
        mrp: val('mrp'),
        consumer_care: val('consumer_care'),
        fssai_license: val('fssai_license') || foodVal('fssai_license'),
        batch_lot: val('batch_lot'),
        best_before: val('best_before'),
        country_of_origin: val('country_of_origin'),
        // Prefer the cleaned ingredient view; raw OCR text stays available
        // in the response for audit.
        ingredients_raw: ing?.cleaned_text || ing?.raw_text || '',
      }));
      // Provenance + evidence status for UNEDITED fields only; any
      // keystroke below deletes both (field becomes MANUAL/CORRECTED).
      // The frozen evidence snapshot stays for the review panels.
      const meta: Record<string, { conf: number | null }> = {};
      const statuses: Record<string, OcrFieldStatus> = {};
      const evidence: Record<string, { value: string; conf: number | null; image?: string | null; box?: unknown; status?: OcrFieldStatus; provider?: string | null; region?: string | null }> = {};
      const sources: Record<string, string[]> = {};
      const candidates: Record<string, { source: string; value: string }[]> = {};
      const reasons: Record<string, string> = {};
      // Stage 2B server reconciliation: the OCR response itself carries
      // OCR + Vision reconciled FINAL candidates (conflicts stay
      // NEEDS_REVIEW, never auto-picked). Prefer it; fall back to the
      // standalone reconcile call only against older backends.
      let reconciled: Record<string, {
        status?: OcrFieldStatus; final_value?: string | null;
        sources?: string[]; candidates?: { source: string; value: string }[];
        needs_review_reason?: string; region?: string | null;
      }> = {};
      const serverRec = (res.reconciliation?.fields ?? {}) as typeof reconciled;
      if (Object.keys(serverRec).length > 0) {
        reconciled = serverRec;
      } else {
        try {
          const rec = await api.reconcile({
            ocr_result: { fields: res.fields },
            requirements,
          }, auth);
          reconciled = (rec.fields ?? {}) as typeof reconciled;
        } catch {
          reconciled = {};
        }
      }
      (Object.keys(FORM_TO_REGISTRY)).forEach((formKey) => {
        const hit = f[formKey];
        const regKey = FORM_TO_REGISTRY[formKey];
        const rec = reconciled[formKey] ?? reconciled[regKey];
        if (hit?.value !== null && hit?.value !== undefined && hit.value !== '') {
          meta[FORM_TO_REGISTRY[formKey]] = { conf: hit.confidence };
        }
        if (hit?.status) {
          statuses[FORM_TO_REGISTRY[formKey]] = hit.status;
        }
        if (hit !== undefined && hit !== null) {
          const vCand = (rec?.candidates ?? []).find((c) => c.source === 'vision') as { provider?: string | null } | undefined;
          const hitRec = hit as { box?: unknown; provider?: string | null; provenance?: string };
          evidence[formKey] = {
            value: hit.value ?? '',
            conf: hit.confidence ?? null,
            image: hit.image ?? null,
            box: hitRec.box,
            status: hit.status,
            provider: vCand?.provider ?? hitRec.provider ?? hitRec.provenance ?? res.engine ?? null,
            region: rec?.region ?? null,
          };
        }
        const srcList = rec?.sources ?? (hit as { sources?: { image?: string | null }[] } | undefined)?.sources?.map((s) => 'OCR').filter((v, i, a) => a.indexOf(v) === i);
        if (srcList && srcList.length > 0) sources[regKey] = srcList.map((s) => s === 'vision' ? 'Vision AI' : s === 'rapidocr' || s === 'OCR' ? 'OCR' : s);
        else if (hit?.provenance) sources[regKey] = [hit.provenance === 'vision' ? 'Vision AI' : 'OCR'];
        const recCands = (rec?.candidates ?? []).map((c) => ({ source: c.source === 'vision' ? 'Vision' : c.source === 'rapidocr' ? 'OCR' : c.source, value: c.value, confidence: (c as { confidence?: number | null }).confidence ?? null }));
        if (recCands.length > 0) candidates[regKey] = recCands;
        if (rec?.needs_review_reason) reasons[regKey] = rec.needs_review_reason;
        if (rec?.status && (rec.final_value !== undefined || rec.status !== 'DETECTED')) {
          // Server reconciliation is authoritative on status; keep the
          // OCR-filled form value but surface the reconciled status.
          if (rec.status === 'NEEDS_REVIEW' || rec.status === 'NOT_DETECTED') {
            statuses[regKey] = rec.status;
          }
        }
      });
      setFieldSources(sources);
      setFieldCandidates(candidates);
      setFieldReasons(reasons);
      setOcrMeta(meta);
      setOcrFieldStatus(statuses);
      setOcrEvidence(evidence);
      setSavedCorrections(0);
      setProductId('');
      setCorrected({});
      setCorrections([]);
      setOcr(res);
      setOcrBackendMs(res.timings?.total_ms ?? null);
      // Stage 2B: surface vision-stage diagnostics when the backend ran
      // it (provider + bounded call count + latency); OCR-only otherwise.
      if (res.vision) {
        setVisionInfo({
          enabled: !!res.vision.vision_enabled,
          provider: res.vision.vision_provider ?? null,
          calls: res.vision.vision_calls ?? 0,
          latencyMs: res.vision.vision_latency_ms ?? null,
          error: res.vision.vision_error ?? null,
          detail: res.vision.vision_status_detail ?? null,
        });
      } else {
        setVisionInfo(null);
      }
      const n = res.images_analyzed ?? ((files.front ? 1 : 0) + (files.back ? 1 : 0));
      setOcrStatus(res.status === 'OK'
        ? `OCR extraction (${res.engine}) over ${n} package image${n === 1 ? '' : 's'} — review every value before analysis. OCR is not guaranteed correct.`
        : 'OCR could not read confidently — fields left blank for manual entry (NEEDS_REVIEW).');
      setPhase('review');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (ocrTimer.current) { clearInterval(ocrTimer.current); ocrTimer.current = null; }
      setWorking(false);
    }
  }

  async function runAnalysis() {
    setWorking(true);
    setError('');
    try {
      const inspection = await api.createInspection({
        inspector_id: inspectorId || 'officer',
        inspector_name: inspectorId || 'Officer',
        business_name: business || 'Field inspection',
        inspection_date: new Date().toISOString().slice(0, 10),
        inspection_type: 'PHYSICAL',
      }, auth);
      const wantsOnline = inspectionContext !== 'PACKAGE_ONLY';
      // OCR uncertainty: date fields the OCR pipeline attempted but could
      // not read, left blank by the officer, are submitted with measured
      // zero confidence so the engine routes them to NEEDS_REVIEW instead
      // of reporting a legal FAIL for what may be OCR uncertainty.
      // Officer-confirmed blanks (no OCR attempt) still FAIL as missing.
      const uncertaintyMeta: Record<string, { ocr_engine: string; confidence: number }> = {};
      if (ocr) {
        const blankDate: [keyof Fields, string][] = [
          ['manufacturing_date', 'mfg_month_year'],
          ['best_before', 'best_before'],
        ];
        for (const [formKey, regKey] of blankDate) {
          const st = ocrFieldStatus[regKey];
          if (fields[formKey] === '' && (st === 'NEEDS_REVIEW' || st === 'NOT_DETECTED')) {
            uncertaintyMeta[regKey] = { ocr_engine: 'rapidocr', confidence: 0.0 };
          }
        }
      }
      const product = await api.addProduct(inspection.inspection_id, {
        product_name: fields.product_name,
        category: fields.category,
        is_prepackaged: true,
        manufacturer: fields.manufacturer || undefined,
        quantity: fields.quantity === '' ? null : Number(fields.quantity),
        quantity_unit: fields.quantity_unit,
        quantity_type: 'weight',
        manufacturing_date: fields.manufacturing_date || undefined,
        best_before: fields.best_before || undefined,
        mrp: fields.mrp === '' ? null : Number(fields.mrp),
        consumer_care: fields.consumer_care || undefined,
        country_of_origin: fields.country_of_origin || undefined,
        unit_sale_price: 'Rs.50 per kg',
        imported: fields.imported,
        ecommerce: fields.ecommerce || wantsOnline,
        food: fields.is_food,
        fssai_license: fields.fssai_license || undefined,
        ingredients_raw: fields.ingredients_raw || undefined,
        // Physical vs online-listing context (existing declaration infra;
        // no schema change). Online checks need listing evidence for FAIL.
        inspection_context: inspectionContext,
        online_listing_url: listingUrl || undefined,
        source_listing_url: listingUrl || undefined,
        // Provenance for UNEDITED OCR fields only (registry field names);
        // edited/typed values stay manual-entry with null confidence.
        // Blank date fields with OCR uncertainty carry zero confidence so
        // the engine routes them to NEEDS_REVIEW, not FAIL.
        field_meta: {
          ...Object.fromEntries(
            Object.entries(ocrMeta).map(([reg, m]) => [reg, { ocr_engine: 'rapidocr', confidence: m.conf }])),
          ...uncertaintyMeta,
        },
      });
      const analysis = await api.analyze(inspection.inspection_id, { product_id: product.product_id });
      setInspectionId(inspection.inspection_id);
      setProductId(product.product_id);
      // Stage 2: persist officer corrections append-only (officer-only
      // endpoint). Originals are preserved server-side; failures never
      // block the already-computed analysis.
      if (corrections.length > 0) {
        let saved = 0;
        for (const c of corrections) {
          try {
            await api.createCorrection(inspection.inspection_id, product.product_id, {
              field_key: FORM_TO_REGISTRY[c.field] ?? c.field,
              original_value: c.from,
              corrected_value: c.to,
              original_status: c.status ?? 'NEEDS_REVIEW',
              original_confidence: c.conf ?? null,
              evidence_snapshot: { image_id: c.image ?? null, box: c.box ?? null },
              correction_reason: 'officer review during Scan & Inspect',
            }, auth);
            saved += 1;
          } catch {
            /* correction persistence is best-effort post-analysis */
          }
        }
        setSavedCorrections(saved);
      }
      setResult(analysis);
      setPhase('result');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setWorking(false);
    }
  }

  async function downloadPdf() {
    if (!inspectionId) return;
    setDownloading(true);
    try {
      const blob = await api.reportPdf(inspectionId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `legalakshi-official-report-${inspectionId.slice(0, 8)}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  }

  const provBadge = (formKey: string) => {
    const regKey = FORM_TO_REGISTRY[formKey] ?? formKey;
    const srcs = fieldSources[regKey];
    const hasVision = (srcs ?? []).some((s) => s === 'Vision AI' || s === 'vision');
    const hasOcr = (srcs ?? []).some((s) => s === 'OCR' || s === 'rapidocr');
    const hit = ocrMeta[regKey];
    if (hit) {
      // Stage 2B §13: agreement display. "Auto-detected" is kept for
      // continuity; the source suffix names the evidence.
      const agreement = hasVision && hasOcr
        ? 'OCR + AI agreement'
        : hasVision
          ? 'Vision-supported'
          : 'OCR';
      return <span className="rounded-full bg-[#e3f7ed] px-2 py-0.5 font-mono text-[10px] text-[#08784e]" data-testid={`prov-${formKey}`}>✓ Auto-detected · {agreement} · {hit.conf === null || hit.conf === undefined ? 'n/a' : `${Math.round(hit.conf * 100)}%`}</span>;
    }
    if (corrected[regKey]) {
      return <span className="rounded-full bg-[#e5f1f4] px-2 py-0.5 font-mono text-[10px] text-[#507b8c]" data-testid={`prov-${formKey}`}>✎ Officer corrected · Correction saved{savedCorrections > 0 ? ' ✓' : ''}</span>;
    }
    // Unedited but empty: show the OCR evidence status instead of MANUAL.
    // Stage 2B §13: conflicting OCR/Vision candidates display both sides.
    const st = ocrFieldStatus[regKey];
    if (st === 'NEEDS_REVIEW') {
      const cands = fieldCandidates[regKey] ?? [];
      const reason = fieldReasons[regKey];
      if (cands.length > 1) {
        const sides = cands.map((c) => `${c.source}: ${c.value}`).join(' vs ');
        return <span className="rounded-full bg-[#fff4cf] px-2 py-0.5 font-mono text-[10px] text-[#946b09]" data-testid={`prov-${formKey}`} title={reason ? `Reason: ${reason}` : undefined}>⚠ OCR/AI conflict: {sides}{reason ? ` · Reason: ${reason}` : ''}</span>;
      }
      return <span className="rounded-full bg-[#fff4cf] px-2 py-0.5 font-mono text-[10px] text-[#946b09]" data-testid={`prov-${formKey}`} title={reason ? `Reason: ${reason}` : undefined}>⚠ Needs review{reason ? ` · Reason: ${reason}` : ''}</span>;
    }
    if (st === 'NOT_DETECTED') {
      return <span className="rounded-full bg-[#edf1ef] px-2 py-0.5 font-mono text-[10px] text-[#53625b]" data-testid={`prov-${formKey}`}>○ Not detected · No reliable evidence found</span>;
    }
    return <span className="rounded-full bg-[#edf1ef] px-2 py-0.5 font-mono text-[10px] text-[#53625b]" data-testid={`prov-${formKey}`}>MANUAL</span>;
  };

  const correctionNote = (formKey: string) => {
    const regKey = FORM_TO_REGISTRY[formKey] ?? formKey;
    const entry = [...corrections].reverse().find((c) => (FORM_TO_REGISTRY[c.field] ?? c.field) === regKey);
    if (!entry) return null;
    return <p className="mt-1 font-mono text-[10px] leading-relaxed text-[#507b8c]" data-testid={`correction-note-${formKey}`}>
      Original: {entry.from === '' ? '(blank)' : entry.from} → Corrected: {entry.to === '' ? '(blank)' : entry.to} · Correction saved{savedCorrections > 0 ? ' to inspection record' : ' locally — persists on analysis'}.
    </p>;
  };

  const textField = (label: string, key: keyof Fields) => (
    <label key={key} className="block">
      <span className="mb-1.5 flex items-center justify-between text-xs font-bold text-[#3d5145]">{label}{provBadge(key as string)}</span>
      <input value={String(fields[key])} onChange={set(key)} className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none focus:border-[#18B978]" data-testid={`input-officer-${key}`} />
      {correctionNote(key as string)}
    </label>
  );

  const FIELD_LABELS: Record<string, string> = {
    product_name: 'Product name', category: 'Category', manufacturer: 'Manufacturer',
    quantity: 'Quantity', quantity_unit: 'Unit', manufacturing_date: 'Manufacturing date',
    mrp: 'MRP', consumer_care: 'Consumer care', fssai_license: 'FSSAI licence',
    batch_lot: 'Batch / lot', best_before: 'Best before', country_of_origin: 'Country of origin',
    ingredients_raw: 'Ingredients',
  };
  const fmtBox = (box: unknown): string => {
    try {
      const pts = box as number[][];
      const xs = pts.map((p) => Number(p[0]));
      const ys = pts.map((p) => Number(p[1]));
      return `x${Math.round(Math.min(...xs))},y${Math.round(Math.min(...ys))} ${Math.round(Math.max(...xs) - Math.min(...xs))}×${Math.round(Math.max(...ys) - Math.min(...ys))}`;
    } catch {
      return '—';
    }
  };
  const EvidencePanel = ({ formKeys, testId }: { formKeys: string[]; testId: string }) => {
    const [open, setOpen] = useState(false);
    const rows = formKeys.filter((k) => ocrEvidence[k] !== undefined);
    if (rows.length === 0) return null;
    return <div className="mt-3" data-testid={testId}>
      <button onClick={() => setOpen((o) => !o)} className="text-xs font-bold text-[#12885c] hover:underline" data-testid={`${testId}-toggle`}>{open ? 'Hide evidence' : 'Review evidence'}</button>
      {open && <div className="mt-2 space-y-1.5">{rows.map((k) => {
        const ev = ocrEvidence[k];
        return <div key={k} className="rounded-lg bg-[#fbfcfb] p-2.5 font-mono text-[10px] leading-relaxed text-[#586a5f]" data-testid={`${testId}-row-${k}`}>
          <b className="text-[#30473a]">{FIELD_LABELS[k] ?? k}</b> · OCR value: <b>{ev.value === '' ? '(blank)' : ev.value}</b><br />
          confidence: {ev.conf === null || ev.conf === undefined ? 'n/a' : `${Math.round(ev.conf * 100)}%`} · status: {ev.status ?? '—'} · source: {ev.image ?? '—'} · box: {fmtBox(ev.box)}{ev.provider ? <> · provider: {ev.provider}</> : null}{ev.region ? <> · region: {ev.region}</> : null}
        </div>;
      })}</div>}
    </div>;
  };
  const groupBox = (title: string, children: React.ReactNode, evKeys?: string[]) => (
    <div className="rounded-xl border border-[#e4ece6] p-4">
      <h3 className="mb-3 text-xs font-extrabold uppercase tracking-[.12em] text-[#12885c]">{title}</h3>
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
      {evKeys && <EvidencePanel formKeys={evKeys} testId={`evidence-${title.toLowerCase().replaceAll(/[^a-z]+/g, '-')}`} />}
    </div>
  );

  const packageFindings = (result?.findings ?? []).filter((f) => !ONLINE_CHECKS.has(f.rule_id));
  const onlineFindings = (result?.findings ?? []).filter((f) => ONLINE_CHECKS.has(f.rule_id));
  const imagesAnalyzed = ocr?.images_analyzed ?? ((files.front ? 1 : 0) + (files.back ? 1 : 0) + extraFiles.length);

  return <div>
    <div className="mb-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
      <div>
        <p className="mb-2 text-[11px] font-bold uppercase tracking-[.17em] text-[#18a86f]">Enforcement · field inspection</p>
        <h1 className="text-[28px] font-extrabold tracking-[-.045em] text-[#17191C] md:text-[34px]">Scan &amp; Inspect.</h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[#68766f]">Capture the label, verify the extraction, run the rule engine — then act on real findings.</p>
      </div>
      <div className="flex gap-2 text-xs font-bold text-[#607069]">
        {(['capture', 'review', 'result'] as Phase[]).map((p, i) => <span key={p} className={`rounded-full px-3 py-1.5 ${phase === p ? 'bg-[#dff5e9] text-[#12885c]' : 'bg-white text-[#a2ada6]'}`}>{i + 1}. {p}</span>)}
      </div>
    </div>

    {phase === 'capture' && <div className="mx-auto max-w-4xl">
      <div className="grid gap-5 md:grid-cols-2">
        <PhotoSlot title="Front of the product" description="Principal display panel for evidence." photo={photos.front} onChange={(f) => pick('front', f)} onRemove={() => remove('front')} testId="input-officer-photo-front" />
        <PhotoSlot title="Back of the product" description="Declarations, quantity, dates, licence." photo={photos.back} onChange={(f) => pick('back', f)} onRemove={() => remove('back')} testId="input-officer-photo-back" />
      </div>
      <div className="mt-5 grid gap-4 rounded-2xl border border-[#e0e9e3] bg-white p-4 shadow-soft">
        <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">More package photos (side / bottom / extra — same inspection)</span>
          <input type="file" accept="image/*" multiple onChange={(e) => setExtraFiles(Array.from(e.target.files ?? []))} className="w-full text-xs text-[#607069]" data-testid="input-officer-photo-extra" />
          {extraFiles.length > 0 && <span className="mt-1 block text-[11px] text-[#12885c]">{extraFiles.length} extra image{extraFiles.length === 1 ? '' : 's'} selected — treated as one inspection.</span>}
        </label>
        <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Online listing screenshot (only if checking e-commerce compliance)</span>
          <input type="file" accept="image/*" onChange={(e) => setListingFile(e.target.files?.[0] ?? null)} className="w-full text-xs text-[#607069]" data-testid="input-officer-photo-listing" />
        </label>
      </div>
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Premises / business</span><input value={business} onChange={(e) => setBusiness(e.target.value)} placeholder="Shop name, market" className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none focus:border-[#18B978]" data-testid="input-officer-business" /></label>
        <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Inspector ID</span><input value={inspectorId} onChange={(e) => setInspectorId(e.target.value)} placeholder="e.g. INSP-0231" className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none focus:border-[#18B978]" data-testid="input-officer-id" /></label>
      </div>
      <div className="mt-5 flex flex-col-reverse items-stretch justify-between gap-3 border-t border-[#e4ece6] pt-5 sm:flex-row sm:items-center">
        <button onClick={() => { setOcrMeta({}); setOcrFieldStatus({}); setCorrected({}); setCorrections([]); setOcrEvidence({}); setFieldSources({}); setFieldCandidates({}); setFieldReasons({}); setVisionInfo(null); setSavedCorrections(0); setProductId(''); setOcr(null); setOcrBackendMs(null); setIngDetail(null); setOcrStatus('Manual entry — no OCR run. Values will be recorded as MANUAL declarations.'); setPhase('review'); }} className="rounded-lg border border-[#dce5df] px-4 py-2.5 text-sm font-semibold text-[#426050]" data-testid="button-officer-manual">Enter manually</button>
        <button onClick={runOcr} disabled={working || (!files.front && !files.back && extraFiles.length === 0)} className="inline-flex items-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-semibold text-white shadow-[0_5px_12px_rgba(24,185,120,.18)] hover:bg-[#119e67] disabled:opacity-50" data-testid="button-officer-run-ocr"><ScanLine size={16} />{working ? 'Running OCR…' : 'Run OCR extraction'}</button>
      </div>
      {working && <div className="mt-4 rounded-xl border border-[#cfeedd] bg-[#eaf8f1] p-4" data-testid="ocr-progress">
        <p className="text-xs font-bold text-[#12885c]">{OCR_STEPS[ocrStep]}</p>
        <p className="mt-1 font-mono text-[11px] text-[#4c7761]" data-testid="ocr-elapsed">elapsed {ocrElapsed.toFixed(1)}s — staged OCR (one fast pass, then targeted regions only)</p>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#d3e9dc]"><div className="h-full rounded-full bg-[#18B978] transition-all" style={{ width: `${((ocrStep + 1) / OCR_STEPS.length) * 100}%` }} /></div>
      </div>}
      {error && <p className="mt-3 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]" data-testid="text-officer-capture-error">{error}</p>}
    </div>}

    {phase === 'review' && <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-start gap-3 rounded-xl border border-[#cfeedd] bg-[#eaf8f1] p-4 text-xs leading-relaxed text-[#4c7761]" data-testid="banner-ocr-extraction"><Info size={17} className="mt-0.5 shrink-0 text-[#18B978]" /><span><b>OCR extraction — review before analysis.</b> {ocrStatus || 'Values below came from the OCR service or manual entry.'} OCR is not guaranteed correct; every edit converts that field to a MANUAL reviewed value.</span></div>
      {imagesAnalyzed > 0 && <p className="text-xs font-bold text-[#607069]" data-testid="text-images-analyzed">{imagesAnalyzed} package image{imagesAnalyzed === 1 ? '' : 's'} analyzed{(ocr?.images ?? []).length > 0 ? ` (${(ocr?.images ?? []).map((i) => i.image).join(', ')})` : ''}{ocrBackendMs !== null ? ` in ${(ocrBackendMs / 1000).toFixed(1)}s (${ocr?.timings?.provider_calls ?? '?'} OCR passes)` : ''}.</p>}
      {visionInfo && <p className="text-[11px] text-[#607069]" data-testid="text-vision-status">{visionInfo.enabled && visionInfo.detail !== 'partial'
        ? `Vision AI active — OCR and visual evidence are being reconciled (${visionInfo.provider ?? 'provider'} · ${visionInfo.calls ?? 0} grouped call${visionInfo.calls === 1 ? '' : 's'}${visionInfo.latencyMs !== null && visionInfo.latencyMs !== undefined ? ` · ${(visionInfo.latencyMs / 1000).toFixed(1)}s` : ''}).`
        : visionInfo.enabled
          ? 'Vision AI partially unavailable — affected fields require review.'
          : `Vision AI unavailable — OCR-only extraction is being used.${visionInfo.error ? ` (${visionInfo.error})` : ''}`}</p>}
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft" data-testid="readiness-panel">
        <h2 className="font-bold text-[#20382b]">Inspection readiness</h2>
        <p className="mt-1 text-[11px] text-[#849188]">Review once — verify only the highlighted fields before analysis. Required fields come from the applicable Rule Engine checks, not from OCR guesses.</p>
        <div className="mt-4 flex flex-wrap gap-2 text-[11px] font-bold">
          <span className="rounded-full bg-[#e3f7ed] px-2.5 py-1 text-[#08784e]" data-testid="readiness-auto">✓ Auto-detected: {autoCount}</span>
          <span className="rounded-full bg-[#fff4cf] px-2.5 py-1 text-[#946b09]" data-testid="readiness-review">⚠ Needs review: {reviewCount}</span>
          <span className="rounded-full bg-[#edf1ef] px-2.5 py-1 text-[#53625b]" data-testid="readiness-missing">○ Not detected: {missingCount}</span>
        </div>
        <div className="mt-4 border-t border-[#edf1ee] pt-4">
          {requirements === null
            ? <p className="text-xs text-[#849188]" data-testid="readiness-unavailable">Rule requirements temporarily unavailable. Analysis readiness cannot be fully determined — you may still review the fields below.</p>
            : requiredReqs.length === 0
              ? <p className="text-xs text-[#849188]" data-testid="readiness-none">No reviewable required fields for this context.</p>
              : <><p className="text-xs font-bold text-[#30473a]" data-testid="readiness-required">Required for analysis: ✓ {usableCount} / {requiredReqs.length}</p>
                {blockedReqs.length > 0
                  ? <div className="mt-2 rounded-lg bg-[#fff4cf] p-3 text-xs leading-relaxed text-[#946b09]" data-testid="readiness-blocked">
                    <b>{blockedReqs.length} field{blockedReqs.length === 1 ? '' : 's'} required by the applicable checks need{blockedReqs.length === 1 ? 's' : ''} review:</b>
                    <ul className="mt-1 list-disc pl-5">{blockedReqs.map((r) => <li key={r.field}>{FIELD_LABELS[r.form_key as string] ?? r.field}</li>)}</ul>
                  </div>
                  : <p className="mt-2 inline-flex items-center gap-1 rounded-full bg-[#e3f7ed] px-2.5 py-1 text-[11px] font-bold text-[#08784e]" data-testid="readiness-ready"><Check size={12} />Ready for analysis</p>}
              </>}
        </div>
      </div>
      {Object.entries(fieldCandidates).filter(([, cands]) => cands.length > 1).length > 0 && <div className="rounded-xl border border-[#f0e3c2] bg-[#fffdf4] p-4" data-testid="conflict-panel">
        <h3 className="mb-1 text-xs font-extrabold uppercase tracking-[.12em] text-[#946b09]">OCR / AI conflicts ({Object.entries(fieldCandidates).filter(([, cands]) => cands.length > 1).length})</h3>
        <p className="mb-3 text-[11px] leading-relaxed text-[#849188]">Both candidates are preserved — nothing was auto-picked. Confirm the correct value in the field below.</p>
        <div className="space-y-2">{Object.entries(fieldCandidates).filter(([, cands]) => cands.length > 1).map(([regKey, cands]) => {
          const formKey = Object.keys(FORM_TO_REGISTRY).find((k) => FORM_TO_REGISTRY[k] === regKey) ?? regKey;
          const ev = ocrEvidence[formKey];
          return <div key={regKey} className="rounded-lg bg-white p-3 text-xs leading-relaxed text-[#3d5145]" data-testid={`conflict-${formKey}`}>
            <b>{FIELD_LABELS[formKey] ?? regKey}</b>
            {cands.map((c, i) => <span key={i} className="mt-1 block font-mono text-[11px]">{c.source}: <b>{c.value === '' ? '(blank)' : c.value}</b>{c.confidence !== null && c.confidence !== undefined ? ` · ${Math.round(c.confidence * 100)}%` : ''}</span>)}
            {fieldReasons[regKey] && <span className="mt-1 block text-[11px] text-[#946b09]">Reason: {fieldReasons[regKey]}</span>}
            {ev && Boolean(ev.image || ev.box) && <span className="mt-1 block font-mono text-[10px] text-[#849188]">View evidence: source {ev.image ?? '—'}{ev.conf !== null && ev.conf !== undefined ? ` · ${Math.round(ev.conf * 100)}%` : ''} · status {ev.status ?? '—'} — full evidence under “Verify extracted fields”.</span>}
          </div>;
        })}</div>
      </div>}
      {ocr?.timings?.images && Object.keys(ocr.timings.images).length > 0 && <div className="rounded-xl border border-[#e4ece6] bg-white p-4" data-testid="image-quality-panel">
        <h3 className="mb-2 text-xs font-extrabold uppercase tracking-[.12em] text-[#12885c]">Image quality</h3>
        <p className="mb-3 text-[11px] leading-relaxed text-[#849188]">Pre-OCR diagnostics per photo — explains why extraction may be uncertain. Poor quality never blocks review.</p>
        <div className="space-y-2">{Object.entries(ocr.timings.images).map(([label, t]) => {
          const q = t.image_quality ?? {};
          const grade = (k: string) => String(q[k] ?? '—');
          const tone = (g: string) => g === 'GOOD' ? 'bg-[#e3f7ed] text-[#08784e]' : g === 'FAIR' ? 'bg-[#fff4cf] text-[#946b09]' : g === 'POOR' ? 'bg-[#fce6e4] text-[#b43b37]' : 'bg-[#edf1ef] text-[#53625b]';
          return <div key={label} className="flex flex-wrap items-center gap-2 text-[11px]" data-testid={`image-quality-${label}`}>
            <b className="text-[#30473a]">{label}</b>
            {[['sharpness', grade('sharpness')], ['brightness', grade('brightness')], ['contrast', grade('contrast')], ['resolution', grade('resolution')], ['readability', grade('readability')]].map(([k, g]) => <span key={k} className={`rounded-full px-2 py-0.5 font-mono text-[10px] ${tone(g)}`}>{k}: {g}</span>)}
            {t.orientation && t.orientation !== '0' && <span className="font-mono text-[10px] text-[#849188]">orientation: {t.orientation}</span>}
          </div>;
        })}</div>
      </div>}
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
        <h2 className="font-bold text-[#20382b]">Verify extracted fields</h2>
        <div className="mt-5 space-y-4">
          {groupBox('Product', <>{textField('Product name', 'product_name')}{textField('Category', 'category')}</>, ['product_name'])}
          {groupBox('Label declarations', <>{textField('Manufacturer', 'manufacturer')}{textField('Quantity', 'quantity')}{textField('Unit', 'quantity_unit')}{textField('Manufacturing date', 'manufacturing_date')}{textField('MRP (blank = missing)', 'mrp')}{textField('Consumer care', 'consumer_care')}</>, ['manufacturer', 'quantity', 'manufacturing_date', 'mrp', 'consumer_care'])}
          {groupBox('Food information', <>{textField('FSSAI licence', 'fssai_license')}{textField('Batch / lot', 'batch_lot')}{textField('Best before', 'best_before')}{textField('Country of origin', 'country_of_origin')}</>, ['fssai_license', 'batch_lot', 'best_before', 'country_of_origin'])}
          <div className="rounded-xl border border-[#e4ece6] p-4" data-testid="ingredients-panel">
            <h3 className="mb-3 text-xs font-extrabold uppercase tracking-[.12em] text-[#12885c]">Ingredients</h3>
            {ingDetail && <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px]" data-testid="ingredients-meta">
              <span className="font-bold text-[#3d5145]">OCR confidence: {ingDetail.confidence === null || ingDetail.confidence === undefined ? 'n/a' : `${Math.round((ingDetail.confidence as number) * 100)}%`}</span>
              <IngredientStatusPill detection={ingDetail.detection} />
              {ingDetail.coherence?.score !== undefined && <span className="font-mono text-[#607069]">coherence {Math.round((ingDetail.coherence.score as number) * 100)}%</span>}
            </div>}
            {ingDetail?.raw_text ? <div className="mb-3 rounded-lg bg-[#f4f7f5] p-3" data-testid="ingredients-raw">
              <p className="mb-1 text-[11px] font-bold uppercase tracking-wide text-[#607069]">Raw OCR (audit trail)</p>
              <p className="font-mono text-[11px] leading-relaxed text-[#3d5145]">{ingDetail.raw_text}</p>
            </div> : null}
            {ingDetail && (ingDetail.heading || (ingDetail.accepted_lines ?? []).length > 0) ? <div className="mb-3 rounded-lg border border-[#e4ece6] p-3" data-testid="ingredients-region">
              <p className="mb-1 text-[11px] font-bold uppercase tracking-wide text-[#607069]">Detected ingredient region</p>
              {ingDetail.heading ? <p className="font-mono text-[11px] text-[#3d5145]">heading: {ingDetail.heading}</p> : null}
              <p className="text-[11px] text-[#607069]">accepted lines: {(ingDetail.accepted_lines ?? []).length} · source: {ingDetail.ocr_source ?? 'rapidocr'}</p>
            </div> : null}
            {ingDetail?.rejected_lines && ingDetail.rejected_lines.length > 0 ? <div className="mb-3 rounded-lg border border-[#f0d9c9] bg-[#fff9f4] p-3" data-testid="ingredients-rejected">
              <p className="mb-1 text-[11px] font-bold uppercase tracking-wide text-[#8a5a3b]">Rejected lines ({ingDetail.rejected_lines.length}) — excluded from the cleaned list</p>
              {ingDetail.rejected_lines.map((r, i) => <p key={i} className="mt-1 font-mono text-[11px] leading-relaxed text-[#6e5a27]">✕ {r.text} <span className="text-[#a27812]">[{r.reason}]</span></p>)}
            </div> : null}
            {ingDetail && ingDetail.detection !== 'DETECTED' ? <p className="mb-3 rounded-lg bg-[#fff4cf] p-3 text-[11px] font-bold leading-relaxed text-[#946b09]" data-testid="ingredients-review-note">NEEDS REVIEW — ingredient confidence is insufficient; verify the cleaned list against the package before analysis.</p> : null}
            <label className="block"><span className="mb-1.5 flex items-center justify-between text-xs font-bold text-[#3d5145]">Cleaned ingredient list (editable, reconstruction + contamination guard output){provBadge('ingredients_raw')}</span>
              <textarea value={fields.ingredients_raw} onChange={set('ingredients_raw')} rows={3} className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none focus:border-[#18B978]" data-testid="input-officer-ingredients_raw" /></label>
            {ingDetail && <HighlightedUncertain text={fields.ingredients_raw} items={ingDetail.analysis?.ingredients ?? []} />}
            {ingDetail?.analysis?.ingredients && ingDetail.analysis.ingredients.length > 0 && <div className="mt-3 space-y-2" data-testid="ingredients-breakdown">
              <IngredientGroup title="Recognized ingredients" items={ingDetail.analysis.ingredients} match={(s) => s === 'ALLOWED' || s === 'CONDITIONAL'} tone="ok" testId="ingredients-recognized" />
              <IngredientGroup title="Additives / INS" items={ingDetail.analysis.ingredients} match={(s) => s === 'NEEDS_REVIEW'} tone="warn" testId="ingredients-additives" />
              <IngredientGroup title="Unknown" items={ingDetail.analysis.ingredients} match={(s) => s === 'UNKNOWN' || s === 'RESTRICTED' || s === 'PROHIBITED'} tone="muted" testId="ingredients-unknown" />
              <p className="text-[11px] leading-relaxed text-[#849188]">Unknown means no configured rule was found — never an offence. Only explicit registry matches can restrict an ingredient.</p>
            </div>}
          </div>
          <div className="rounded-xl border border-[#e4ece6] p-4">
            <h3 className="mb-3 text-xs font-extrabold uppercase tracking-[.12em] text-[#12885c]">Nutrition &amp; symbols</h3>
            {ocr?.veg_nonveg_symbol && <p className="mb-3 text-xs text-[#4c7761]" data-testid="text-veg-symbol">Veg/non-veg symbol: <b>{ocr.veg_nonveg_symbol.classification}</b> ({ocr.veg_nonveg_symbol.status}{ocr.veg_nonveg_symbol.confidence !== null && ocr.veg_nonveg_symbol.confidence !== undefined ? `, ${Math.round(ocr.veg_nonveg_symbol.confidence * 100)}%` : ''}) — image analysis, inspector verifies.</p>}
            {!ocr?.veg_nonveg_symbol && <p className="mb-3 text-xs text-[#849188]">Nutrition and symbol details appear here after OCR; values are informational, not verdicts.</p>}
          </div>
          <div className="rounded-xl border border-[#e4ece6] p-4">
            <h3 className="mb-3 text-xs font-extrabold uppercase tracking-[.12em] text-[#12885c]">Legal checks</h3>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">Inspection context</span>
                <select value={inspectionContext} onChange={(e) => setInspectionContext(e.target.value)} className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none focus:border-[#18B978]" data-testid="input-officer-inspection-context">
                  <option value="PACKAGE_ONLY">Physical package only</option>
                  <option value="ONLINE_LISTING">Online listing only</option>
                  <option value="PACKAGE_AND_ONLINE_LISTING">Package + online listing</option>
                </select></label>
              <label className="block"><span className="mb-1.5 block text-xs font-bold text-[#3d5145]">E-commerce listing URL (required for online checks)</span>
                <input value={listingUrl} onChange={(e) => setListingUrl(e.target.value)} placeholder="https://…" className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none focus:border-[#18B978]" data-testid="input-officer-listing-url" /></label>
            </div>
            <div className="mt-4 flex flex-wrap gap-5">
              {(['imported', 'ecommerce', 'is_food'] as const).map((key) => (
                <label key={key} className="flex items-center gap-2 text-xs font-bold text-[#3d5145]"><input type="checkbox" checked={fields[key]} onChange={set(key)} data-testid={`input-officer-${key}`} />{key === 'imported' ? 'Imported product' : key === 'ecommerce' ? 'E-commerce listing' : 'Food product'}</label>
              ))}
            </div>
            {inspectionContext === 'PACKAGE_ONLY' && <p className="mt-3 text-[11px] leading-relaxed text-[#849188]">Online-listing checks will be reported as NOT CHECKED — package photos cannot establish online compliance.</p>}
          </div>
        </div>
        {error && <p className="mt-4 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]" data-testid="text-officer-error">{error}</p>}
        {corrections.length > 0 && <div className="rounded-xl border border-[#e4ece6] p-4" data-testid="corrections-panel">
          <h3 className="mb-2 text-xs font-extrabold uppercase tracking-[.12em] text-[#12885c]">Correction history ({corrections.length})</h3>
          <p className="mb-2 text-[11px] text-[#849188]">Original OCR values kept for future learning — nothing here trains anything{savedCorrections > 0 ? `; ${savedCorrections} saved to the inspection record${productId ? ` (product ${productId.slice(0, 8)}…)` : ''} (verified rows only may enter the trusted dataset).` : ' (persisted to the inspection record on analysis).'}</p>
          <div className="space-y-1.5">{corrections.map((c, i) => <p key={i} className="font-mono text-[10px] leading-relaxed text-[#586a5f]" data-testid={`correction-${c.field}`}><b className="text-[#30473a]">{FIELD_LABELS[c.field] ?? c.field}</b>: “{c.from}” → “{c.to}” · {c.at.slice(0, 16).replace('T', ' ')}{c.image ? ` · ${c.image}` : ''}</p>)}</div>
        </div>}
        <div className="mt-5 flex justify-between border-t border-[#edf1ee] pt-5">
          <button onClick={() => setPhase('capture')} className="rounded-lg border border-[#dce5df] px-4 py-2.5 text-sm font-semibold text-[#426050]" data-testid="button-officer-back">Back</button>
          <button onClick={runAnalysis} disabled={working || !fields.product_name || !analysisReady} title={!analysisReady ? 'Required fields need review first (see Inspection readiness above)' : undefined} className="inline-flex items-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50" data-testid="button-officer-analyze"><ShieldCheck size={16} />{working ? 'Analyzing…' : 'Run rule-engine analysis'}</button>
        </div>
      </div>
    </div>}

    {phase === 'result' && result && <div className="space-y-5">
      <div className="rounded-2xl bg-[#eaf8f1] p-7">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-bold uppercase tracking-[.14em] text-[#6a7f72]">Compliance score</p>
            <div className="mt-4 text-6xl font-extrabold tracking-[-.07em] text-[#0d8556]">{result.score.value}<span className="text-2xl text-current/50">/{result.score.out_of}</span></div>
          </div>
          <div className="space-y-2 text-right">
            <div><span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold ${PILL[result.status] ?? PILL.NOT_APPLICABLE}`}>{result.status.replaceAll('_', ' ')}</span></div>
            <div>{result.score.finalizable
              ? <span className="inline-flex items-center gap-1 rounded-full bg-[#e3f7ed] px-2.5 py-1 text-[11px] font-bold text-[#08784e]"><Check size={12} />Finalizable</span>
              : <span className="inline-flex items-center gap-1 rounded-full bg-[#fff4cf] px-2.5 py-1 text-[11px] font-bold text-[#946b09]"><Info size={12} />Review required</span>}</div>
          </div>
        </div>
      </div>
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
        <h2 className="font-bold text-[#20382b]">Physical package findings ({packageFindings.length})</h2>
        <div className="mt-4 space-y-3">
          {packageFindings.map((f) => (
            <div key={f.rule_id} className="rounded-lg border border-[#edf1ee] p-4" data-testid={`finding-${f.rule_id}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-mono text-xs font-bold text-[#30473a]">{f.rule_id}</p>
                <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold ${PILL[f.status] ?? PILL.NOT_APPLICABLE}`}>{f.status.replaceAll('_', ' ')}</span>
              </div>
              <p className="mt-2 text-sm font-semibold text-[#30473a]">{f.requirement}</p>
              <p className="mt-1 text-xs leading-relaxed text-[#68766f]">{f.explanation}</p>
              <p className="mt-2 font-mono text-[10px] text-[#849188]">{f.source_reference ?? ''} · detected: {f.evidence?.detected_value ?? '—'} · origin: {f.evidence?.confidence_origin ?? 'MANUAL'}</p>
            </div>
          ))}
        </div>
      </div>
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
        <h2 className="font-bold text-[#20382b]">Online listing compliance ({onlineFindings.length})</h2>
        {onlineFindings.length === 0
          ? <p className="mt-2 text-xs text-[#849188]">Status: NOT CHECKED — no e-commerce listing evidence was provided. Physical package images cannot establish online listing compliance.</p>
          : <div className="mt-4 space-y-3">
            {onlineFindings.map((f) => (
              <div key={f.rule_id} className="rounded-lg border border-[#edf1ee] p-4" data-testid={`finding-${f.rule_id}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-mono text-xs font-bold text-[#30473a]">{f.rule_id}</p>
                  <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold ${PILL[f.status] ?? PILL.NOT_APPLICABLE}`}>{f.status.replaceAll('_', ' ')}</span>
                </div>
                <p className="mt-2 text-sm font-semibold text-[#30473a]">{f.requirement}</p>
                <p className="mt-1 text-xs leading-relaxed text-[#68766f]">{f.explanation}</p>
              </div>
            ))}
          </div>}
      </div>
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
        <h2 className="font-bold text-[#20382b]">Enforcement next steps</h2>
        <p className="mt-1 text-[11px] text-[#849188]">Automated findings are potential compliance findings and require inspector review.</p>
        {result.violation_ids.length === 0
          ? <p className="mt-2 text-xs text-[#849188]">No violations raised — no enforcement case to open.</p>
          : <div className="mt-3 space-y-2">{result.violation_ids.map((vid) => (
            <div key={vid} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-[#fff7df] p-3">
              <span className="font-mono text-[11px] text-[#6e5a27]">Violation {vid.slice(0, 8)}… (PENDING verification)</span>
              <Link href={`/inspector/audit/${vid}`} className="rounded-lg bg-[#18B978] px-3 py-2 text-xs font-bold text-white" data-testid={`link-case-${vid}`}>Open case <ArrowRight size={12} className="ml-1 inline" /></Link>
            </div>))}</div>}
        <div className="mt-4 flex flex-wrap gap-2">
          <button onClick={downloadPdf} disabled={downloading} className="inline-flex items-center gap-2 rounded-lg border border-[#dce5df] px-4 py-2.5 text-sm font-semibold text-[#426050] disabled:opacity-50" data-testid="button-officer-report-pdf"><Download size={16} />{downloading ? 'Generating…' : 'Generate official report (PDF)'}</button>
          <button onClick={() => { clearScanPhotos(); setPhase('capture'); setResult(null); setProductId(''); setSavedCorrections(0); }} className="rounded-lg px-4 py-2.5 text-sm font-semibold text-[#607069] hover:bg-[#eef5f0]" data-testid="button-officer-new-scan"><ScanLine size={16} className="mr-1 inline" />New scan</button>
        </div>
        {error && <p className="mt-3 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]">{error}</p>}
      </div>
    </div>}
  </div>;
}
