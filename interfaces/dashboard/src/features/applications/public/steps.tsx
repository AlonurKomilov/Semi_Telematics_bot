// Public driver-application form — the 8 FMCSA steps (49 CFR §391.21).
//
// Each step is { title, sub, Render, validate }.  Render reads/writes the
// shared `data` object by path; validate returns an error map (empty = ok).
import { useRef, useState } from 'react';
import { Plus, Trash2, ShieldCheck, ChevronDown, FileText, Camera, CheckCircle2, Loader2 } from 'lucide-react';
import {
  deepGet, V, run, US_STATES, YES_NO, YEARS_AT_ADDR, CDL_CLASSES, ENDORSEMENTS,
  YEARS_CDL, EQUIPMENT_TYPES, REGIONS, PREFERRED_ROLE,
  ACCIDENT_TYPES, INJURY_LEVELS, PREVENTABLE, CONVICTION_STATUS, CONTACT_OK,
  HEARD_SOURCES, blankJob, blankAddress, blankAccident, blankViolation, ocrCdl,
  lookupCarriers,
} from './lib';
import type { CarrierHit } from './lib';
import type { Data, Errors } from './lib';
import {
  Field, TextInput, TextArea, SelectInput, Choices, Check_, Chip, DocUpload, SignatureBlock,
} from './controls';
import { pspDisclosure, fcraDisclosure, employmentDisclosure } from './disclosures';
import type { CarrierLegal, Disclosure, Block } from './disclosures';

export interface StepDef {
  title: string;
  sub: string;
  // Optional phase label.  Consecutive steps sharing a group render under one
  // sidebar heading and a "<group> · N of M" counter, so the legally-separate
  // PSP / FCRA / final-consent screens read as one "Final Authorizations" run.
  group?: string;
  Render: (p: RenderProps) => JSX.Element;
  validate: (data: Data) => Errors;
}
// Per-carrier pre-qual gate thresholds (adapt the Step 1 question text).
export interface GateReq { years: number; age: number; cls: string; }
interface RenderProps {
  data: Data;
  set: (path: string, value: unknown) => void;
  errors: Errors;
  req?: GateReq;
  // Carrier legal/contact details that fill the Step 8 consent disclosures.
  carrier?: CarrierLegal;
  // For the CDL fast-fill: the recruiting-link token (empty in preview →
  // OCR skipped) and the only-write-when-blank setter, so an OCR response
  // can never overwrite something the applicant already typed.
  token?: string;
  setIfEmpty?: (path: string, value: unknown) => void;
}

const grid = 'grid grid-cols-1 gap-4 sm:grid-cols-2';
const full = 'sm:col-span-2';
const sectionTitle = 'text-xs font-medium uppercase tracking-wide text-muted-foreground';

// Need-3-years-of-history when the applicant has been at their current
// address under 3 years (FMCSA residence-history requirement).
const needsAddressHistory = (years: string) => ['<1', '1', '2'].includes(years);

// ── Step 1 · Pre-Qualification ──────────────────────────────────────
const Step1: StepDef = {
  title: 'Pre-Qualification', sub: 'DOT eligibility',
  validate: (d) => {
    const e: Errors = {};
    for (const k of ['cdl1yr', 'age21', 'workAuth', 'cdlClassA', 'dotMedical'])
      if (!deepGet(d, `gate.${k}`)) e[`gate.${k}`] = 'Required';
    return e;
  },
  Render: ({ data, set, errors, req }) => {
    const years = req?.years ?? 1;
    const age = req?.age ?? 21;
    const cls = req?.cls || 'A';
    const Q = (key: string, label: string, hint?: string) => (
      <Field label={label} hint={hint} required error={errors[`gate.${key}`]}>
        <Choices value={deepGet(data, `gate.${key}`)} onChange={(v) => set(`gate.${key}`, v)} options={YES_NO} name={`gate-${key}`} />
      </Field>
    );
    const anyNo = ['cdl1yr', 'age21', 'workAuth', 'cdlClassA', 'dotMedical'].some((k) => deepGet(data, `gate.${k}`) === 'no');
    return (
      <div className="flex flex-col gap-5">
        {Q('cdl1yr', `Do you have at least ${years} ${years === 1 ? 'year' : 'years'} of verifiable CDL Class ${cls} driving experience?`)}
        {Q('age21', `Are you ${age} years of age or older?`)}
        {Q('workAuth', 'Are you legally authorized to work in the United States?')}
        {Q('cdlClassA', `Do you currently hold a valid CDL Class ${cls}?`)}
        {Q('dotMedical', 'Do you have a current DOT medical card?', '49 CFR §391.41')}
        {anyNo && (
          <p className="rounded-md border border-warn-bd bg-warn-bg px-3 py-2 text-sm text-warn">
            Based on your answers you may not meet the minimum DOT requirements — you can still submit, and a recruiter will review.
          </p>
        )}
      </div>
    );
  },
};

// Apply an OCR result to the form — shared by the Step-2 fast-fill card and
// the Step-3 CDL-Front upload, so both paths prefill identically.  Writes go
// through setIfEmpty (never overwrite a typed value); identity fields are
// included even from Step 3 (harmless — they're blank-only fills).
// Fields cascade in top-to-bottom with a short stagger so the applicant SEES
// the form being filled (an instant dump reads as a glitch, not assistance).
const _tick = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
async function applyCdlPrefill(
  fields: NonNullable<Awaited<ReturnType<typeof ocrCdl>>>,
  setIfEmpty: (path: string, value: unknown) => void,
) {
  const map: [string, unknown][] = [
    ['personal.first', fields.first], ['personal.middle', fields.middle],
    ['personal.last', fields.last], ['personal.dob', fields.dob],
    ['personal.addr1', fields.addr1], ['personal.city', fields.city],
    ['personal.state', fields.state], ['personal.zip', fields.zip],
    ['cdl.number', fields.number], ['cdl.state', fields.issue_state],
    ['cdl.class', fields.cdl_class], ['cdl.exp', fields.exp],
    ['cdl.restrictions', fields.restrictions],
  ];
  for (const [path, v] of map) {
    if (!v) continue;
    setIfEmpty(path, v);
    await _tick(90);
  }
  for (const code of fields.endorsements ?? []) {
    setIfEmpty(`cdl.endorsements.${code}`, true);
    await _tick(60);
  }
}

