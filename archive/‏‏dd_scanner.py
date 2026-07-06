"""
German Retail Underwriter — DD Lease Scanner
=============================================
Scans PDF lease agreements and compares extracted commercial terms
against the seller's rent roll (Excel). Flags discrepancies for DD.

What it checks per tenant:
  - Rent per m² (tolerance ±5%)
  - Lease area (tolerance ±2%)
  - Lease expiry date (exact match)
  - Break option date (exact match)
  - CPI indexation clause (Y/N)
  - Security deposit (tolerance ±10%)

Output: discrepancy report per tenant with severity (RED/AMBER/OK).

Limitations (honest):
  - Text PDF only (scanned images need OCR — not implemented)
  - German and English lease formats only
  - Rent must appear in EUR/m²/month or annual EUR format
  - Works best on standard GdW/ZIA lease templates
"""
import re
import io
from datetime import datetime, date
from typing import Optional
import pdfplumber


# ─── EXTRACTION HELPERS ───────────────────────────────────────────────────────

def _extract_float(s: str) -> float:
    if not s: return 0.0
    s = re.sub(r'[€$£\s]', '', str(s))
    # Handle German thousands: 1.234,56
    if re.match(r'^\d{1,3}(\.\d{3})+(,\d+)?$', s):
        s = s.replace('.', '').replace(',', '.')
    elif re.match(r'^\d{1,3}(,\d{3})+(\.\d+)?$', s):
        s = s.replace(',', '')
    elif re.match(r'^\d+,\d{3}$', s):
        s = s.replace(',', '')
    else:
        s = s.replace(',', '.')
    m = re.search(r'[\d]+\.?[\d]*', s)
    try: return float(m.group()) if m else 0.0
    except: return 0.0


