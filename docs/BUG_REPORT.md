# vSNP GUI Bug Report

**Date:** February 3, 2026
**Status:** All critical issues resolved

---

## Resolved Issues

| Issue | Status | Resolution |
|-------|--------|------------|
| Preset paths hardcoded | ✅ Fixed | Now uses `settings.vsnp3_path` and `config.gui_root` |
| `.join("\\n")` bug | ✅ Fixed | Now uses `.join("\n")` |
| Citation incorrect | ✅ Fixed | Updated to Hicks, Stuber, Lantz, Torchetti, Robbe-Austerman |
| Large import warning | ✅ Fixed | Backend returns 400 if >500 VCFs, frontend confirms |
| Silent exception in ref detection | ✅ Fixed | Now logs: `[vcf-ref] Failed to read {path}: {error}` |
| Settings validation | ✅ Fixed | `settingsReady` guard prevents ops before config |

---

## Open Enhancements (Low Priority)

| Enhancement | Description |
|-------------|-------------|
| Import progress indicator | Large VCF imports run synchronously |
| Reference detection caching | Could cache per-directory for large sets |

---

## Verified Working

- Core two-step workflow
- Custom VCF set builder with presets
- Reference auto-detection from VCF headers
- Fuzzy reference matching
- Deduplication (keep newest by mtime)
- Path picker integration
- Settings validation with banner
- Large import confirmation (>500 VCFs)
- Mismatch report generation
- QC summary and exclusions
- Real-time job logging
