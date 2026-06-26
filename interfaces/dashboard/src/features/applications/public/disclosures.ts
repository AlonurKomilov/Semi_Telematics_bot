// FIXED legal disclosure templates for the apply-form consent step.
//
// The LANGUAGE here is fixed (exact FMCSA / FCRA wording); only the carrier
// BLANKS substitute in (name / DOT / MC / address / CRA).  The PSP notice
// mandates its language be used "in whole, exactly as provided… as one
// stand-alone document" — so PSP is rendered isolated and never edited.
// Recruiters fill the blanks (in the brand panel); they cannot edit the text.

export interface CarrierLegal {
  name: string;
  dot: string; mc: string; phone: string;
  legal_address: string; compliance_email: string;
  cra_name: string; cra_address: string; cra_phone: string; cra_site: string;
}

export type Block =
  | { kind: 'h'; text: string }
  | { kind: 'p'; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'kv'; rows: [string, string][] }
  | { kind: 'note'; text: string };

export interface Disclosure {
  id: 'psp' | 'fcra' | 'employment_verification';
  title: string;
  standalone?: boolean;   // PSP must stand alone (FMCSA)
  blocks: Block[];
}

// ── PSP (49 CFR — FMCSA Pre-Employment Screening Program) ───────────
export function pspDisclosure(c: CarrierLegal): Disclosure {
  const C = c.name || 'the Prospective Employer';
  return {
    id: 'psp',
    standalone: true,
    title: 'Important Disclosure Regarding Background Reports from the PSP Online Service',
    blocks: [
      { kind: 'p', text: `In connection with your application for employment with ${C} ("Prospective Employer"), Prospective Employer, its employees, agents or contractors may obtain one or more reports regarding your driving, and safety inspection history from the Federal Motor Carrier Safety Administration (FMCSA).` },
      { kind: 'p', text: 'When the application for employment is submitted in person, if the Prospective Employer uses any information it obtains from FMCSA in a decision to not hire you or to make any other adverse employment decision regarding you, the Prospective Employer will provide you with a copy of the report upon which its decision was based and a written summary of your rights under the Fair Credit Reporting Act before taking any final adverse action. If any final adverse action is taken against you based upon your driving history or safety report, the Prospective Employer will notify you that the action has been taken and that the action was based in part or in whole on this report.' },
      { kind: 'p', text: 'When the application for employment is submitted by mail, telephone, computer, or other similar means, if the Prospective Employer uses any information it obtains from FMCSA in a decision to not hire you or to make any other adverse employment decision regarding you, the Prospective Employer must provide you within three business days of taking adverse action oral, written or electronic notification: that adverse action has been taken based in whole or in part on information obtained from FMCSA; the name, address, and the toll free telephone number of FMCSA; that the FMCSA did not make the decision to take the adverse action and is unable to provide you the specific reasons why the adverse action was taken; and that you may, upon providing proper identification, request a free copy of the report and may dispute with the FMCSA the accuracy or completeness of any information or report. If you request a copy of a driver record from the Prospective Employer who procured the report, then, within 3 business days of receiving your request, together with proper identification, the Prospective Employer must send or provide to you a copy of your report and a summary of your rights under the Fair Credit Reporting Act.' },
      { kind: 'p', text: 'Neither the Prospective Employer nor the FMCSA contractor supplying the crash and safety information has the capability to correct any safety data that appears to be incorrect. You may challenge the accuracy of the data by submitting a request to https://dataqs.fmcsa.dot.gov. If you challenge crash or inspection information reported by a State, FMCSA cannot change or correct this data. Your request will be forwarded by the DataQs system to the appropriate State for adjudication.' },
      { kind: 'p', text: 'Any crash or inspection in which you were involved will display on your PSP report. Since the PSP report does not report, or assign, or imply fault, it will include all Commercial Motor Vehicle (CMV) crashes where you were a driver or co-driver and where those crashes were reported to FMCSA, regardless of fault. Similarly, all inspections, with or without violations, appear on the PSP report. State citations associated with Federal Motor Carrier Safety Regulations (FMCSR) violations that have been adjudicated by a court of law will also appear, and remain, on a PSP report.' },
      { kind: 'p', text: 'The Prospective Employer cannot obtain background reports from FMCSA without your authorization.' },
      { kind: 'h', text: 'Authorization' },
      { kind: 'p', text: 'If you agree that the Prospective Employer may obtain such background reports, please read the following and check the box below:' },
      { kind: 'p', text: `I authorize ${C} ("Prospective Employer") to access the FMCSA Pre-Employment Screening Program (PSP) system to seek information regarding my commercial driving safety record and information regarding my safety inspection history. I understand that I am authorizing the release of safety performance information including crash data from the previous five (5) years and inspection history from the previous three (3) years. I understand and acknowledge that this release of information may assist the Prospective Employer to make a determination regarding my suitability as an employee.` },
      { kind: 'p', text: 'I further understand that neither the Prospective Employer nor the FMCSA contractor supplying the crash and safety information has the capability to correct any safety data that appears to be incorrect. I understand I may challenge the accuracy of the data by submitting a request to https://dataqs.fmcsa.dot.gov. If I challenge crash or inspection information reported by a State, FMCSA cannot change or correct this data. I understand my request will be forwarded by the DataQs system to the appropriate State for adjudication.' },
      { kind: 'p', text: 'I understand that any crash or inspection in which I was involved will display on my PSP report. Since the PSP report does not report, or assign, or imply fault, I acknowledge it will include all CMV crashes where I was a driver or co-driver and where those crashes were reported to FMCSA, regardless of fault. Similarly, I understand all inspections, with or without violations, will appear on my PSP report, and State citations associated with FMCSR violations that have been adjudicated by a court of law will also appear, and remain, on my PSP report.' },
      { kind: 'p', text: 'I have read the above Disclosure Regarding Background Reports provided to me by Prospective Employer and I understand that if I authorize this Disclosure and Authorization, Prospective Employer may obtain a report of my crash and inspection history. I hereby authorize Prospective Employer and its employees, authorized agents, and/or affiliates to obtain the information authorized above.' },
      { kind: 'note', text: 'NOTICE: This form is made available to monthly account holders by NIC on behalf of the U.S. Department of Transportation, Federal Motor Carrier Safety Administration (FMCSA). Account holders are required by federal law to obtain an Applicant’s written or electronic consent prior to accessing the Applicant’s PSP report. Further, account holders are required by FMCSA to use the language contained in this Disclosure and Authorization form to obtain an Applicant’s consent. The language must be used in whole, exactly as provided. Further, the language on this form must exist as one stand-alone document. The language may NOT be included with other consent forms or any other language. NOTICE: The prospective employment concept referenced in this form contemplates the definition of "employee" contained at 49 C.F.R. 383.5. LAST UPDATED 2/11/2016' },
    ],
  };
}