// ── CDL fast-fill (Step 2 header card) ──────────────────────────────
// One optional photo of the licence FRONT does three jobs: prefills the
// Step-2 identity/address fields, prefills the Step-3 CDL details, and
// lands in the Step-3 "CDL — Front" required-document slot (so it's never
// uploaded twice).  Strictly best-effort — any failure quietly falls back
// to manual typing, and prefill NEVER overwrites a typed value.
function CdlFastFill({ token, data, set, setIfEmpty }: {
  token?: string; data: Data;
  set: (path: string, value: unknown) => void;
  setIfEmpty?: (path: string, value: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle');
  const hasDoc = !!deepGet(data, 'cdl.docs.cdlFront');

  const onPick = async (f: File | undefined) => {
    if (!f || busy || f.size > 8 * 1024 * 1024) return;
    // The photo IS the CDL-Front document — store it (with preview) so
    // Step 3's required upload is already satisfied.
    const base = { file: f, name: f.name, size: f.size, type: f.type };
    if (f.type.startsWith('image/')) {
      const reader = new FileReader();
      reader.onload = () => set('cdl.docs.cdlFront', { ...base, dataUrl: String(reader.result) });
      reader.readAsDataURL(f);
    } else {
      set('cdl.docs.cdlFront', base);
    }
    if (!token || !setIfEmpty) return;    // recruiter preview — no network
    setBusy(true);
    const fields = await ocrCdl(token, f);
    if (!fields) { setBusy(false); setState('fail'); return; }
    await applyCdlPrefill(fields, setIfEmpty);   // busy until the cascade lands
    setBusy(false);
    setState('ok');
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-3 rounded-md border border-border bg-muted/30 p-3 sm:flex-row sm:items-center">
        <Camera size={20} className="hidden shrink-0 text-primary sm:block" />
        <div className="flex-1">
          <p className="text-sm font-medium text-foreground">Have your CDL handy?</p>
          <p className="text-xs text-muted-foreground">
            Snap a photo of the <b>front</b> and we'll fill in your details automatically —
            it also counts as your license upload later.
          </p>
          {!token && (
            <p className="mt-1.5 inline-flex rounded border border-info-bd bg-info-bg px-2 py-1 text-2xs font-medium text-info">
              Preview — auto-fill is disabled here; it runs on the live apply link.
            </p>
          )}
        </div>
        <label className={busy ? 'pointer-events-none opacity-60' : 'cursor-pointer'}>
          <input type="file" accept="image/*" className="hidden" disabled={busy}
            onChange={(e) => { onPick(e.target.files?.[0]); e.currentTarget.value = ''; }} />
          <span className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90">
            {busy
              ? <><Loader2 size={15} className="animate-spin" /> Reading…</>
              : <><Camera size={15} /> {hasDoc ? 'Retake photo' : 'Add photo'}</>}
          </span>
        </label>
      </div>
      {state === 'ok' && (
        <p className="flex items-start gap-1.5 rounded-md border border-info-bd bg-info-bg px-3 py-2 text-xs text-info">
          <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
          We filled in details from your license — please double-check everything, especially your address.
        </p>
      )}
      {state === 'fail' && (
        <p className="text-xs text-muted-foreground">
          Couldn't read that photo — no problem, just fill in the fields below.
          (Your photo is kept as the license upload.)
        </p>
      )}
    </div>
  );
}

// ── Step 2 · Personal & Contact ─────────────────────────────────────
const Step2: StepDef = {
  title: 'Personal & Contact', sub: 'Identity, address', group: 'Driver Profile',
  validate: (d) => {
    const e: Errors = {};
    const p = d.personal || {};
    if (V.required(p.first)) e['personal.first'] = 'Required';
    if (V.required(p.last)) e['personal.last'] = 'Required';
    const dob = run(p.dob, [V.required, V.date, V.minAge(18)]); if (dob) e['personal.dob'] = dob;
    // SSN is deliberately NOT asked here — it's collected on the Background
    // Check Authorization step, where it's actually used and the applicant
    // is already invested (asking this early is a trust barrier / drop-off).
    const ph = run(p.phone, [V.required, V.phone]); if (ph) e['personal.phone'] = ph;
    const em = run(p.email, [V.required, V.email]); if (em) e['personal.email'] = em;
    if (V.required(p.addr1)) e['personal.addr1'] = 'Required';
    if (V.required(p.city)) e['personal.city'] = 'Required';
    if (V.required(p.state)) e['personal.state'] = 'Required';
    const zip = run(p.zip, [V.required, V.zip]); if (zip) e['personal.zip'] = zip;
    if (V.required(p.yearsAtAddr)) e['personal.yearsAtAddr'] = 'Required';
    // Emergency contact is required — a carrier must know who to call.
    const emc = (d.personal || {}).emergency || {};
    if (V.required(emc.name)) e['personal.emergency.name'] = 'Required';
    const emcPh = run(emc.phone, [V.required, V.phone]); if (emcPh) e['personal.emergency.phone'] = emcPh;
    if (V.required(emc.relationship)) e['personal.emergency.relationship'] = 'Required';
    // FMCSA: 3-year residence history when current address < 3 years.  Each
    // previous address must carry a street + the dates it covers.
    if (needsAddressHistory(p.yearsAtAddr)) {
      const hist: Data[] = Array.isArray(d.addressHistory) ? d.addressHistory : [];
      if (hist.length === 0) e['addressHistory._'] = 'Add your previous address(es) to cover the last 3 years';
      hist.forEach((a, i) => {
        if (V.required(a.addr1)) e[`addressHistory.${i}.addr1`] = 'Required';
        if (V.required(a.from)) e[`addressHistory.${i}.from`] = 'Required';
        if (V.required(a.to)) e[`addressHistory.${i}.to`] = 'Required';
      });
    }
    return e;
  },
  Render: ({ data, set, errors, token, setIfEmpty }) => {
    const p = data.personal || {};
    const hist: Data[] = data.addressHistory || [];
    const setHist = (next: Data[]) => set('addressHistory', next);
    const showHist = needsAddressHistory(p.yearsAtAddr);
    return (
      <div className="flex flex-col gap-6">
        <CdlFastFill token={token} data={data} set={set} setIfEmpty={setIfEmpty} />
        <div>
          <p className={`${sectionTitle} mb-2`}>Legal name</p>
          <div className={grid}>
            <Field label="First name" hint="as on CDL" required error={errors['personal.first']}>
              <TextInput value={p.first} onChange={(v) => set('personal.first', v)} error={!!errors['personal.first']} />
            </Field>
            <Field label="Middle name">
              <TextInput value={p.middle} onChange={(v) => set('personal.middle', v)} />
            </Field>
            <Field label="Last name" required error={errors['personal.last']}>
              <TextInput value={p.last} onChange={(v) => set('personal.last', v)} error={!!errors['personal.last']} />
            </Field>
          </div>
        </div>
        <div>
          <p className={`${sectionTitle} mb-2`}>Identification & contact</p>
          <div className={grid}>
            <Field label="Date of birth" hint="MM/DD/YYYY" required error={errors['personal.dob']}>
              <TextInput type="date" value={p.dob} onChange={(v) => set('personal.dob', v)} mono error={!!errors['personal.dob']} />
            </Field>
            <Field label="Mobile phone" required error={errors['personal.phone']}>
              <TextInput type="tel" value={p.phone} onChange={(v) => set('personal.phone', v)} format="phone" error={!!errors['personal.phone']} />
            </Field>
            <Field label="Email" required error={errors['personal.email']}>
              <TextInput type="email" value={p.email} onChange={(v) => set('personal.email', v)} error={!!errors['personal.email']} />
            </Field>
          </div>
        </div>
        <div>
          <p className={`${sectionTitle} mb-2`}>Current address</p>
          <div className={grid}>
            <Field label="Street address" className={full} required error={errors['personal.addr1']}>
              <TextInput value={p.addr1} onChange={(v) => set('personal.addr1', v)} error={!!errors['personal.addr1']} />
            </Field>
            <Field label="Apt / Unit">
              <TextInput value={p.addr2} onChange={(v) => set('personal.addr2', v)} />
            </Field>
            <Field label="City" required error={errors['personal.city']}>
              <TextInput value={p.city} onChange={(v) => set('personal.city', v)} error={!!errors['personal.city']} />
            </Field>
            <Field label="State" required error={errors['personal.state']}>
              <SelectInput value={p.state} onChange={(v) => set('personal.state', v)} options={US_STATES} mono error={!!errors['personal.state']} />
            </Field>
            <Field label="ZIP" required error={errors['personal.zip']}>
              <TextInput value={p.zip} onChange={(v) => set('personal.zip', v)} mono error={!!errors['personal.zip']} />
            </Field>
            <Field label="Years at this address" required error={errors['personal.yearsAtAddr']}>
              <SelectInput value={p.yearsAtAddr} onChange={(v) => set('personal.yearsAtAddr', v)} options={YEARS_AT_ADDR} error={!!errors['personal.yearsAtAddr']} />
            </Field>
          </div>
        </div>
        {showHist && (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className={sectionTitle}>Previous addresses — last 3 years</p>
              <button type="button" onClick={() => setHist([...hist, blankAddress()])}
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"><Plus size={14} /> Add address</button>
            </div>
            {errors['addressHistory._'] && <p className="mb-2 text-xs text-destructive">{errors['addressHistory._']}</p>}
            <div className="flex flex-col gap-4">
              {hist.map((a, i) => (
                <div key={i} className="rounded-md border border-border p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs font-medium text-muted-foreground">Address #{i + 1}</span>
                    <button type="button" onClick={() => setHist(hist.filter((_, idx) => idx !== i))}
                      className="text-muted-foreground hover:text-destructive"><Trash2 size={14} /></button>
                  </div>
                  <div className={grid}>
                    <Field label="Street" className={full} required error={errors[`addressHistory.${i}.addr1`]}><TextInput value={a.addr1} onChange={(v) => setHist(hist.map((x, idx) => idx === i ? { ...x, addr1: v } : x))} error={!!errors[`addressHistory.${i}.addr1`]} /></Field>
                    <Field label="City"><TextInput value={a.city} onChange={(v) => setHist(hist.map((x, idx) => idx === i ? { ...x, city: v } : x))} /></Field>
                    <Field label="State"><SelectInput value={a.state} onChange={(v) => setHist(hist.map((x, idx) => idx === i ? { ...x, state: v } : x))} options={US_STATES} mono /></Field>
                    <Field label="From" required error={errors[`addressHistory.${i}.from`]}><TextInput type="month" value={a.from} onChange={(v) => setHist(hist.map((x, idx) => idx === i ? { ...x, from: v } : x))} mono error={!!errors[`addressHistory.${i}.from`]} /></Field>
                    <Field label="To" required error={errors[`addressHistory.${i}.to`]}><TextInput type="month" value={a.to} onChange={(v) => setHist(hist.map((x, idx) => idx === i ? { ...x, to: v } : x))} mono error={!!errors[`addressHistory.${i}.to`]} /></Field>
                  </div>
                </div>
              ))}
              {hist.length === 0 && <p className="text-sm text-muted-foreground">No previous addresses added yet.</p>}
            </div>
          </div>
        )}
        <div>
          <p className={`${sectionTitle} mb-2`}>Emergency contact</p>
          <div className={grid}>
            <Field label="Full name" required error={errors['personal.emergency.name']}>
              <TextInput value={(p.emergency || {}).name} onChange={(v) => set('personal.emergency.name', v)}
                error={!!errors['personal.emergency.name']} />
            </Field>
            <Field label="Phone" required error={errors['personal.emergency.phone']}>
              <TextInput type="tel" value={(p.emergency || {}).phone} onChange={(v) => set('personal.emergency.phone', v)}
                format="phone" error={!!errors['personal.emergency.phone']} />
            </Field>
            <Field label="Relationship" className={full} required error={errors['personal.emergency.relationship']}>
              <TextInput value={(p.emergency || {}).relationship} onChange={(v) => set('personal.emergency.relationship', v)}
                placeholder="Spouse, parent, sibling…" error={!!errors['personal.emergency.relationship']} />
            </Field>
          </div>
        </div>
      </div>
    );
  },
};

// ── Step 3 · CDL & Documents ────────────────────────────────────────
const Step3: StepDef = {
  title: 'CDL & Documents', sub: 'License + uploads', group: 'Driver Profile',
  validate: (d) => {
    const e: Errors = {};
    const c = d.cdl || {};
    if (V.required(c.number)) e['cdl.number'] = 'Required';
    if (V.required(c.state)) e['cdl.state'] = 'Required';
    if (V.required(c.class)) e['cdl.class'] = 'Required';
    if (V.required(c.exp)) e['cdl.exp'] = 'Required';
    const docs = c.docs || {};
    if (!docs.cdlFront) e['cdl.docs.cdlFront'] = 'Required';
    if (!docs.cdlBack) e['cdl.docs.cdlBack'] = 'Required';
    if (!docs.medical) e['cdl.docs.medical'] = 'Required';
    return e;
  },
  Render: ({ data, set, errors, token, setIfEmpty }) => {
    const c = data.cdl || {};
    const end = c.endorsements || {};
    // Uploading the CDL front HERE also runs the fast-fill (same OCR the
    // Step-2 card uses) so a driver who skipped the card still gets the
    // licence fields above prefilled.  Fires only on a fresh pick — a photo
    // carried over from Step 2 was already read there.
    const [ocrBusy, setOcrBusy] = useState(false);
    const [ocrOk, setOcrOk] = useState(false);
    const onCdlFront = async (v: { file: File } | null) => {
      set('cdl.docs.cdlFront', v);
      if (!v?.file || !v.file.type.startsWith('image/') || !token || !setIfEmpty) return;
      setOcrBusy(true);
      const fields = await ocrCdl(token, v.file);
      if (!fields) { setOcrBusy(false); return; }   // silent — manual entry as usual
      await applyCdlPrefill(fields, setIfEmpty);    // busy until the cascade lands
      setOcrBusy(false);
      setOcrOk(true);
    };
    return (
      <div className="flex flex-col gap-6">
        <div className={grid}>
          <Field label="CDL number" required error={errors['cdl.number']}>
            <TextInput value={c.number} onChange={(v) => set('cdl.number', v)} mono error={!!errors['cdl.number']} />
          </Field>
          <Field label="Issuing state" required error={errors['cdl.state']}>
            <SelectInput value={c.state} onChange={(v) => set('cdl.state', v)} options={US_STATES} mono error={!!errors['cdl.state']} />
          </Field>
          <Field label="Class" required error={errors['cdl.class']}>
            <SelectInput value={c.class} onChange={(v) => set('cdl.class', v)} options={CDL_CLASSES} error={!!errors['cdl.class']} />
          </Field>
          <Field label="Expiration date" required error={errors['cdl.exp']}>
            <TextInput type="date" value={c.exp} onChange={(v) => set('cdl.exp', v)} mono error={!!errors['cdl.exp']} />
          </Field>
          <Field label="Restrictions" className={full}>
            <TextInput value={c.restrictions} onChange={(v) => set('cdl.restrictions', v)} mono />
          </Field>
        </div>
        <div>
          <p className={`${sectionTitle} mb-2`}>Endorsements</p>
          <div className="flex flex-wrap gap-2">
            {ENDORSEMENTS.map((en) => (
              <Chip key={en.code} code={en.code} label={en.label} checked={!!end[en.code]}
                onChange={(b) => set(`cdl.endorsements.${en.code}`, b)} />
            ))}
          </div>
        </div>
        <div>
          <p className={`${sectionTitle} mb-2`}>Required documents</p>
          <div className={grid}>
            <DocUpload label="CDL — Front" sub="Photo side" required value={(c.docs || {}).cdlFront}
              onChange={onCdlFront} error={errors['cdl.docs.cdlFront']} />
            <DocUpload label="CDL — Back" sub="Barcode side" required value={(c.docs || {}).cdlBack}
              onChange={(v) => set('cdl.docs.cdlBack', v)} error={errors['cdl.docs.cdlBack']} />
            <DocUpload label="DOT Medical Card" sub="Form MCSA-5876" required value={(c.docs || {}).medical}
              onChange={(v) => set('cdl.docs.medical', v)} error={errors['cdl.docs.medical']} />
          </div>
          {ocrBusy && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 size={13} className="animate-spin" /> Reading your license…
            </p>
          )}
          {ocrOk && !ocrBusy && (
            <p className="mt-2 flex items-start gap-1.5 rounded-md border border-info-bd bg-info-bg px-3 py-2 text-xs text-info">
              <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
              We filled in the license details above from your photo — please double-check them.
            </p>
          )}
        </div>
        <div className={grid}>
          <Field label="TWIC card" hint="Transportation Worker ID">
            <Choices value={c.twic} onChange={(v) => set('cdl.twic', v)} name="twic"
              options={[{ value: 'yes', label: 'Yes' }, { value: 'no', label: 'No' }, { value: 'pending', label: 'In progress' }]} />
          </Field>
          <Field label="Hazmat clearance" hint="TSA HME">
            <Choices value={c.hazmat} onChange={(v) => set('cdl.hazmat', v)} name="hazmat"
              options={[{ value: 'current', label: 'Current' }, { value: 'expired', label: 'Expired' }, { value: 'none', label: 'None' }]} />
          </Field>
          <div className={full}>
            <Check_ checked={!!c.military} onChange={(b) => set('cdl.military', b)}>U.S. Armed Forces veteran</Check_>
          </div>
        </div>
      </div>
    );
  },
};

// ── Step 4 · Driving Experience ─────────────────────────────────────
const Step4: StepDef = {
  title: 'Driving Experience', sub: 'Equipment & history', group: 'Driver Profile',
  validate: (d) => {
    const e: Errors = {};
    const x = d.experience || {};
    if (V.required(x.yearsCdl)) e['experience.yearsCdl'] = 'Required';
    if (!x.equipment || x.equipment.length === 0) e['experience.equipment'] = 'Select at least one';
    if (!x.regions || x.regions.length === 0) e['experience.regions'] = 'Select at least one';
    return e;
  },
  Render: ({ data, set, errors }) => {
    const x = data.experience || {};
    const toggle = (key: string, val: string) => {
      const arr: string[] = x[key] || [];
      set(`experience.${key}`, arr.includes(val) ? arr.filter((v) => v !== val) : [...arr, val]);
    };
    return (
      <div className="flex flex-col gap-6">
        <div className={grid}>
          <Field label="Years driving CDL" required error={errors['experience.yearsCdl']}>
            <SelectInput value={x.yearsCdl} onChange={(v) => set('experience.yearsCdl', v)} options={YEARS_CDL} error={!!errors['experience.yearsCdl']} />
          </Field>
        </div>
        <Field label="Equipment operated" required error={errors['experience.equipment']}>
          <div className="flex flex-wrap gap-2">
            {EQUIPMENT_TYPES.map((t) => (
              <Chip key={t} label={t} checked={(x.equipment || []).includes(t)} onChange={() => toggle('equipment', t)} />
            ))}
          </div>
        </Field>
        <Field label="Regions & lanes" required error={errors['experience.regions']}>
          <div className="flex flex-wrap gap-2">
            {REGIONS.map((t) => (
              <Chip key={t} label={t} checked={(x.regions || []).includes(t)} onChange={() => toggle('regions', t)} />
            ))}
          </div>
        </Field>
        <Field label="Preferred role">
          <Choices value={x.preferredRole} onChange={(v) => set('experience.preferredRole', v)} options={PREFERRED_ROLE} name="role" />
        </Field>
      </div>
    );
  },
};

// ── FMCSA employer autocomplete ─────────────────────────────────────
// The Company field suggests carriers from the FMCSA registry as the
// applicant types (≥3 chars, debounced).  Picking one fills the company's
// registered legal name + city/state/phone (blank-only) and records the
// USDOT number — which is what lets the recruiter's §391.23 request reach
// the right employer.  Free typing always works; no token (preview) or an
// upstream hiccup just means no suggestions.
function CarrierNameInput({ token, value, onChange, onPick, error }: {
  token?: string; value?: string; onChange: (v: string) => void;
  onPick: (c: CarrierHit) => void; error?: boolean;
}) {
  const [hits, setHits] = useState<CarrierHit[]>([]);
  const [open, setOpen] = useState(false);
  const seq = useRef(0);

  const onInput = (v: string) => {
    onChange(v);
    const q = v.trim();
    if (!token || q.length < 3) { setHits([]); setOpen(false); return; }
    const id = ++seq.current;
    setTimeout(async () => {
      if (id !== seq.current) return;          // superseded keystroke
      const res = await lookupCarriers(token, q);
      if (id !== seq.current) return;
      setHits(res);
      setOpen(res.length > 0);
    }, 350);
  };

  return (
    <div className="relative" onBlurCapture={() => setTimeout(() => setOpen(false), 120)}>
      <TextInput value={value} onChange={onInput} error={error} />
      {open && (
        <ul className="absolute left-0 right-0 top-full z-30 mt-1 max-h-56 overflow-y-auto rounded-md border border-border bg-card shadow-lg">
          <li className="px-3 pt-1.5 pb-0.5 text-2xs uppercase tracking-wide text-muted-foreground/70">
            From the FMCSA registry
          </li>
          {hits.map((h) => (
            <li key={`${h.dot_number}-${h.name}`}>
              <button type="button"
                onMouseDown={(e) => { e.preventDefault(); onPick(h); setHits([]); setOpen(false); }}
                className="w-full px-3 py-2 text-left hover:bg-muted">
                <span className="block text-sm text-foreground">
                  {h.name}{h.dba ? ` (${h.dba})` : ''}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {[h.city, h.state].filter(Boolean).join(', ')}
                  {h.dot_number ? ` · USDOT ${h.dot_number}` : ''}
                  {h.active ? '' : ' · inactive'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Step 5 · Employment History ─────────────────────────────────────
const Step5: StepDef = {
  title: 'Employment History', sub: 'Last 10 years · FMCSA',
  validate: (d) => {
    const e: Errors = {};
    const employed = (d.work || {}).employed;
    if (!employed) { e['work.employed'] = 'Required'; return e; }
    // Attested no employment in the window → just require the "account for
    // the time" note (FMCSA still needs the period explained); no job list.
    if (employed === 'no') {
      if (V.required((d.work || {}).explain)) e['work.explain'] = 'Required';
      return e;
    }
    // Employed → the 10-year history is required and each row is validated.
    const jobs: Data[] = d.employment || [];
    if (jobs.length === 0) { e['employment._'] = 'Add at least one position'; return e; }
    jobs.forEach((j, i) => {
      if (V.required(j.company)) e[`employment.${i}.company`] = 'Required';
      if (V.required(j.from)) e[`employment.${i}.from`] = 'Required';
      if (!j.current && V.required(j.to)) e[`employment.${i}.to`] = 'Required';
      if (!j.fmcsa) e[`employment.${i}.fmcsa`] = 'Required';
      // Employer email is optional — but when given, it must be an email
      // (it prefills the recruiter's §391.23 request address).
      if (j.employerEmail && V.email(j.employerEmail)) e[`employment.${i}.employerEmail`] = 'Invalid email';
    });
    return e;
  },
  Render: ({ data, set, errors, token, setIfEmpty }) => {
    const jobs: Data[] = data.employment || [];
    const setJobs = (next: Data[]) => set('employment', next);
    const upd = (i: number, key: string, val: unknown) => setJobs(jobs.map((j, idx) => idx === i ? { ...j, [key]: val } : j));
    const updMany = (i: number, patch: Record<string, unknown>) =>
      setJobs(jobs.map((j, idx) => idx === i ? { ...j, ...patch } : j));
    const work = data.work || {};
    const employed = work.employed;
    // Selecting "no" clears any stray employer rows so the stored record can't
    // contradict the attestation (no "no employment" alongside a job list).
    const onGate = (v: string) => { set('work.employed', v); if (v === 'no') setJobs([]); };
    return (
      <div className="flex flex-col gap-4">
        <Field label="Have you been employed (including self-employment) at any point in the last 10 years?"
          required error={errors['work.employed']}>
          <Choices value={employed} onChange={onGate} options={YES_NO} name="work-employed" />
        </Field>
        {employed === 'no' && (
          <Field label="Account for this period" hint="FMCSA — schooling, military, unemployment, etc." required error={errors['work.explain']}>
            <TextArea value={work.explain} onChange={(v) => set('work.explain', v)} rows={4}
              placeholder="Briefly describe what you were doing during this time…" />
          </Field>
        )}
        {employed === 'yes' && (<>
        <p className="text-sm text-muted-foreground">List the last 10 years of employment, most recent first. Account for any gaps.</p>
        {errors['employment._'] && <p className="text-xs text-destructive">{errors['employment._']}</p>}
        {jobs.map((j, i) => (
          <div key={i} className="rounded-md border border-border p-4">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-xs font-semibold text-foreground">
                Position #{String(i + 1).padStart(2, '0')}{i === 0 && <span className="ml-1.5 font-normal text-muted-foreground">· most recent</span>}
              </span>
              <button type="button" onClick={() => setJobs(jobs.filter((_, idx) => idx !== i))}
                className="text-muted-foreground hover:text-destructive"><Trash2 size={14} /></button>
            </div>
            <div className={grid}>
              <Field label="Company / motor carrier" required error={errors[`employment.${i}.company`]}
                hint={token
                  ? 'start typing — we’ll suggest from the FMCSA registry'
                  : 'FMCSA suggestions run on the live apply link (disabled in preview)'}>
                <CarrierNameInput token={token} value={j.company}
                  // Manual edits invalidate a previous registry pick — the
                  // captured USDOT/MC/email belong to the OLD name.
                  onChange={(v) => updMany(i, { company: v, usdot: '', mc: '', employerEmail: '' })}
                  onPick={async (c) => {
                    // Name + registry identifiers land at once (the chip
                    // appears); the location/phone fills then CASCADE in like
                    // the CDL fast-fill — visible assistance, not a glitch.
                    updMany(i, {
                      company: c.name,
                      usdot: c.dot_number, mc: c.mc_number,
                    });
                    if (!setIfEmpty) return;
                    // setIfEmpty is atomic against LATEST state (safe across
                    // the awaits, unlike jobs-closure updates) and blank-only
                    // — never clobbers what the driver typed.
                    for (const [field, v] of [['city', c.city], ['state', c.state],
                                              ['phone', c.phone], ['employerEmail', c.email]] as const) {
                      if (!v) continue;
                      await _tick(90);
                      setIfEmpty(`employment.${i}.${field}`, v);
                    }
                  }}
                  error={!!errors[`employment.${i}.company`]} />
                {j.usdot && (
                  <p className="mt-1 flex items-center gap-1 text-2xs text-muted-foreground">
                    <ShieldCheck size={12} className="shrink-0" />
                    FMCSA-verified · USDOT {j.usdot}{j.mc ? ` · MC ${j.mc}` : ''}
                  </p>
                )}
              </Field>
              <Field label="Position / title"><TextInput value={j.position} onChange={(v) => upd(i, 'position', v)} /></Field>
              <Field label="City"><TextInput value={j.city} onChange={(v) => upd(i, 'city', v)} /></Field>
              <Field label="State"><SelectInput value={j.state} onChange={(v) => upd(i, 'state', v)} options={US_STATES} mono /></Field>
              <Field label="Phone"><TextInput type="tel" value={j.phone} onChange={(v) => upd(i, 'phone', v)} format="phone" mono /></Field>
              <Field label="Email" error={errors[`employment.${i}.employerEmail`]}>
                <TextInput type="email" value={j.employerEmail} onChange={(v) => upd(i, 'employerEmail', v)}
                  error={!!errors[`employment.${i}.employerEmail`]} />
              </Field>
              <Field label="Equipment operated"><TextInput value={j.equipment} onChange={(v) => upd(i, 'equipment', v)} /></Field>
              <Field label="From" required error={errors[`employment.${i}.from`]}>
                <TextInput type="month" value={j.from} onChange={(v) => upd(i, 'from', v)} mono error={!!errors[`employment.${i}.from`]} />
              </Field>
              <Field label="To" required error={errors[`employment.${i}.to`]}>
                <TextInput type="month" value={j.to} onChange={(v) => upd(i, 'to', v)} mono disabled={!!j.current} error={!!errors[`employment.${i}.to`]} />
              </Field>
              <div className={full}>
                <Check_ checked={!!j.current} onChange={(b) => upd(i, 'current', b)}>I currently work here</Check_>
              </div>
              <Field label="Reason for leaving" className={full}><TextInput value={j.reason} onChange={(v) => upd(i, 'reason', v)} /></Field>
              <Field label="Explain any gap before this job" className={full} hint="FMCSA — account for time between jobs">
                <TextInput value={j.gapExplanation} onChange={(v) => upd(i, 'gapExplanation', v)} placeholder="e.g. unemployed, schooling, medical leave" />
              </Field>
              <Field label="Subject to FMCSA regulations?" hint="Operated CMV in commerce" required error={errors[`employment.${i}.fmcsa`]}>
                <Choices value={j.fmcsa} onChange={(v) => upd(i, 'fmcsa', v)} options={YES_NO} name={`fmcsa-${i}`} />
              </Field>
              <Field label="May we contact this employer?">
                <Choices value={j.contactOk} onChange={(v) => upd(i, 'contactOk', v)} options={CONTACT_OK} name={`contact-${i}`} />
              </Field>
            </div>
          </div>
        ))}
        <button type="button" onClick={() => setJobs([...jobs, blankJob()])}
          className="inline-flex w-fit items-center gap-1.5 rounded-md border border-border px-3 py-2 text-sm text-foreground hover:bg-muted">
          <Plus size={16} /> {jobs.length === 0 ? 'Add first employer' : 'Add another employer'}
        </button>
        </>)}
      </div>
    );
  },
};

// ── Step 6 · Accidents & Violations ─────────────────────────────────
const Step6: StepDef = {
  title: 'Accidents & Violations', sub: 'Last 3 years',
  validate: (d) => {
    const e: Errors = {};
    const inc = d.incidents || {};
    for (const k of ['hasAccidents', 'hasViolations', 'hasSuspensions', 'hasDenial'])
      if (!inc[k]) e[`incidents.${k}`] = 'Required';
    // A disclosed "yes" must be backed by detail — ≥1 dated record, or a note.
    if (inc.hasAccidents === 'yes') {
      const acc: Data[] = inc.accidents || [];
      if (acc.length === 0) e['incidents.accidents._'] = 'Add the accident(s) you disclosed';
      acc.forEach((a, i) => { if (V.required(a.date)) e[`incidents.accidents.${i}.date`] = 'Required'; });
    }
    if (inc.hasViolations === 'yes') {
      const vio: Data[] = inc.violations || [];
      if (vio.length === 0) e['incidents.violations._'] = 'Add the violation(s) you disclosed';
      vio.forEach((a, i) => { if (V.required(a.date)) e[`incidents.violations.${i}.date`] = 'Required'; });
    }
    if ((inc.hasSuspensions === 'yes' || inc.hasDenial === 'yes') && V.required(inc.suspensionsDesc))
      e['incidents.suspensionsDesc'] = 'Required';
    return e;
  },
  Render: ({ data, set, errors }) => {
    const inc = data.incidents || {};
    const accidents: Data[] = inc.accidents || [];
    const violations: Data[] = inc.violations || [];
    const setAcc = (next: Data[]) => set('incidents.accidents', next);
    const setVio = (next: Data[]) => set('incidents.violations', next);
    const updAcc = (i: number, k: string, v: unknown) => setAcc(accidents.map((a, idx) => idx === i ? { ...a, [k]: v } : a));
    const updVio = (i: number, k: string, v: unknown) => setVio(violations.map((a, idx) => idx === i ? { ...a, [k]: v } : a));
    const gate = (key: string, label: string) => (
      <Field label={label} required error={errors[`incidents.${key}`]}>
        <Choices value={inc[key]} onChange={(v) => set(`incidents.${key}`, v)} options={YES_NO} name={key} />
      </Field>
    );
    return (
      <div className="flex flex-col gap-5">
        {gate('hasAccidents', 'Any accidents in the past 3 years?')}
        {inc.hasAccidents === 'yes' && (
          <div className="rounded-md border border-border p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className={sectionTitle}>Accident records</span>
              <button type="button" onClick={() => setAcc([...accidents, blankAccident()])} className="inline-flex items-center gap-1 text-xs text-primary hover:underline"><Plus size={14} /> Add</button>
            </div>
            {errors['incidents.accidents._'] && <p className="mb-2 text-xs text-destructive">{errors['incidents.accidents._']}</p>}
            <div className="flex flex-col gap-4">
              {accidents.map((a, i) => (
                <div key={i} className="rounded border border-border/60 p-3">
                  <div className="mb-2 flex justify-end"><button type="button" onClick={() => setAcc(accidents.filter((_, idx) => idx !== i))} className="text-muted-foreground hover:text-destructive"><Trash2 size={14} /></button></div>
                  <div className={grid}>
                    <Field label="Date" required error={errors[`incidents.accidents.${i}.date`]}><TextInput type="date" value={a.date} onChange={(v) => updAcc(i, 'date', v)} mono error={!!errors[`incidents.accidents.${i}.date`]} /></Field>
                    <Field label="Location"><TextInput value={a.location} onChange={(v) => updAcc(i, 'location', v)} /></Field>
                    <Field label="Type"><SelectInput value={a.type} onChange={(v) => updAcc(i, 'type', v)} options={ACCIDENT_TYPES} /></Field>
                    <Field label="Injuries / fatalities"><SelectInput value={a.injuries} onChange={(v) => updAcc(i, 'injuries', v)} options={INJURY_LEVELS} /></Field>
                    <Field label="Preventable?" className={full}><Choices value={a.preventable} onChange={(v) => updAcc(i, 'preventable', v)} options={PREVENTABLE} name={`prev-${i}`} /></Field>
                    <Field label="Description" className={full}><TextArea value={a.desc} onChange={(v) => updAcc(i, 'desc', v)} rows={2} /></Field>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        {gate('hasViolations', 'Any moving violations in a CMV in the past 3 years?')}
        {inc.hasViolations === 'yes' && (
          <div className="rounded-md border border-border p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className={sectionTitle}>Violation records</span>
              <button type="button" onClick={() => setVio([...violations, blankViolation()])} className="inline-flex items-center gap-1 text-xs text-primary hover:underline"><Plus size={14} /> Add</button>
            </div>
            {errors['incidents.violations._'] && <p className="mb-2 text-xs text-destructive">{errors['incidents.violations._']}</p>}
            <div className="flex flex-col gap-4">
              {violations.map((a, i) => (
                <div key={i} className="rounded border border-border/60 p-3">
                  <div className="mb-2 flex justify-end"><button type="button" onClick={() => setVio(violations.filter((_, idx) => idx !== i))} className="text-muted-foreground hover:text-destructive"><Trash2 size={14} /></button></div>
                  <div className={grid}>
                    <Field label="Date" required error={errors[`incidents.violations.${i}.date`]}><TextInput type="date" value={a.date} onChange={(v) => updVio(i, 'date', v)} mono error={!!errors[`incidents.violations.${i}.date`]} /></Field>
                    <Field label="State"><SelectInput value={a.state} onChange={(v) => updVio(i, 'state', v)} options={US_STATES} mono /></Field>
                    <Field label="Charge"><TextInput value={a.charge} onChange={(v) => updVio(i, 'charge', v)} /></Field>
                    <Field label="Penalty"><TextInput value={a.penalty} onChange={(v) => updVio(i, 'penalty', v)} /></Field>
                    <Field label="Conviction status" className={full}><SelectInput value={a.status} onChange={(v) => updVio(i, 'status', v)} options={CONVICTION_STATUS} /></Field>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        {gate('hasSuspensions', 'Has your license ever been suspended, revoked, or cancelled?')}
        {gate('hasDenial', 'Have you ever been denied a license or permit to operate a CMV?')}
        {(inc.hasSuspensions === 'yes' || inc.hasDenial === 'yes') && (
          <Field label="Please describe" hint="dates, jurisdictions, reason, and resolution" required error={errors['incidents.suspensionsDesc']}>
            <TextArea value={inc.suspensionsDesc} onChange={(v) => set('incidents.suspensionsDesc', v)} rows={4} error={!!errors['incidents.suspensionsDesc']} />
          </Field>
        )}
      </div>
    );
  },
};

// ── Step 7 · Position & Referral ────────────────────────────────────
const Step7: StepDef = {
  title: 'Position & Referral', sub: 'Role, truck info, source',
  validate: (d) => {
    const e: Errors = {};
    const p = d.position || {};
    if (!p.type) e['position.type'] = 'Required';
    if (V.required(p.source)) e['position.source'] = 'Required';
    // 'Other' → make them name the actual source so attribution is usable.
    if (p.source === 'Other' && V.required(p.sourceOther)) e['position.sourceOther'] = 'Please tell us where';
    if (p.type === 'owner') {
      const t = p.truck || {};
      const yr = Number(t.year);
      if (V.required(t.year) || !/^\d{4}$/.test(String(t.year)) || yr < 1990 || yr > new Date().getFullYear() + 1)
        e['position.truck.year'] = 'Enter a valid year';
      if (V.required(t.make)) e['position.truck.make'] = 'Required';
      if (V.required(t.model)) e['position.truck.model'] = 'Required';
      if (!t.picture) e['position.truck.picture'] = 'Required';
      if (!t.dotInspection) e['position.truck.dotInspection'] = 'Required';
    }
    return e;
  },
  Render: ({ data, set, errors }) => {
    const p = data.position || {};
    const t = p.truck || {};
    return (
      <div className="flex flex-col gap-6">
        <Field label="Are you applying as a Company Driver or Owner Operator?" required error={errors['position.type']}>
          <Choices value={p.type} onChange={(v) => set('position.type', v)} name="ptype"
            options={[{ value: 'company', label: 'Company Driver' }, { value: 'owner', label: 'Owner Operator' }]} />
        </Field>
        {p.type === 'owner' && (
          <div>
            <p className={`${sectionTitle} mb-2`}>Truck information</p>
            <div className={grid}>
              <Field label="Truck Year" required error={errors['position.truck.year']}>
                <TextInput value={t.year} onChange={(v) => set('position.truck.year', v)} mono error={!!errors['position.truck.year']} />
              </Field>
              <Field label="Truck Make" required error={errors['position.truck.make']}>
                <TextInput value={t.make} onChange={(v) => set('position.truck.make', v)} error={!!errors['position.truck.make']} />
              </Field>
              <Field label="Truck Model" required error={errors['position.truck.model']}>
                <TextInput value={t.model} onChange={(v) => set('position.truck.model', v)} error={!!errors['position.truck.model']} />
              </Field>
              <div className={full}>
                <div className={grid}>
                  <DocUpload label="Truck Picture" sub="Exterior — side or 3/4 view" required value={t.picture}
                    onChange={(v) => set('position.truck.picture', v)} error={errors['position.truck.picture']} />
                  <DocUpload label="DOT Inspection" sub="Latest annual inspection" required value={t.dotInspection}
                    onChange={(v) => set('position.truck.dotInspection', v)} error={errors['position.truck.dotInspection']} />
                </div>
              </div>
            </div>
          </div>
        )}
        <div className={grid}>
          <Field label="How did you hear about us?" required error={errors['position.source']}>
            <SelectInput value={p.source} onChange={(v) => set('position.source', v)} options={HEARD_SOURCES} error={!!errors['position.source']} />
          </Field>
          {p.source === 'Other' && (
            <Field label="Please tell us where" required error={errors['position.sourceOther']}>
              <TextInput value={p.sourceOther} onChange={(v) => set('position.sourceOther', v)}
                placeholder="e.g. a job fair, a billboard, a friend…" error={!!errors['position.sourceOther']} />
            </Field>
          )}
          <Field label="Additional message" className={full}>
            <TextArea value={p.message} onChange={(v) => set('position.message', v)} rows={4} />
          </Field>
        </div>
      </div>
    );
  },
};

// ── Steps 8–10 · Consents & Signature ───────────────────────────────
// PSP and the FCRA background-check disclosure are isolated onto their OWN
// steps: both carry a "stand-alone document" requirement (FMCSA PSP notice;
// FCRA §1681b(b)(2)(A)), so they may not share a screen with other consents.
// The final step keeps the §391.23 Employee-Verification doc + the remaining
// plain-checkbox consents + the signature.
const CHECK_CONSENTS = [
  { key: 'mvr', label: 'Motor Vehicle Record (MVR)', desc: 'Authorize the carrier to obtain my driving record from each state where I have been licensed in the past 3 years. (Also covered under the consumer-report / FCRA authorization above.)' },
  { key: 'clearinghouse', label: 'FMCSA Drug & Alcohol Clearinghouse', desc: 'I give general consent for the carrier to query the FMCSA Drug & Alcohol Clearinghouse under 49 CFR §382.701. I understand a pre-employment full query also requires my separate electronic consent in the Clearinghouse itself.' },
  { key: 'drug', label: 'Pre-employment drug screen', desc: 'I agree to submit to a DOT-regulated drug test prior to operating a commercial motor vehicle.' },
  { key: 'truthful', label: 'Truthful & complete statements', desc: 'I certify that all statements in this application are true and complete. False or omitted information is grounds for rejection or termination.' },
];
const FINAL_CONSENT_KEYS = ['employment_verification', ...CHECK_CONSENTS.map((x) => x.key)];
const EMPTY_LEGAL: CarrierLegal = {
  name: '', dot: '', mc: '', phone: '', legal_address: '',
  compliance_email: '', cra_name: '', cra_address: '', cra_phone: '', cra_site: '',
};

// Renders one disclosure's blocks (read-only legal text).
function DisclosureBody({ blocks }: { blocks: Block[] }) {
  return (
    <div className="flex flex-col gap-2.5 text-xs leading-relaxed text-muted-foreground">
      {blocks.map((b, i) => {
        if (b.kind === 'h') return <p key={i} className="text-xs font-semibold uppercase tracking-wide text-foreground">{b.text}</p>;
        if (b.kind === 'p') return <p key={i}>{b.text}</p>;
        if (b.kind === 'note') return <p key={i} className="rounded bg-muted/50 p-2 text-2xs italic">{b.text}</p>;
        if (b.kind === 'ul') return <ul key={i} className="ml-4 list-disc space-y-1">{b.items.map((it, j) => <li key={j}>{it}</li>)}</ul>;
        return (
          <div key={i} className="grid grid-cols-1 gap-x-4 gap-y-0.5 sm:grid-cols-2">
            {b.rows.map(([k, v], j) => <p key={j}><span className="text-foreground">{k}:</span> {v}</p>)}
          </div>
        );
      })}
    </div>
  );
}

// Expandable "review → acknowledge" card for a full disclosure.
function DisclosureCard({ doc, checked, error, onChange }: {
  doc: Disclosure; checked: boolean; error?: string; onChange: (b: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`rounded-md border ${error ? 'border-destructive/50' : 'border-border'} bg-card`}>
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left">
        <FileText size={16} className="shrink-0 text-muted-foreground" />
        <span className="flex-1 text-sm font-medium text-foreground">{doc.title}</span>
        <ChevronDown size={16} className={`shrink-0 text-muted-foreground transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="max-h-72 overflow-y-auto border-t border-border px-4 py-3">
          <DisclosureBody blocks={doc.blocks} />
        </div>
      )}
      <div className="border-t border-border px-4 py-3">
        <Check_ checked={checked} onChange={onChange}>
          <span className="text-sm">I have read and authorize the <span className="font-medium">{doc.title}</span>.</span>
        </Check_>
        {!open && <button type="button" onClick={() => setOpen(true)} className="mt-1 ml-7 block text-2xs text-primary hover:underline">Read the full document</button>}
      </div>
    </div>
  );
}

// A short consent whose text IS the authorization (no separate document).
// Same card frame as DisclosureCard so the final step reads as one consistent
// stack of consent cards rather than "one document card + bare checkboxes".
function ConsentCard({ label, desc, checked, error, onChange }: {
  label: string; desc: string; checked: boolean; error?: string; onChange: (b: boolean) => void;
}) {
  return (
    <div className={`rounded-md border ${error ? 'border-destructive/50' : 'border-border'} bg-card px-4 py-3`}>
      <Check_ checked={checked} onChange={onChange}>
        <span className="text-sm font-medium text-foreground">{label}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{desc}</span>
      </Check_>
    </div>
  );
}

// A standalone step that presents ONE disclosure in full (expanded) with
// nothing else but its authorization — satisfies the "stand-alone document"
// rule for PSP and the FCRA background-check disclosure.
function disclosureStep(id: 'psp' | 'fcra', title: string, sub: string): StepDef {
  const build = id === 'psp' ? pspDisclosure : fcraDisclosure;
  return {
    title, sub, group: 'Final Authorizations',
    validate: (d) => {
      const e: Errors = {};
      // SSN is collected HERE (not on Personal & Contact): it exists solely to
      // run the consumer report being authorized, and asking for it that early
      // is a trust barrier.  Same data path (personal.ssn) — server + DQ
      // packet are unchanged.
      if (id === 'fcra') {
        const ssn = run((d.personal || {}).ssn, [V.required, V.ssn]);
        if (ssn) e['personal.ssn'] = ssn;
      }
      if (!(d.consents || {})[id]) e[`consents.${id}`] = 'Required';
      return e;
    },
    Render: ({ data, set, errors, carrier }) => {
      const doc = build(carrier || EMPTY_LEGAL);
      const c = data.consents || {};
      return (
        <div className="flex flex-col gap-4">
          <div className="flex items-start gap-2 rounded-md border border-info-bd bg-info-bg p-3 text-sm text-info">
            <ShieldCheck size={18} className="mt-0.5 shrink-0" />
            <p>Federal law requires this authorization, and it must be presented on its own. Please read the full document below, then check the box to authorize.</p>
          </div>
          <div className="rounded-md border border-border bg-card p-4">
            <p className="mb-2 text-sm font-semibold text-foreground">{doc.title}</p>
            <div className="max-h-[26rem] overflow-y-auto rounded border border-border p-3">
              <DisclosureBody blocks={doc.blocks} />
            </div>
          </div>
          {id === 'fcra' && (
            <div className="rounded-md border border-border bg-card px-4 py-3">
              <div className="max-w-xs">
                <Field label="Social Security #" required error={errors['personal.ssn']}
                  hint="XXX-XX-XXXX — used only for the consumer report you authorize here; encrypted at rest">
                  <TextInput value={(data.personal || {}).ssn} onChange={(v) => set('personal.ssn', v)}
                    format="ssn" mono error={!!errors['personal.ssn']} />
                </Field>
              </div>
            </div>
          )}
          <div className={`rounded-md border ${errors[`consents.${id}`] ? 'border-destructive/50' : 'border-border'} bg-card px-4 py-3`}>
            <Check_ checked={!!c[id]} onChange={(b) => set(`consents.${id}`, b)}>
              <span className="text-sm">I have read and authorize the <span className="font-medium">{doc.title}</span>.</span>
            </Check_>
          </div>
          {errors[`consents.${id}`] && <p className="text-xs text-destructive">This authorization is required to continue.</p>}
        </div>
      );
    },
  };
}

const Step8Psp = disclosureStep('psp', 'PSP Authorization', 'FMCSA crash & inspection history');
const Step9Fcra = disclosureStep('fcra', 'Background Check Authorization', 'FCRA consumer report');

const Step10: StepDef = {
  title: 'Consents & Signature', sub: 'Authorizations', group: 'Final Authorizations',
  validate: (d) => {
    const e: Errors = {};
    const c = d.consents || {};
    for (const key of FINAL_CONSENT_KEYS) if (!c[key]) e[`consents.${key}`] = 'Required';
    if (V.required(c.sigDate)) e['consents.sigDate'] = 'Required';
    const mode = c.sigMode || 'type';
    if (mode === 'type') {
      const full_ = `${(d.personal || {}).first || ''} ${(d.personal || {}).last || ''}`.trim();
      if (V.required(c.sigName)) e['consents.sigName'] = 'Required';
      else if (full_ && c.sigName.trim().toLowerCase() !== full_.toLowerCase()) e['consents.sigName'] = `Type exactly: ${full_}`;
    } else if (!c.sigDataUrl) {
      e['consents.sigDraw'] = 'Please draw your signature';
    }
    return e;
  },
  Render: ({ data, set, errors, carrier }) => {
    const c = data.consents || {};
    const fullName = `${(data.personal || {}).first || ''} ${(data.personal || {}).last || ''}`.trim();
    const anyConsentErr = FINAL_CONSENT_KEYS.some((key) => errors[`consents.${key}`]);
    const empDoc = employmentDisclosure(carrier || EMPTY_LEGAL);
    return (
      <div className="flex flex-col gap-5">
        <div className="flex items-start gap-2 rounded-md border border-info-bd bg-info-bg p-3 text-sm text-info">
          <ShieldCheck size={18} className="mt-0.5 shrink-0" />
          <p>Final authorizations. Review the document below and check each box. Your data is encrypted and used only for this hiring decision.</p>
        </div>
        <div>
          <p className={`${sectionTitle} mb-2`}>Required consents</p>
          <div className="flex flex-col gap-3">
            {/* The one consent backed by a full legal document keeps its
                expandable card; the short ones share the same card frame. */}
            <DisclosureCard doc={empDoc} checked={!!c.employment_verification}
              error={errors['consents.employment_verification']} onChange={(b) => set('consents.employment_verification', b)} />
            {CHECK_CONSENTS.map(({ key, label, desc }) => (
              <ConsentCard key={key} label={label} desc={desc} checked={!!c[key]}
                error={errors[`consents.${key}`]} onChange={(b) => set(`consents.${key}`, b)} />
            ))}
          </div>
        </div>
        {anyConsentErr && <p className="text-xs text-destructive">All authorizations are required to submit.</p>}
        <div>
          <p className={`${sectionTitle} mb-2`}>Electronic signature</p>
          <SignatureBlock
            mode={(c.sigMode || 'type') as 'type' | 'draw'}
            name={c.sigName} dataUrl={c.sigDataUrl} fullName={fullName}
            onMode={(m) => set('consents.sigMode', m)}
            onName={(v) => set('consents.sigName', v)}
            onDraw={(url) => set('consents.sigDataUrl', url)}
            error={errors['consents.sigName'] || errors['consents.sigDraw']}
          />
          <div className="mt-3 max-w-xs">
            <Field label="Date" required error={errors['consents.sigDate']}>
              <TextInput type="date" value={c.sigDate} onChange={(v) => set('consents.sigDate', v)} mono error={!!errors['consents.sigDate']} />
            </Field>
          </div>
        </div>
      </div>
    );
  },
};

export const STEPS: StepDef[] = [Step1, Step2, Step3, Step4, Step5, Step6, Step7, Step8Psp, Step9Fcra, Step10];
