// Public driver-application form — the 8 FMCSA steps (49 CFR §391.21).
//
// Each step is { title, sub, Render, validate }.  Render reads/writes the
// shared `data` object by path; validate returns an error map (empty = ok).
import { Plus, Trash2, ShieldCheck } from 'lucide-react';
import {
  deepGet, V, run, US_STATES, YES_NO, YEARS_AT_ADDR, CDL_CLASSES, ENDORSEMENTS,
  YEARS_CDL, TOTAL_MILES, EQUIPMENT_TYPES, REGIONS, PREFERRED_ROLE,
  ACCIDENT_TYPES, INJURY_LEVELS, PREVENTABLE, CONVICTION_STATUS, CONTACT_OK,
  HEARD_SOURCES, blankJob, blankAddress, blankAccident, blankViolation,
} from './lib';
import type { Data, Errors } from './lib';
import {
  Field, TextInput, TextArea, SelectInput, Choices, Check_, Chip, DocUpload, SignatureBlock,
} from './controls';

export interface StepDef {
  title: string;
  sub: string;
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

// ── Step 2 · Personal & Contact ─────────────────────────────────────
const Step2: StepDef = {
  title: 'Personal & Contact', sub: 'Identity, address',
  validate: (d) => {
    const e: Errors = {};
    const p = d.personal || {};
    if (V.required(p.first)) e['personal.first'] = 'Required';
    if (V.required(p.last)) e['personal.last'] = 'Required';
    const dob = run(p.dob, [V.required, V.date, V.minAge(18)]); if (dob) e['personal.dob'] = dob;
    const ssn = run(p.ssn, [V.required, V.ssn]); if (ssn) e['personal.ssn'] = ssn;
    const ph = run(p.phone, [V.required, V.phone]); if (ph) e['personal.phone'] = ph;
    const em = run(p.email, [V.required, V.email]); if (em) e['personal.email'] = em;
    if (V.required(p.addr1)) e['personal.addr1'] = 'Required';
    if (V.required(p.city)) e['personal.city'] = 'Required';
    if (V.required(p.state)) e['personal.state'] = 'Required';
    const zip = run(p.zip, [V.required, V.zip]); if (zip) e['personal.zip'] = zip;
    if (V.required(p.yearsAtAddr)) e['personal.yearsAtAddr'] = 'Required';
    // FMCSA: 3-year residence history when current address < 3 years.
    if (needsAddressHistory(p.yearsAtAddr) && (!Array.isArray(d.addressHistory) || d.addressHistory.length === 0))
      e['addressHistory._'] = 'Add your previous address(es) to cover the last 3 years';
    return e;
  },
  Render: ({ data, set, errors }) => {
    const p = data.personal || {};
    const hist: Data[] = data.addressHistory || [];
    const setHist = (next: Data[]) => set('addressHistory', next);
    const showHist = needsAddressHistory(p.yearsAtAddr);
    return (
      <div className="flex flex-col gap-6">
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
            <Field label="Social Security #" hint="XXX-XX-XXXX" required error={errors['personal.ssn']}>
              <TextInput value={p.ssn} onChange={(v) => set('personal.ssn', v)} format="ssn" mono error={!!errors['personal.ssn']} />
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
                    <Field label="Street" className={full}><TextInput value={a.addr1} onChange={(v) => setHist(hist.map((x, idx) => idx === i ? { ...x, addr1: v } : x))} /></Field>
                    <Field label="City"><TextInput value={a.city} onChange={(v) => setHist(hist.map((x, idx) => idx === i ? { ...x, city: v } : x))} /></Field>
                    <Field label="State"><SelectInput value={a.state} onChange={(v) => setHist(hist.map((x, idx) => idx === i ? { ...x, state: v } : x))} options={US_STATES} mono /></Field>
                    <Field label="From"><TextInput type="month" value={a.from} onChange={(v) => setHist(hist.map((x, idx) => idx === i ? { ...x, from: v } : x))} mono /></Field>
                    <Field label="To"><TextInput type="month" value={a.to} onChange={(v) => setHist(hist.map((x, idx) => idx === i ? { ...x, to: v } : x))} mono /></Field>
                  </div>
                </div>
              ))}
              {hist.length === 0 && <p className="text-sm text-muted-foreground">No previous addresses added yet.</p>}
            </div>
          </div>
        )}
        <div>
          <p className={`${sectionTitle} mb-2`}>Emergency contact <span className="font-normal normal-case">· optional</span></p>
          <div className={grid}>
            <Field label="Full name">
              <TextInput value={(p.emergency || {}).name} onChange={(v) => set('personal.emergency.name', v)} />
            </Field>
            <Field label="Phone">
              <TextInput type="tel" value={(p.emergency || {}).phone} onChange={(v) => set('personal.emergency.phone', v)} format="phone" />
            </Field>
            <Field label="Relationship" className={full}>
              <TextInput value={(p.emergency || {}).relationship} onChange={(v) => set('personal.emergency.relationship', v)}
                placeholder="Spouse, parent, sibling…" />
            </Field>
          </div>
        </div>
      </div>
    );
  },
};

