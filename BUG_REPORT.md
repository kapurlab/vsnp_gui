# vSNP GUI Bug Report

**Date:** February 3, 2026

---

## Open Issues

| Issue | Location | Severity | Description |
|-------|----------|----------|-------------|
| No import progress indicator | `importVcfs()` in App.jsx | Low | Large VCF imports run synchronously with no feedback |
| Silent exception handling | `_detect_vcf_reference()` in main.py | Low | Returns empty string on error without logging |
| No import size limit | `/import-vcfs` endpoint | Low | Could import thousands of files without warning |

---

## Recommendations

1. **Import progress** - Convert import-vcfs to async job with progress reporting
2. **Reference caching** - Cache detected references per directory for large sets
3. **Import limit** - Warn if importing >500 VCFs, require confirmation for >1000

---

## Verified Working

- Citation (correct authors)
- Preset paths (dynamic from settings)
- Newline joins in presets
- Reference detection from VCF headers
- Fuzzy reference matching
- Deduplication logic
- Custom VCF set workflow