// ── FCRA / MVR consumer report + Summary of Rights ──────────────────
export function fcraDisclosure(c: CarrierLegal): Disclosure {
  const C = c.name || 'the Company';
  const craLine = c.cra_name
    ? `The consumer and/or investigative consumer report(s) will be obtained from: ${c.cra_name}${c.cra_address ? ', ' + c.cra_address : ''}${c.cra_phone ? ' ' + c.cra_phone : ''}.${c.cra_site ? ` ${c.cra_name} privacy policy can be found at ${c.cra_site}.` : ''}`
    : 'The consumer and/or investigative consumer report(s) will be obtained from a third-party consumer reporting agency.';
  return {
    id: 'fcra',
    title: 'Disclosure Regarding Consumer and/or Investigative Background Reports',
    blocks: [
      { kind: 'p', text: `The Employer, ${C} ("Company") may obtain information about you for employment purposes from a third party consumer reporting agency. Thus, you may be the subject of a "consumer report" and/or an "investigative consumer report" which may include information about your character, general reputation, personal characteristics, and/or mode of living and which can involve personal interviews with sources such as your neighbors, friends, supervisors, or associates. These reports may contain information regarding your credit history, criminal history, social security verification, motor vehicle records ("driving records"), verification of your education or employment history, or other background checks. Credit history will only be requested where such information is substantially related to the duties and responsibilities of the position for which you are applying.` },
      { kind: 'p', text: 'You have the right, upon written request made within a reasonable time, to request whether a consumer report has been run about you, and disclosure of the nature and scope of any investigative consumer report and to request a copy of your report. Please be advised that the nature and scope of any investigative consumer report will be your employment history. The scope of this disclosure is all-encompassing, however, allowing the Company to obtain from any outside organization all manner of consumer reports throughout the course of your employment to the extent permitted by law.' },
      { kind: 'p', text: craLine },
      { kind: 'h', text: 'Acknowledgment and Authorization for Background Check' },
      { kind: 'p', text: 'I acknowledge receipt of the separate document entitled DISCLOSURE REGARDING BACKGROUND INVESTIGATION and A SUMMARY OF YOUR RIGHTS UNDER THE FAIR CREDIT REPORTING ACT and certify that I have read and understand both of those documents. I hereby authorize the obtaining of "consumer reports" and/or "investigative consumer reports" by the Employer at any time after receipt of this authorization and throughout my employment. To this end, I hereby authorize, without reservation, any law enforcement agency, administrator, state or federal agency, institution, school or university (public or private), information service bureau, employer, or insurance company to furnish any and all background information requested by the consumer reporting agency identified above and/or from Employer itself. I agree that a facsimile ("fax"), electronic or photographic copy of this Authorization shall be as valid as the original.' },
      { kind: 'p', text: 'New York applicants only: Upon request, you will be informed whether or not a consumer report was requested by the Company, and if such report was requested, informed of the name and address of the consumer reporting agency that furnished the report. You have the right to inspect and receive a copy of any investigative consumer report requested by the Company by contacting the consumer reporting agency identified above directly. By consenting below, you acknowledge receipt of Article 23-A of the New York Correction Law.' },
      { kind: 'p', text: 'Washington State applicants only: You also have the right to request from the consumer reporting agency a written summary of your rights and remedies under the Washington Fair Credit Reporting Act.' },
      { kind: 'p', text: 'Minnesota and Oklahoma applicants only: You may request a copy of a consumer report if one is obtained by the Company.' },
      { kind: 'p', text: 'California applicants only: Under California Civil Code section 1786.22, you are entitled to find out what is in the CRA’s file on you with proper identification.' },
      { kind: 'h', text: 'A Summary of Your Rights Under the Fair Credit Reporting Act' },
      { kind: 'p', text: 'Para información en español, visite www.consumerfinance.gov/learnmore o escribe a la Consumer Financial Protection Bureau, 1700 G Street NW, Washington, DC 20552.' },
      { kind: 'p', text: 'The federal Fair Credit Reporting Act (FCRA) promotes the accuracy, fairness, and privacy of information in the files of consumer reporting agencies. There are many types of consumer reporting agencies, including credit bureaus and specialty agencies (such as agencies that sell information about check writing histories, medical records, and rental history records). Here is a summary of your major rights under the FCRA.' },
      { kind: 'ul', items: [
        'You must be told if information in your file has been used against you. Anyone who uses a credit report or another type of consumer report to deny your application for credit, insurance, or employment — or to take another adverse action against you — must tell you, and must give you the name, address, and phone number of the agency that provided the information.',
        'You have the right to know what is in your file. You may request and obtain all the information about you in the files of a consumer reporting agency (your "file disclosure"). You will be required to provide proper identification, which may include your Social Security number.',
        'You have the right to ask for a credit score.',
        'You have the right to dispute incomplete or inaccurate information. If you identify information in your file that is incomplete or inaccurate, and report it to the consumer reporting agency, the agency must investigate unless your dispute is frivolous.',
        'Consumer reporting agencies must correct or delete inaccurate, incomplete, or unverifiable information, usually within 30 days.',
        'Consumer reporting agencies may not report outdated negative information. In most cases, a consumer reporting agency may not report negative information that is more than seven years old, or bankruptcies that are more than 10 years old.',
        'Access to your file is limited. A consumer reporting agency may provide information about you only to people with a valid need.',
        'You must give your consent for reports to be provided to employers.',
        'You may limit "prescreened" offers of credit and insurance you get based on information in your credit report.',
        'You may seek damages from violators.',
      ] },
      { kind: 'p', text: 'For more information, including information about additional rights, go to www.consumerfinance.gov/learnmore or write to: Consumer Financial Protection Bureau, 1700 G Street NW, Washington, DC 20552.' },
    ],
  };
}