// ── Step 3 · CDL & Documents ────────────────────────────────────────
const Step3: StepDef = {
  title: 'CDL & Documents', sub: 'License + uploads',
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
  Render: ({ data, set, errors }) => {
    const c = data.cdl || {};
    const end = c.endorsements || {};
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
              onChange={(v) => set('cdl.docs.cdlFront', v)} error={errors['cdl.docs.cdlFront']} />
            <DocUpload label="CDL — Back" sub="Barcode side" required value={(c.docs || {}).cdlBack}
              onChange={(v) => set('cdl.docs.cdlBack', v)} error={errors['cdl.docs.cdlBack']} />
            <DocUpload label="DOT Medical Card" sub="Form MCSA-5876" required value={(c.docs || {}).medical}
              onChange={(v) => set('cdl.docs.medical', v)} error={errors['cdl.docs.medical']} />
          </div>
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
  title: 'Driving Experience', sub: 'Equipment & history',
  validate: (d) => {
    const e: Errors = {};
    const x = d.experience || {};
    if (V.required(x.yearsCdl)) e['experience.yearsCdl'] = 'Required';
    if (V.required(x.totalMiles)) e['experience.totalMiles'] = 'Required';
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
          <Field label="Total career miles" required error={errors['experience.totalMiles']}>
            <SelectInput value={x.totalMiles} onChange={(v) => set('experience.totalMiles', v)} options={TOTAL_MILES} error={!!errors['experience.totalMiles']} />
          </Field>
          <Field label="Longest single run" className={full}>
            <TextInput value={x.longestRun} onChange={(v) => set('experience.longestRun', v)} mono />
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

// ── Step 5 · Employment History ─────────────────────────────────────
const Step5: StepDef = {
  title: 'Employment History', sub: 'Last 10 years · FMCSA',
  validate: (d) => {
    const e: Errors = {};
    const jobs: Data[] = d.employment || [];
    if (jobs.length === 0) { e['employment._'] = 'Add at least one position'; return e; }
    jobs.forEach((j, i) => {
      if (V.required(j.company)) e[`employment.${i}.company`] = 'Required';
      if (V.required(j.from)) e[`employment.${i}.from`] = 'Required';
      if (!j.current && V.required(j.to)) e[`employment.${i}.to`] = 'Required';
      if (!j.fmcsa) e[`employment.${i}.fmcsa`] = 'Required';
    });
    return e;
  },
  Render: ({ data, set, errors }) => {
    const jobs: Data[] = data.employment || [];
    const setJobs = (next: Data[]) => set('employment', next);
    const upd = (i: number, key: string, val: unknown) => setJobs(jobs.map((j, idx) => idx === i ? { ...j, [key]: val } : j));
    return (
      <div className="flex flex-col gap-4">
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
              <Field label="Company / motor carrier" required error={errors[`employment.${i}.company`]}>
                <TextInput value={j.company} onChange={(v) => upd(i, 'company', v)} error={!!errors[`employment.${i}.company`]} />
              </Field>
              <Field label="Position / title"><TextInput value={j.position} onChange={(v) => upd(i, 'position', v)} /></Field>
              <Field label="City"><TextInput value={j.city} onChange={(v) => upd(i, 'city', v)} /></Field>
              <Field label="State"><SelectInput value={j.state} onChange={(v) => upd(i, 'state', v)} options={US_STATES} mono /></Field>
              <Field label="Phone"><TextInput type="tel" value={j.phone} onChange={(v) => upd(i, 'phone', v)} format="phone" mono /></Field>
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
            <div className="flex flex-col gap-4">
              {accidents.map((a, i) => (
                <div key={i} className="rounded border border-border/60 p-3">
                  <div className="mb-2 flex justify-end"><button type="button" onClick={() => setAcc(accidents.filter((_, idx) => idx !== i))} className="text-muted-foreground hover:text-destructive"><Trash2 size={14} /></button></div>
                  <div className={grid}>
                    <Field label="Date"><TextInput type="date" value={a.date} onChange={(v) => updAcc(i, 'date', v)} mono /></Field>
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
            <div className="flex flex-col gap-4">
              {violations.map((a, i) => (
                <div key={i} className="rounded border border-border/60 p-3">
                  <div className="mb-2 flex justify-end"><button type="button" onClick={() => setVio(violations.filter((_, idx) => idx !== i))} className="text-muted-foreground hover:text-destructive"><Trash2 size={14} /></button></div>
                  <div className={grid}>
                    <Field label="Date"><TextInput type="date" value={a.date} onChange={(v) => updVio(i, 'date', v)} mono /></Field>
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
          <Field label="Please describe" hint="dates, jurisdictions, reason, and resolution">
            <TextArea value={inc.suspensionsDesc} onChange={(v) => set('incidents.suspensionsDesc', v)} rows={4} />
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

// ── Step 8 · Consents & Signature ───────────────────────────────────
const CONSENTS = [
  { key: 'psp', label: 'FMCSA Pre-Employment Screening Program (PSP)', desc: 'Authorize retrieval of crash and inspection history from the FMCSA Motor Carrier Management Information System (MCMIS).' },
  { key: 'mvr', label: 'Motor Vehicle Record (MVR)', desc: 'Authorize the carrier to obtain my driving record from each state where I have been licensed in the past 3 years.' },
  { key: 'clearinghouse', label: 'FMCSA Drug & Alcohol Clearinghouse', desc: 'Authorize a full query for drug & alcohol program violations under 49 CFR §382.701.' },
  { key: 'fcra', label: 'Consumer report (FCRA disclosure)', desc: 'I have read the separate FCRA disclosure and authorize a background check, including criminal history.' },
  { key: 'drug', label: 'Pre-employment drug screen', desc: 'I agree to submit to a DOT-regulated drug test prior to operating a commercial motor vehicle.' },
  { key: 'truthful', label: 'Truthful & complete statements', desc: 'I certify that all statements in this application are true and complete. False or omitted information is grounds for rejection or termination.' },
];

const Step8: StepDef = {
  title: 'Consents & Signature', sub: 'Authorizations',
  validate: (d) => {
    const e: Errors = {};
    const c = d.consents || {};
    for (const { key } of CONSENTS) if (!c[key]) e[`consents.${key}`] = 'Required';
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
  Render: ({ data, set, errors }) => {
    const c = data.consents || {};
    const fullName = `${(data.personal || {}).first || ''} ${(data.personal || {}).last || ''}`.trim();
    const anyConsentErr = CONSENTS.some(({ key }) => errors[`consents.${key}`]);
    return (
      <div className="flex flex-col gap-5">
        <div className="flex items-start gap-2 rounded-md border border-info-bd bg-info-bg p-3 text-sm text-info">
          <ShieldCheck size={18} className="mt-0.5 shrink-0" />
          <p>Federal law requires your authorization before we can run these checks. Review each item and check the box to consent. Your data is encrypted and used only for this hiring decision.</p>
        </div>
        <div className="flex flex-col gap-3">
          {CONSENTS.map(({ key, label, desc }) => (
            <Check_ key={key} checked={!!c[key]} onChange={(b) => set(`consents.${key}`, b)}>
              <span className="font-medium">{label}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">{desc}</span>
            </Check_>
          ))}
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

export const STEPS: StepDef[] = [Step1, Step2, Step3, Step4, Step5, Step6, Step7, Step8];
