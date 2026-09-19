/** Shared label-capture pieces (consumer scan + officer Scan & Inspect). */
import { CheckCircle2, UploadCloud } from 'lucide-react';

export type ScanPhoto = { name: string; url: string };

/** In-memory photo holder shared across the scan workflow steps. */
export const currentScanPhotos: { front: ScanPhoto | null; back: ScanPhoto | null } = {
  front: null,
  back: null,
};

export function setScanPhoto(side: 'front' | 'back', photo: ScanPhoto | null): void {
  const previous = currentScanPhotos[side];
  if (previous) URL.revokeObjectURL(previous.url);
  currentScanPhotos[side] = photo;
}

export function clearScanPhotos(): void {
  (['front', 'back'] as const).forEach((side) => setScanPhoto(side, null));
}

export function PhotoSlot({ title, description, photo, onChange, onRemove, testId }: { title: string; description: string; photo: ScanPhoto | null; onChange: (file: File) => void; onRemove: () => void; testId: string }) {
  return <div className="rounded-2xl border border-[#e0e9e3] bg-white p-4 shadow-soft">
    <div className="mb-3 flex items-start justify-between gap-3"><div><p className="text-sm font-bold text-[#20382b]">{title}</p><p className="mt-1 text-xs leading-relaxed text-[#829088]">{description}</p></div>{photo && <CheckCircle2 size={18} className="shrink-0 text-[#18B978]" />}</div>
    <label className="group relative flex h-52 cursor-pointer items-center justify-center overflow-hidden rounded-xl border-2 border-dashed border-[#b9e6d0] bg-[#eef9f2] text-center transition hover:border-[#18B978] hover:bg-[#e7f7ee]">
      {photo ? <img src={photo.url} alt={`${title} preview`} className="h-full w-full object-cover" /> : <div><span className="mx-auto mb-3 grid h-14 w-14 place-items-center rounded-2xl bg-white text-[#18B978] shadow-soft"><UploadCloud size={25} /></span><span className="block text-xs font-bold text-[#214333]">Choose photo</span><span className="mt-1 block text-[11px] text-[#718179]">JPG or PNG · up to 10 MB</span></div>}
      <input type="file" accept="image/*" capture="environment" className="sr-only" onChange={event => { const file = event.target.files?.[0]; if (file) onChange(file); event.currentTarget.value = ''; }} data-testid={testId} />
    </label>
    {photo && <div className="mt-3 flex items-center justify-between gap-3"><span className="truncate text-[11px] text-[#718179]">{photo.name}</span><button type="button" onClick={onRemove} className="shrink-0 text-xs font-bold text-[#b43b37] hover:underline">Remove</button></div>}
  </div>;
}