def _parse_date(s: str) -> Optional[date]:
    if not s or str(s).strip() in ['', 'nan', 'None', '—']: return None
    s = str(s).strip()
    for fmt in ['%d.%m.%Y', '%d/%m/%Y', '%Y-%m-%d', '%d.%m.%y', '%B %d, %Y']:
        try: return datetime.strptime(s, fmt).date()
        except: continue
    try: return None
    except: return None


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract all text from PDF using pdfplumber."""
    try:
        text_pages = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t: text_pages.append(t)
        return '\n'.join(text_pages)
    except Exception as e:
        return f'ERROR: {e}'


# ─── LEASE TERM EXTRACTION ────────────────────────────────────────────────────

def extract_lease_terms(pdf_text: str, filename: str = '') -> dict:
    """
    Extract commercial terms from lease agreement text.
    Returns dict of extracted fields with confidence scores.
    """
    text = pdf_text
    result = {
        'filename':       filename,
        'tenant_name':    None,
        'area_sqm':       None,
        'rent_sqm_mo':    None,
        'annual_rent':    None,
        'lease_start':    None,
        'lease_expiry':   None,
        'break_option':   None,
        'cpi_clause':     None,
        'cpi_passthrough':None,
        'security_dep':   None,
        'confidence':     {},
        'raw_extracts':   {},
    }

    # ── Tenant name ──────────────────────────────────────────────────────────
    for pattern in [
        r'"Mieter"\)\s*\n*([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s&\.\-,]+(?:GmbH|AG|KG|SE|Ltd|mbH)[\s&\.\-,]*(?:Co\.|KG)?)',
        r'(?:und|and)\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s&\.\-]+(?:GmbH|AG|KG|SE|mbH))',
        r'(?:Mieter|Tenant|Mieterin|Lessee)[:\s]+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s&\.\-,]+(?:GmbH|AG|KG|SE|Ltd|Inc|mbH)?)',
    ]:
        m = re.search(pattern, text, re.I)
        if m:
            name = m.group(1).strip().rstrip(',.')
            if 3 < len(name) < 80:
                result['tenant_name'] = name
                result['confidence']['tenant_name'] = 'HIGH'
                result['raw_extracts']['tenant_name'] = m.group(0)[:100]
                break

    # ── Lease area ───────────────────────────────────────────────────────────
    for pattern in [
        r'(?:Mietfläche|Nutzfläche|Mietgegenstand)[^\d]*([\d][,\.\d]+)\s*(?:m²|qm|Quadratmeter)',
        r'(?:area|floor\s*space|leased\s*area)[^\d]*([\d][,\.\d]+)\s*(?:m²|sqm|sq\.?m)',
        r'([\d][,\.\d]+)\s*(?:m²|qm)\s*(?:Nutz|Miet|Gesamt)',
    ]:
        m = re.search(pattern, text, re.I)
        if m:
            val = _extract_float(m.group(1))
            if 50 < val < 50000:
                result['area_sqm'] = val
                result['confidence']['area_sqm'] = 'HIGH'
                result['raw_extracts']['area_sqm'] = m.group(0)[:80]
                break

    # ── Rent ─────────────────────────────────────────────────────────────────
    # Try monthly per m² first
    for pattern in [
        r'(?:Kaltmiete|Nettomiete|Mietzins|Grundmiete)[^\d€]*([\d][,\.\d]+)\s*(?:EUR|€)?\s*/\s*(?:m²|qm)\s*/?\s*(?:Monat|Monat|month|mtl)',
        r'([\d][,\.\d]+)\s*(?:EUR|€)\s*/\s*(?:m²|qm)\s*/\s*(?:Monat|month)',
        r'(?:rent|miete)[^\d€]*([\d][,\.\d]+)\s*(?:EUR|€)\s*per\s*(?:m²|sqm)',
    ]:
        m = re.search(pattern, text, re.I)
        if m:
            val = _extract_float(m.group(1))
            if 2 < val < 200:  # sanity: EUR/m²/month
                result['rent_sqm_mo'] = val
                result['confidence']['rent_sqm_mo'] = 'HIGH'
                result['raw_extracts']['rent_sqm_mo'] = m.group(0)[:100]
                break

    # Annual rent
    for pattern in [
        r'(?:Jahresmiete|Jahres(?:netto)?miete|annual rent)[^\d€]*([\d][,\.\d]+)\s*(?:EUR|€)',
        r'(?:p\.?a\.?|per\s*(?:year|annum|Jahr))[^\d]*([\d][,\.\d]+)\s*(?:EUR|€)',
    ]:
        m = re.search(pattern, text, re.I)
        if m:
            val = _extract_float(m.group(1))
            if 1000 < val < 50_000_000:
                result['annual_rent'] = val
                result['confidence']['annual_rent'] = 'HIGH' if result['rent_sqm_mo'] is None else 'MEDIUM'
                result['raw_extracts']['annual_rent'] = m.group(0)[:100]
                break

    # Back-calculate rent/m² from annual if needed
    if result['rent_sqm_mo'] is None and result['annual_rent'] and result['area_sqm']:
        calc = result['annual_rent'] / (result['area_sqm'] * 12)
        if 2 < calc < 200:
            result['rent_sqm_mo'] = round(calc, 2)
            result['confidence']['rent_sqm_mo'] = 'CALCULATED'

    # ── Dates ────────────────────────────────────────────────────────────────
    for field, patterns in {
        'lease_start': [
            r'(?:Mietbeginn|Mietzeit\s+beginnt|commence[sd]?|ab\s+dem)[^\d]*([\d]{1,2}[\.\-/][\d]{1,2}[\.\-/][\d]{2,4})',
            r'(?:start|begin)[^\d]*([\d]{1,2}[\.\-/][\d]{1,2}[\.\-/][\d]{4})',
        ],
        'lease_expiry': [
            r'Ende\s+(\d{1,2}\.\d{1,2}\.\d{4})',
            r'(?:Mietende|endet\s+am|expires?|läuft\s+(?:ab|bis))[^\d]*(\d{1,2}[\.\/]\d{1,2}[\.\/]\d{2,4})',
            r'(?:until|bis\s+(?:zum)?|end\s+date)[^\d]*(\d{1,2}[\.\/]\d{1,2}[\.\/]\d{4})',
        ],
        'break_option': [
            r'zum\s+(\d{1,2}\.\d{1,2}\.\d{4})\s+zu\s+k',
            r'(?:Sonderk.ndigungsrecht|SKR|break\s*option)[^\d]*(\d{1,2}[\./ ]\d{1,2}[\./ ]\d{2,4})',
            r'(?:k.ndigen|terminate)[^\d]*(\d{1,2}\.\d{1,2}\.\d{4})',
        ],
    }.items():
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                d = _parse_date(m.group(1))
                if d and 2000 <= d.year <= 2060:
                    result[field] = d
                    result['confidence'][field] = 'HIGH'
                    result['raw_extracts'][field] = m.group(0)[:100]
                    break

    # ── CPI / Indexation ─────────────────────────────────────────────────────
    cpi_patterns = [
        r'(?:Wertsicherung|Indexierung|Indexklausel|CPI|Verbraucherpreisindex|inflation)',
        r'(?:index\s*clause|rent\s*review|Mietanpassung)',
    ]
    for pat in cpi_patterns:
        if re.search(pat, text, re.I):
            result['cpi_clause'] = 'Y'
            result['confidence']['cpi_clause'] = 'HIGH'
            # Try to extract passthrough %
            m_pt = re.search(r'(?:zu|wird|to)\s*(\d{2,3})\s*%\s*(?:angepasst|angep|adjusted|Anpassung)|'                             r'(\d{2,3})\s*%\s*(?:der|of|des)\s*(?:Veränderung|change|index)', text, re.I)
            if m_pt:
                _pt_val = m_pt.group(1) or m_pt.group(2)
                if _pt_val:
                    result['cpi_passthrough'] = int(_pt_val) / 100
            break
    if result['cpi_clause'] is None:
        result['cpi_clause'] = 'N'
        result['confidence']['cpi_clause'] = 'MEDIUM'

    # ── Security deposit ─────────────────────────────────────────────────────
    for pat in [
        r'(?:Höhe von)\s*(?:EUR|€)?\s*([\d][,\.\d]+)',
        r'(?:Kaution|Sicherheitsleistung|security\s*deposit)[^\d€]*([\d][,\.\d]+)\s*(?:EUR|€)',
        r'(?:Monatsmieten?|months?\s*rent)[^\d]*(\d)\s*(?:Monats|months?)',
    ]:
        m = re.search(pat, text, re.I)
        if m:
            val = _extract_float(m.group(1))
            if 0 < val < 2_000_000:
                result['security_dep'] = val
                result['confidence']['security_dep'] = 'HIGH'
                result['raw_extracts']['security_dep'] = m.group(0)[:100]
                break

    return result


# ─── COMPARISON ENGINE ────────────────────────────────────────────────────────

def compare_lease_to_rentroll(extracted: dict, rentroll_row: dict,
                               tenant_name_hint: str = '') -> list:
    """
    Compare extracted PDF lease terms against one rent roll row.
    Returns list of discrepancy dicts with severity.
    """
    discrepancies = []

    def _disc(field, pdf_val, rr_val, tolerance_pct, severity_fn):
        if pdf_val is None or rr_val is None or rr_val == 0:
            if pdf_val is None:
                discrepancies.append({
                    'field':    field,
                    'severity': 'AMBER',
                    'pdf_val':  'Not found in PDF',
                    'rr_val':   rr_val,
                    'message':  f'{field} could not be extracted from lease — verify manually.',
                })
            return
        diff_pct = abs(pdf_val - rr_val) / rr_val if rr_val else 0
        severity = severity_fn(diff_pct, tolerance_pct)
        if severity != 'OK':
            discrepancies.append({
                'field':    field,
                'severity': severity,
                'pdf_val':  pdf_val,
                'rr_val':   rr_val,
                'diff_pct': diff_pct,
                'message':  (f'{field}: PDF shows {pdf_val}, rent roll shows {rr_val} '
                             f'(diff: {diff_pct:.1%}) — {"CRITICAL" if severity=="RED" else "verify"}.'),
            })

    def _num_severity(diff_pct, tol):
        if diff_pct <= tol:         return 'OK'
        elif diff_pct <= tol * 3:   return 'AMBER'
        else:                       return 'RED'

    # Rent per m²
    _disc('Rent (EUR/m²/mo)', extracted.get('rent_sqm_mo'),
          rentroll_row.get('rent_sqm_mo'), 0.05, _num_severity)

    # Area
    _disc('Area (m²)', extracted.get('area_sqm'),
          rentroll_row.get('area_sqm'), 0.02, _num_severity)

    # Security deposit
    if rentroll_row.get('security_dep', 0) > 0:
        _disc('Security Deposit (EUR)', extracted.get('security_dep'),
              rentroll_row.get('security_dep'), 0.10, _num_severity)

    # Lease expiry — exact match expected
    pdf_exp = extracted.get('lease_expiry')
    rr_exp  = rentroll_row.get('lease_expiry')
    if pdf_exp and rr_exp:
        if isinstance(rr_exp, str): rr_exp = _parse_date(rr_exp)
        if pdf_exp != rr_exp:
            discrepancies.append({
                'field':    'Lease Expiry',
                'severity': 'RED',
                'pdf_val':  str(pdf_exp),
                'rr_val':   str(rr_exp),
                'message':  f'CRITICAL: Lease expiry mismatch — PDF: {pdf_exp}, rent roll: {rr_exp}.',
            })

    # Break option — exact match
    pdf_brk = extracted.get('break_option')
    rr_brk  = rentroll_row.get('break_option')
    if pdf_brk and not rr_brk:
        discrepancies.append({
            'field':    'Break Option',
            'severity': 'RED',
            'pdf_val':  str(pdf_brk),
            'rr_val':   'None in rent roll',
            'message':  f'CRITICAL: Break option {pdf_brk} in lease but NOT in rent roll — risk not captured.',
        })
    elif rr_brk and not pdf_brk:
        discrepancies.append({
            'field':    'Break Option',
            'severity': 'AMBER',
            'pdf_val':  'Not found in PDF',
            'rr_val':   str(rr_brk),
            'message':  f'Break option in rent roll ({rr_brk}) not found in lease PDF — verify lease wording.',
        })

    # CPI clause
    pdf_cpi = extracted.get('cpi_clause')
    rr_cpi  = rentroll_row.get('cpi_clause')
    if pdf_cpi and rr_cpi and pdf_cpi != rr_cpi:
        discrepancies.append({
            'field':    'CPI Clause',
            'severity': 'AMBER',
            'pdf_val':  pdf_cpi,
            'rr_val':   rr_cpi,
            'message':  f'CPI indexation mismatch: PDF={pdf_cpi}, rent roll={rr_cpi} — check lease wording.',
        })

    # If no discrepancies found
    if not discrepancies:
        discrepancies.append({
            'field':    'OVERALL',
            'severity': 'OK',
            'pdf_val':  None,
            'rr_val':   None,
            'message':  'All extracted fields match rent roll within tolerance.',
        })

    return discrepancies


# ─── REPORT GENERATOR ─────────────────────────────────────────────────────────

def run_dd_scan(pdf_files: dict, rentroll_df) -> dict:
    """
    Run full DD scan: extract from multiple PDFs, compare to rent roll.
    
    Args:
        pdf_files: dict of {filename: bytes}
        rentroll_df: DataFrame from rrp.load_rent_roll()
    
    Returns: scan_results dict
    """
    results = {}
    summary = {'RED': 0, 'AMBER': 0, 'OK': 0, 'UNMATCHED': 0}

    for filename, pdf_bytes in pdf_files.items():
        text = _extract_text_from_pdf(pdf_bytes)
        if text.startswith('ERROR'):
            results[filename] = {
                'status':      'ERROR',
                'error':       text,
                'extracted':   {},
                'discrepancies': [],
                'matched_tenant': None,
            }
            continue

        extracted = extract_lease_terms(text, filename)

        # Match to rent roll tenant
        matched_row = None
        matched_name = None
        rr_active = rentroll_df[~rentroll_df['is_vacant']] if 'is_vacant' in rentroll_df.columns else rentroll_df

        # Try tenant name match
        if extracted.get('tenant_name'):
            pdf_name_lower = extracted['tenant_name'].lower()
            for _, row in rr_active.iterrows():
                rr_name = str(row.get('tenant', '')).lower()
                # Check if either contains the other
                if (pdf_name_lower in rr_name or rr_name in pdf_name_lower or
                        any(word in rr_name for word in pdf_name_lower.split() if len(word) > 3)):
                    matched_row = row.to_dict()
                    matched_name = row.get('tenant', '')
                    break

        # Try area match as fallback
        if matched_row is None and extracted.get('area_sqm'):
            for _, row in rr_active.iterrows():
                rr_area = float(row.get('area_sqm', 0) or 0)
                if rr_area > 0 and abs(extracted['area_sqm'] - rr_area) / rr_area < 0.05:
                    matched_row = row.to_dict()
                    matched_name = row.get('tenant', '')
                    break

        if matched_row is None:
            results[filename] = {
                'status':         'UNMATCHED',
                'extracted':      extracted,
                'discrepancies':  [],
                'matched_tenant': None,
                'message':        'Could not match PDF to any rent roll tenant. Verify manually.',
            }
            summary['UNMATCHED'] += 1
            continue

        discs = compare_lease_to_rentroll(extracted, matched_row, matched_name)

        # Overall status
        statuses = [d['severity'] for d in discs]
        if 'RED'   in statuses: status = 'RED'
        elif 'AMBER' in statuses: status = 'AMBER'
        else: status = 'OK'
        summary[status] = summary.get(status, 0) + 1

        results[filename] = {
            'status':         status,
            'extracted':      extracted,
            'discrepancies':  discs,
            'matched_tenant': matched_name,
        }

    return {'results': results, 'summary': summary}


if __name__ == '__main__':
    print("dd_scanner.py — DD Lease Scanner module")
    print("Functions: extract_lease_terms(), compare_lease_to_rentroll(), run_dd_scan()")