// ── Employee Verification (49 CFR §391.23 prior-employer records) ───
export function employmentDisclosure(c: CarrierLegal): Disclosure {
  const C = c.name || 'the Company';
  // Only render rows that are actually filled — never a blank "Address: —".
  const rows = ([
    ['Company', c.name], ['DOT Number', c.dot], ['MC Number', c.mc],
    ['Address', c.legal_address], ['Phone', c.phone], ['Email', c.compliance_email],
  ] as [string, string][]).filter(([, v]) => v);
  return {
    id: 'employment_verification',
    title: 'Employee Verification Consent',
    blocks: [
      { kind: 'h', text: 'Prospective Employer Information' },
      { kind: 'kv', rows },
      { kind: 'h', text: 'Disclosure — Request / Consent for Information from Previous Employers / Carriers' },
      { kind: 'p', text: 'For Alcohol and Controlled Substances Testing Records, Safety Performance History, and Driving Records, and changes in Parts 390 and 391 of the FMCSA.' },
      { kind: 'p', text: `I hereby authorize my previous employers, contractors (if owner-operator), state agencies, and other applicable entities to release and forward to ${C} ("Company") the following information for the past three (3) years, or longer where required by law:` },
      { kind: 'h', text: 'DOT Alcohol and Controlled Substances Testing Information' },
      { kind: 'p', text: 'in accordance with Parts 382 and 40 of the Federal Motor Carrier Safety Regulations (49 CFR Part 382 and 49 CFR Part 40, Section 40.25), limited to the following DOT-regulated testing items, including pre-employment testing results:' },
      { kind: 'ul', items: [
        'Alcohol tests with a result of 0.04 or higher',
        'Verified positive drug tests',
        'Refusals to be tested',
        'Other violations of DOT agency drug and alcohol testing regulations',
        'Information obtained from previous employers regarding a drug and alcohol rule violation',
        'Documentation, if any, of completion of the return-to-duty process following a rule violation',
      ] },
      { kind: 'h', text: 'Safety Performance History Information' },
      { kind: 'p', text: 'in accordance with 49 CFR Part 391.23, including employment dates and work history (which may include position held, reason for leaving, termination information, whether the applicant was subject to Federal Motor Carrier Safety Administration regulations, equipment operated, geographic areas driven, and other applicable information), as well as accident information (including accident date, nature of accident, whether the accident was preventable, and whether injuries, fatalities, or hazardous materials were involved, along with copies of any accident reports).' },
      { kind: 'h', text: 'Driving Record Information' },
      { kind: 'p', text: 'including but not limited to motor vehicle records (MVRs), traffic violations, license suspensions, revocations, disqualifications, accident involvement, and any other driving-related history maintained by state motor vehicle agencies or previous employers, as permitted under applicable federal and state laws and regulations.' },
      { kind: 'h', text: 'Authorization and Rights Under 49 C.F.R. Part 391.23' },
      { kind: 'p', text: 'Pursuant to Section 391.23(i) of the Federal Motor Carrier Safety Regulations, you have the following rights:' },
      { kind: 'ul', items: [
        'You have the right to make a written request at any time to review the information provided.',
        'You have the right to have errors corrected and re-sent by the previous employer, contractor, or reporting agency.',
        'You have the right to have a rebuttal statement attached to any alleged erroneous information.',
      ] },
      { kind: 'p', text: `I have read and understand the above disclosure and authorization. I hereby authorize the release of my alcohol and controlled substances testing records, safety performance history, and driving record information to ${C}.` },
      { kind: 'h', text: 'Applicant Acknowledgment' },
      { kind: 'p', text: `I acknowledge that any false, misleading, or incomplete information provided may result in disqualification from employment consideration or termination of employment if hired. I hereby authorize ${C} to review and verify the information contained in this application as permitted by applicable law, and I submit this application voluntarily.` },
    ],
  };
}

// All three disclosures, filled for a carrier.  PSP first (standalone).
export function buildDisclosures(c: CarrierLegal): Disclosure[] {
  return [pspDisclosure(c), fcraDisclosure(c), employmentDisclosure(c)];
}
