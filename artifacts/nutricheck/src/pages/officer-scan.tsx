/**
 * Officer Scan & Inspect — enforcement-oriented label workflow.
 *
 * Capture front/back (+ extra package photos as ONE inspection, optional
 * e-commerce listing screenshot) -> OCR EXTRACTION review/edit -> backend
 * analysis -> findings -> violations -> official report. Every regulatory
 * result comes from the FastAPI backend; OCR never decides compliance.
 */
import { useRef, useState } from 'react';
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
    // Any keystroke converts the field to a MANUAL reviewed value.
    setOcrMeta((m) => {
      const next = { ...m };
      delete next[FORM_TO_REGISTRY[key] ?? key];
      return next;
    });
    setOcrFieldStatus((m) => {
      const next = { ...m };
      delete next[FORM_TO_REGISTRY[key] ?? key];
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
      // keystroke below deletes both (field becomes MANUAL).
      const meta: Record<string, { conf: number | null }> = {};
      const statuses: Record<string, OcrFieldStatus> = {};
      (Object.keys(FORM_TO_REGISTRY)).forEach((formKey) => {
        const hit = f[formKey];
        if (hit?.value !== null && hit?.value !== undefined && hit.value !== '') {
          meta[FORM_TO_REGISTRY[formKey]] = { conf: hit.confidence };
        }
        if (hit?.status) {
          statuses[FORM_TO_REGISTRY[formKey]] = hit.status;
        }
      });
      setOcrMeta(meta);
      setOcrFieldStatus(statuses);
      setOcr(res);
      setOcrBackendMs(res.timings?.total_ms ?? null);
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
    const hit = ocrMeta[regKey];
    if (hit) {
      return <span className="rounded-full bg-[#e3f7ed] px-2 py-0.5 font-mono text-[10px] text-[#08784e]" data-testid={`prov-${formKey}`}>✓ OCR detected · {hit.conf === null || hit.conf === undefined ? 'n/a' : `${Math.round(hit.conf * 100)}%`}</span>;
    }
    // Unedited but empty: show the OCR evidence status instead of MANUAL.
    const st = ocrFieldStatus[regKey];
    if (st === 'NEEDS_REVIEW') {
      return <span className="rounded-full bg-[#fff4cf] px-2 py-0.5 font-mono text-[10px] text-[#946b09]" data-testid={`prov-${formKey}`}>⚠ Needs review</span>;
    }
    if (st === 'NOT_DETECTED') {
      return <span className="rounded-full bg-[#edf1ef] px-2 py-0.5 font-mono text-[10px] text-[#53625b]" data-testid={`prov-${formKey}`}>○ Not detected</span>;
    }
    return <span className="rounded-full bg-[#edf1ef] px-2 py-0.5 font-mono text-[10px] text-[#53625b]" data-testid={`prov-${formKey}`}>MANUAL</span>;
  };

  const textField = (label: string, key: keyof Fields) => (
    <label key={key} className="block">
      <span className="mb-1.5 flex items-center justify-between text-xs font-bold text-[#3d5145]">{label}{provBadge(key as string)}</span>
      <input value={String(fields[key])} onChange={set(key)} className="focus-ring w-full rounded-lg border border-[#dbe6de] px-3.5 py-3 text-sm outline-none focus:border-[#18B978]" data-testid={`input-officer-${key}`} />
    </label>
  );

  const groupBox = (title: string, children: React.ReactNode) => (
    <div className="rounded-xl border border-[#e4ece6] p-4">
      <h3 className="mb-3 text-xs font-extrabold uppercase tracking-[.12em] text-[#12885c]">{title}</h3>
      <div className="grid gap-4 sm:grid-cols-2">{children}</div>
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
        <button onClick={() => { setOcrMeta({}); setOcrFieldStatus({}); setOcr(null); setOcrBackendMs(null); setIngDetail(null); setOcrStatus('Manual entry — no OCR run. Values will be recorded as MANUAL declarations.'); setPhase('review'); }} className="rounded-lg border border-[#dce5df] px-4 py-2.5 text-sm font-semibold text-[#426050]" data-testid="button-officer-manual">Enter manually</button>
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
      <div className="rounded-2xl border border-[#dfe9e2] bg-white p-6 shadow-soft">
        <h2 className="font-bold text-[#20382b]">Verify extracted fields</h2>
        <div className="mt-5 space-y-4">
          {groupBox('Product', <>{textField('Product name', 'product_name')}{textField('Category', 'category')}</>)}
          {groupBox('Label declarations', <>{textField('Manufacturer', 'manufacturer')}{textField('Quantity', 'quantity')}{textField('Unit', 'quantity_unit')}{textField('Manufacturing date', 'manufacturing_date')}{textField('MRP (blank = missing)', 'mrp')}{textField('Consumer care', 'consumer_care')}</>)}
          {groupBox('Food information', <>{textField('FSSAI licence', 'fssai_license')}{textField('Batch / lot', 'batch_lot')}{textField('Best before', 'best_before')}{textField('Country of origin', 'country_of_origin')}</>)}
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
        <div className="mt-5 flex justify-between border-t border-[#edf1ee] pt-5">
          <button onClick={() => setPhase('capture')} className="rounded-lg border border-[#dce5df] px-4 py-2.5 text-sm font-semibold text-[#426050]" data-testid="button-officer-back">Back</button>
          <button onClick={runAnalysis} disabled={working || !fields.product_name} className="inline-flex items-center gap-2 rounded-lg bg-[#18B978] px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50" data-testid="button-officer-analyze"><ShieldCheck size={16} />{working ? 'Analyzing…' : 'Run rule-engine analysis'}</button>
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
          <button onClick={() => { clearScanPhotos(); setPhase('capture'); setResult(null); }} className="rounded-lg px-4 py-2.5 text-sm font-semibold text-[#607069] hover:bg-[#eef5f0]" data-testid="button-officer-new-scan"><ScanLine size={16} className="mr-1 inline" />New scan</button>
        </div>
        {error && <p className="mt-3 rounded-lg bg-[#fce6e4] p-3 text-xs text-[#8D3834]">{error}</p>}
      </div>
    </div>}
  </div>;
}
